#!/usr/bin/env python3
# thoth-management-api
# Copyright(C) 2018, 2019, 2020 Fridolin Pokorny
#
# This program is free software: you can redistribute it and / or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.

"""Implementation of Management API v1."""

import os
from itertools import islice
import logging
import typing
from typing import Any
from typing import Optional
from typing import Dict
from typing import Tuple

from thoth.common import OpenShift
from thoth.common.exceptions import NotFoundException as OpenShiftNotFound
from thoth.storages import __version__ as thoth_storages_version
from thoth.storages.graph.models_performance import ALL_PERFORMANCE_MODELS
from thoth.storages import SolverResultsStore
from thoth.storages import DependencyMonkeyReportsStore
from thoth.storages.exceptions import NotFoundError

from .configuration import Configuration


PAGINATION_SIZE = 100
_LOGGER = logging.getLogger(__name__)
_OPENSHIFT = OpenShift()


def get_info(secret: str):
    """Get information about Thoth deployment."""
    if secret != Configuration.THOTH_MANAGEMENT_API_TOKEN:
        return {"error": "Wrong secret provided"}, 401

    return {
        "deployment_name": os.getenv("THOTH_DEPLOYMENT_NAME"),
        "s3_endpoint_url": os.getenv("THOTH_S3_ENDPOINT_URL"),
        "knowledge_graph_host": os.getenv("KNOWLEDGE_GRAPH_HOST"),
        "amun_api_url": os.getenv("AMUN_API_URL"),
        "frontend_namespace": os.getenv("THOTH_FRONTEND_NAMESPACE"),
        "middletier_namespace": os.getenv("THOTH_MIDDLETIER_NAMESPACE"),
        "amun_inspection_namespace": os.getenv("THOTH_AMUN_INSPECTION_NAMESPACE"),
        "backend_namespace": os.getenv("THOTH_BACKEND_NAMESPACE"),
        "s3_bucket_prefix": os.getenv("THOTH_CEPH_BUCKET_PREFIX"),
    }


def post_register_python_package_index(
    secret: str, index: dict, enabled: bool = False
) -> tuple:
    """Register the given Python package index in the graph database."""
    from .openapi_server import GRAPH

    if secret != Configuration.THOTH_MANAGEMENT_API_TOKEN:
        return {"error": "Wrong secret provided"}, 401

    GRAPH.register_python_package_index(
        url=index["url"],
        warehouse_api_url=index["warehouse_api_url"],
        verify_ssl=index["verify_ssl"] if index.get("verify_ssl") is not None else True,
        enabled=enabled,
    )
    return {}, 201


def post_set_python_package_index_state(
    secret: str, index_url: dict, enabled: bool
) -> tuple:
    """Disable or enable a Python package index."""
    from .openapi_server import GRAPH

    if secret != Configuration.THOTH_MANAGEMENT_API_TOKEN:
        return {"error": "Wrong secret provided"}, 401

    try:
        GRAPH.set_python_package_index_state(index_url, enabled=enabled)
    except NotFoundError as exc:
        return {"error": str(exc)}, 404

    return {}, 201


def post_solve_python(
    python_package: dict,
    version_specifier: Optional[str] = None,
    debug: bool = False,
    transitive: bool = False,
    index_url: Optional[str] = None,
):
    """Schedule analysis for the given Python package."""
    parameters = locals()
    from .openapi_server import GRAPH

    package_name = python_package.pop("package_name")

    version_specifier = python_package.pop("version_specifier")
    if version_specifier == "*":
        version_specifier = ""

    packages = package_name + (version_specifier if version_specifier else "")

    if index_url:
        try:
            if not GRAPH.is_python_package_index_enabled(url=index_url):
                return (
                    {
                        "parameters": parameters,
                        "error": f"Index URL provided {index_url!r} is disabled",
                    },
                    400,
                )
        except NotFoundError:
            return (
                {
                    "parameters": parameters,
                    "error": f"Index URL provided {index_url!r} not registered in Thoth.",
                },
                400,
            )

        indexes = [index_url]
    else:
        indexes = GRAPH.get_python_package_index_urls_all(enabled=True)

    run_parameters = {
        "packages": packages,
        "indexes": indexes,
        "debug": debug,
        "transitive": transitive,
    }

    response, status_code = _do_schedule(
        run_parameters,
        _OPENSHIFT.schedule_all_solvers,
    )

    # Handle a special case where no solvers for the given name were found.
    if status_code == 202 and not response["analysis_id"]:
        return (
            {
                "error": "Please contact administrator - no solvers were installed",
                "parameters": parameters,
            },
            500,
        )

    return response, status_code


def get_solve_python(analysis_id: str):
    """Retrieve the given solver result."""
    return _get_document(
        SolverResultsStore,
        analysis_id,
        name_prefix="solver-",
        namespace=Configuration.THOTH_MIDDLETIER_NAMESPACE,
    )


def get_solve_python_log(analysis_id: str):
    """Get solver log."""
    return _get_log(
        "solverany", analysis_id, namespace=Configuration.THOTH_MIDDLETIER_NAMESPACE
    )


def get_solve_python_status(analysis_id: str):
    """Get status of an ecosystem solver."""
    return _get_workflow_status(
        locals(), "solver", Configuration.THOTH_MIDDLETIER_NAMESPACE
    )


def list_solve_python_results(page: int = 0):
    """Retrieve a listing of available solver results."""
    return _do_listing(SolverResultsStore, page)


def list_python_package_indexes(secret: str):
    """List registered Python package indexes in the graph database."""
    from .openapi_server import GRAPH

    if secret != Configuration.THOTH_MANAGEMENT_API_TOKEN:
        return {"error": "Wrong secret provided"}, 401

    return {
        "enabled": GRAPH.get_python_package_index_all(enabled=True),
        "disabled": GRAPH.get_python_package_index_all(enabled=False),
    }


def list_solvers():
    """List available registered solvers."""
    # We are fine with 500 here in case of some OpenShift/configuration failures.
    python_solvers = []
    for solver_name in _OPENSHIFT.get_solver_names():
        solver_info = _OPENSHIFT.parse_python_solver_name(solver_name)
        solver_info["solver_name"] = solver_name
        python_solvers.append(solver_info)

    return {"solvers": {"python": python_solvers}, "parameters": {}}


def post_dependency_monkey_python(
    input: Dict[str, Any],
    seed: Optional[int] = None,
    dry_run: bool = False,
    decision: Optional[str] = None,
    debug: bool = False,
    count: Optional[int] = None,
    limit_latest_versions: Optional[int] = None,
):
    """Run dependency monkey on the given application stack to produce all the possible software stacks."""
    requirements = input.pop("requirements")
    context = input.pop("context")
    pipeline = input.pop("pipeline", None)
    predictor = input.pop("predictor", None)
    predictor_config = input.pop("predictor_config", None)
    runtime_environment = input.pop("runtime_environment", None)
    parameters = locals()
    parameters.pop("input")

    return _do_schedule(
        parameters,
        _OPENSHIFT.schedule_dependency_monkey,
        stack_output=Configuration.THOTH_DEPENDENCY_MONKEY_STACK_OUTPUT,
    )


def get_dependency_monkey_python_log(analysis_id: str):
    """Get dependency monkey container log."""
    return _get_log(
        "dm", analysis_id, namespace=Configuration.THOTH_AMUN_INSPECTION_NAMESPACE
    )


def get_dependency_monkey_python_status(analysis_id: str):
    """Get dependency monkey container status."""
    return _get_workflow_status(
        locals(), "dependency-monkey-", Configuration.THOTH_AMUN_INSPECTION_NAMESPACE
    )


def post_analyze(
    secret: str,
    image: str,
    debug: bool = False,
    registry_user: Optional[str] = None,
    registry_password: Optional[str] = None,
    environment_type: Optional[str] = None,
    origin: Optional[str] = None,
    verify_tls: bool = True,
) -> Tuple[Dict[str, Any], int]:
    """Run an analyzer in a restricted namespace."""
    parameters = locals()

    if secret != Configuration.THOTH_MANAGEMENT_API_TOKEN:
        return {"error": "Wrong secret provided"}, 401

    parameters.pop("secret", None)
    parameters["environment_type"] = parameters.get("runtime_environment") or "runtime"
    parameters["is_external"] = False

    response, status_code = _do_schedule(
        parameters,
        _OPENSHIFT.schedule_package_extract,
    )

    return response, status_code


def get_graph_version():
    """Get version of Thoth's storages package installed."""
    return {"thoth-storages": thoth_storages_version}, 200


def get_performance_indicators():
    """List available performance indicators."""
    return {
        "performance-indicators": [
            model_class.__name__ for model_class in ALL_PERFORMANCE_MODELS
        ],
        "parameters": {},
    }


def schedule_graph_refresh(secret: str):
    """Schedule graph refresh job."""
    if secret != Configuration.THOTH_MANAGEMENT_API_TOKEN:
        return {"error": "Wrong secret provided"}, 401

    job_id = _OPENSHIFT.schedule_graph_refresh()
    return {"job_id": job_id}, 201


def get_dependency_monkey_report(analysis_id: str) -> tuple:
    """Retrieve a dependency monkey run report."""
    parameters = {"analysis_id": analysis_id}

    adapter = DependencyMonkeyReportsStore()
    adapter.connect()

    try:
        document = adapter.retrieve_document(analysis_id)
    except NotFoundError:
        return (
            {
                "parameters": parameters,
                "error": f"Report with the given id {analysis_id} was not found",
            },
            404,
        )

    return {"parameters": parameters, "report": document}, 200


def get_hardware_environment(
    secret: str, page: int = 0
) -> typing.Tuple[typing.Dict[str, typing.Any], int]:
    """Get listing of available hardware environments."""
    from .openapi_server import GRAPH

    if secret != Configuration.THOTH_MANAGEMENT_API_TOKEN:
        return {"error": "Wrong secret provided"}, 401

    return {
        "hardware_environments": GRAPH.get_hardware_environments_all(
            is_external=False, start_offset=page, without_id=False
        ),
        "parameters": {
            "page": page,
        },
    }, 200


def post_hardware_environment(
    secret: str, hardware_environment: typing.Dict[str, typing.Any]
) -> typing.Tuple[typing.Dict[str, typing.Any], int]:
    """Get listing of available hardware environments."""
    from .openapi_server import GRAPH

    if secret != Configuration.THOTH_MANAGEMENT_API_TOKEN:
        return {"error": "Wrong secret provided"}, 401

    hw_id = GRAPH.create_hardware_information(hardware_environment, is_external=False)
    return {
        "parameters": {"hardware_environment": hardware_environment},
        "id": hw_id,
    }, 201


def delete_hardware_environment(
    secret: str, id: int
) -> typing.Tuple[typing.Dict[str, typing.Any], int]:
    """Delete the given hardware environment entry."""
    from .openapi_server import GRAPH

    if secret != Configuration.THOTH_MANAGEMENT_API_TOKEN:
        return {"error": "Wrong secret provided"}, 401

    GRAPH.delete_hardware_information(id, is_external=False)

    return {}, 200


def initialize_schema(secret: str):
    """Initialize/update schema in graph database (async)."""
    if secret != Configuration.THOTH_MANAGEMENT_API_TOKEN:
        return {"error": "Wrong secret provided"}, 401

    job_id = _OPENSHIFT.schedule_graph_schema_update()
    return {"job_id": job_id}, 201


def schedule_solver_unsolvable(secret: str, solver_name: str) -> tuple:
    """Schedule solving of unsolvable packages for the given solver."""
    from .openapi_server import GRAPH

    if secret != Configuration.THOTH_MANAGEMENT_API_TOKEN:
        return {"error": "Wrong secret provided"}, 401

    parameters = {"solver_name": solver_name}

    solvers_installed = _OPENSHIFT.get_solver_names()
    if solver_name not in solvers_installed:
        return (
            {
                "parameters": parameters,
                "error": f"Solver with name {solver_name!r} is not installed, "
                f"installed solvers: {', '.join(list(solvers_installed))}",
            },
            404,
        )

    indexes = GRAPH.get_python_package_index_urls_all(enabled=True)
    analyses = []
    for package_name, versions in GRAPH.retrieve_unsolvable_python_packages(
        solver_name
    ).items():
        for package_version in versions:
            analysis_id = _OPENSHIFT.schedule_solver(
                packages=f"{package_name}==={package_version}",
                solver=solver_name,
                indexes=indexes,
                transitive=False,
            )

            analyses.append(
                {
                    "package_name": package_name,
                    "package_version": package_version,
                    "analysis_id": analysis_id,
                }
            )

    response = {"parameters": parameters, "index_urls": indexes, "analyses": analyses}

    if analyses:
        return response, 202

    # No analyses to run, return 200.
    return response, 200


def _do_listing(adapter_class, page: int) -> tuple:
    """Perform actual listing of documents available."""
    adapter = adapter_class()
    adapter.connect()
    result = adapter.get_document_listing()
    # We will need to abandon this logic later anyway once we will be
    # able to query results on data hub side.
    results = list(
        islice(result, page * PAGINATION_SIZE, page * PAGINATION_SIZE + PAGINATION_SIZE)
    )
    return (
        {"results": results, "parameters": {"page": page}},
        200,
        {"page": page, "page_size": PAGINATION_SIZE, "results_count": len(results)},
    )


def _get_document(
    adapter_class,
    analysis_id: str,
    name_prefix: Optional[str] = None,
    namespace: Optional[str] = None,
) -> tuple:
    """Perform actual document retrieval."""
    # Parameters to be reported back to a user of API.
    parameters = {"analysis_id": analysis_id}
    if not analysis_id or not name_prefix:
        return (
            {
                "error": "No analysis id or name prefix provided",
                "parameters": parameters,
            },
            400,
        )
    if not analysis_id.startswith(name_prefix):
        return {"error": "Wrong analysis id provided", "parameters": parameters}, 400

    try:
        adapter = adapter_class()
        adapter.connect()
        result = adapter.retrieve_document(analysis_id)
        return result, 200
    except NotFoundError:
        if namespace:
            try:
                status = _OPENSHIFT.get_job_status_report(
                    analysis_id, namespace=namespace
                )
                if status["state"] == "running" or (
                    status["state"] == "terminated" and status["exit_code"] == 0
                ):
                    # In case we hit terminated and exit code equal to 0, the analysis has just finished and
                    # before this call (document retrieval was unsuccessful, pod finished and we asked later
                    # for status). To fix this time-dependent issue, let's user ask again. Do not do pod status
                    # check before document retrieval - this solution is more optimal as we do not ask master
                    # status each time.
                    return (
                        {
                            "error": "Analysis is still in progress",
                            "status": status,
                            "parameters": parameters,
                        },
                        202,
                    )
                elif status["state"] == "terminated":
                    return (
                        {
                            "error": "Analysis was not successful",
                            "status": status,
                            "parameters": parameters,
                        },
                        404,
                    )
                elif status["state"] in ("scheduling", "waiting", "registered"):
                    return (
                        {
                            "error": "Analysis is being scheduled",
                            "status": status,
                            "parameters": parameters,
                        },
                        202,
                    )
                else:
                    # Can be:
                    #   - return 500 to user as this is our issue
                    raise ValueError(f"Unreachable - unknown job state: {status}")
            except OpenShiftNotFound:
                pass
        return (
            {
                "error": f"Requested result for analysis {analysis_id!r} was not found",
                "parameters": parameters,
            },
            404,
        )


def _get_log(
    node_name: str, analysis_id: str, namespace: str
) -> typing.Tuple[typing.Dict[str, typing.Any], int]:
    """Get log for a node in a workflow."""
    result: typing.Dict[str, typing.Any] = {"parameters": {"analysis_id": analysis_id}}
    try:
        log = _OPENSHIFT.get_workflow_node_log(node_name, analysis_id, namespace)
    except NotFoundError as exc:
        _LOGGER.exception(f"Log for {analysis_id} were not found: {str(exc)}")
        d: typing.Dict[str, Any] = {
            "error": f"Log for analysis {analysis_id} was not found or it has not started yet"
        }
        result.update(d)
        return result, 404
    else:
        result.update({"log": log})
        return result, 200


def _get_workflow_status(parameters: dict, name_prefix: str, namespace: str):
    """Get status for a argo workflow."""
    workflow_id = parameters.get("analysis_id")
    if workflow_id is None:
        return {"error": "No workflow id provided", "parameters": parameters}, 400
    if not workflow_id.startswith(name_prefix):
        return {"error": "Wrong workflow id provided", "parameters": parameters}, 400

    try:
        status = _OPENSHIFT.get_workflow_status_report(
            workflow_id=workflow_id, namespace=namespace
        )
    except OpenShiftNotFound:
        return (
            {
                "parameters": parameters,
                "error": f"Requested status for workflow {workflow_id!r} was not found",
            },
            404,
        )
    return {"parameters": parameters, "status": status}, 200


def _do_schedule(parameters: dict, runner: typing.Callable, **runner_kwargs):
    """Schedule the given job - a generic method for running any analyzer, solver, ..."""
    return (
        {
            "analysis_id": runner(**parameters, **runner_kwargs),
            "parameters": parameters,
            "cached": False,
        },
        202,
    )
