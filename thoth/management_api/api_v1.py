#!/usr/bin/env python3
# thoth-management-api
# Copyright(C) 2018, 2019 Fridolin Pokorny
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

from itertools import islice
import logging
import typing

from thoth.common import OpenShift
from thoth.common.exceptions import NotFoundException as OpenShiftNotFound
from thoth.storages import __version__ as thoth_storages_version
from thoth.storages import GraphDatabase
from thoth.storages.graph.models_performance import ALL_PERFORMANCE_MODELS
from thoth.storages import SolverResultsStore
from thoth.storages import DependencyMonkeyReportsStore
from thoth.storages import PackageAnalysisResultsStore
from thoth.storages.exceptions import NotFoundError

from .configuration import Configuration


PAGINATION_SIZE = 100
_LOGGER = logging.getLogger(__name__)
_OPENSHIFT = OpenShift()


def get_info():
    """Get information about Thoth deployment."""
    return {
        "deployment_name": os.getenv("THOTH_DEPLOYMENT_NAME"),
        "s3_endpoint_url": os.getenv("THOTH_S3_ENDPOINT_URL"),
        "knowledge_graph_host": os.getenv("KNOWLEDGE_GRAPH_HOST"),
        "amun_api_url": os.getenv("AMUN_API_URL"),
        "frontend_namespace": os.getenv("THOTH_FRONTEND_NAMESPACE"),
        "middletier_namespace": os.getenv("THOTH_MIDDLETIER_NAMESPACE"),
        "backend_namespace": os.getenv("THOTH_BACKEND_NAMESPACE"),
        "s3_bucket_prefix": os.getenv("THOTH_CEPH_BUCKET_PREFIX"),
    }


def post_register_python_package_index(secret: str, index: dict, enabled: bool = False) -> tuple:
    """Register the given Python package index in the graph database."""
    if secret != Configuration.THOTH_MANAGEMENT_API_TOKEN:
        return {"error": "Wrong secret provided"}, 401

    graph = GraphDatabase()
    graph.connect()
    graph.register_python_package_index(
        url=index["url"],
        warehouse_api_url=index["warehouse_api_url"],
        verify_ssl=index["verify_ssl"] if index.get("verify_ssl") is not None else True,
        enabled=enabled,
    )
    return {}, 201


def post_set_python_package_index_state(secret: str, index_url: dict, enabled: bool) -> tuple:
    """Disable or enable a Python package index."""
    if secret != Configuration.THOTH_MANAGEMENT_API_TOKEN:
        return {"error": "Wrong secret provided"}, 401

    graph = GraphDatabase()
    graph.connect()
    try:
        graph.set_python_package_index_state(index_url, enabled=enabled)
    except NotFoundError as exc:
        return {"error": str(exc)}, 404

    return {}, 201


def post_solve_python(
    python_package: dict,
    version_specifier: str = None,
    debug: bool = False,
    no_subgraph_checks: bool = False,
    transitive: bool = False,
):
    """Schedule analysis for the given Python package."""
    parameters = locals()

    package_name = python_package.pop("package_name")

    version_specifier = python_package.pop("version_specifier")
    if version_specifier == "*":
        version_specifier = ""

    packages = package_name + (version_specifier if version_specifier else "")

    graph = GraphDatabase()
    graph.connect()
    run_parameters = {
        'packages': packages,
        'indexes': list(graph.get_python_package_index_urls()),
        'debug': debug,
        'subgraph_check_api': Configuration.THOTH_SOLVER_SUBGRAPH_CHECK_API if not no_subgraph_checks else '',
        'transitive': transitive,
    }

    response, status_code = _do_schedule(
        run_parameters, _OPENSHIFT.schedule_all_solvers, output=Configuration.THOTH_SOLVER_OUTPUT
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
    return _get_job_log(locals(), "solver", Configuration.THOTH_MIDDLETIER_NAMESPACE)


def get_solve_python_status(analysis_id: str):
    """Get status of an ecosystem solver."""
    return _get_job_status(locals(), "solver", Configuration.THOTH_MIDDLETIER_NAMESPACE)


def list_solve_python_results(page: int = 0):
    """Retrieve a listing of available solver results."""
    return _do_listing(SolverResultsStore, page)


def list_solvers():
    """List available registered solvers."""
    # We are fine with 500 here in case of some OpenShift/configuration failures.
    python_solvers = []
    for solver_name in _OPENSHIFT.get_solver_names():
        solver_info = GraphDatabase.parse_python_solver_name(solver_name)
        solver_info["solver_name"] = solver_name
        python_solvers.append(solver_info)

    return {
        "solvers": {
            "python": python_solvers
        },
        "parameters": {}
    }


def post_dependency_monkey_python(
    input: dict,
    seed: int = None,
    dry_run: bool = False,
    decision: str = None,
    debug: bool = False,
    count: int = None,
    limit_latest_versions: int = None,
):
    """Run dependency monkey on the given application stack to produce all the possible software stacks."""
    requirements = input.pop("requirements")
    context = input.pop("context")
    parameters = locals()
    parameters.pop("input")

    return _do_schedule(
        parameters,
        _OPENSHIFT.schedule_dependency_monkey,
        report_output=Configuration.THOTH_DEPENDENCY_MONKEY_REPORT_OUTPUT,
        stack_output=Configuration.THOTH_DEPENDENCY_MONKEY_STACK_OUTPUT,
    )


def get_dependency_monkey_python_log(analysis_id: str):
    """Get dependency monkey container log."""
    return _get_job_log(
        locals(), "dependency-monkey-", Configuration.THOTH_MIDDLETIER_NAMESPACE
    )


def get_dependency_monkey_python_status(analysis_id: str):
    """Get dependency monkey container status."""
    return _get_job_status(
        locals(), "dependency-monkey-", Configuration.THOTH_MIDDLETIER_NAMESPACE
    )


def erase_graph(secret: str):
    """Clean content of the graph database."""
    if secret != Configuration.THOTH_MANAGEMENT_API_TOKEN:
        return {"error": "Wrong secret provided"}, 401

    adapter = GraphDatabase()
    adapter.connect()
    adapter.drop_all()
    adapter.initialize_schema()
    return {}, 201


def sync_graph(
    secret: str,
    only_solver_documents: bool,
    only_analysis_documents: bool,
    only_package_analyzer_documents: bool,
    only_inspection_documents: bool,
    only_adviser_documents: bool,
    only_provenance_checker_documents: bool,
    only_dependency_monkey_documents: bool,
):
    """Clean content of the graph database."""
    if secret != Configuration.THOTH_MANAGEMENT_API_TOKEN:
        return {"error": "Wrong secret provided"}, 401

    job_id = _OPENSHIFT.schedule_graph_sync_multiple(
        only_solver_documents=only_solver_documents,
        only_analysis_documents=only_analysis_documents,
        only_package_analyzer_documents=only_package_analyzer_documents,
        only_inspection_documents=only_inspection_documents,
        only_adviser_documents=only_adviser_documents,
        only_provenance_checker_documents=only_provenance_checker_documents,
        only_dependency_monkey_documents=only_dependency_monkey_documents,
    )

    return {"job_id": job_id}, 201


def get_graph_version():
    """Get version of Thoth's storages package installed."""
    return {"thoth-storages": thoth_storages_version}, 200


def get_performance_indicators():
    """List available performance indicators."""
    return {
        "performance-indicators": [
            model_class.__name__ for model_class in ALL_PERFORMANCE_MODELS
        ],
        "parameters": {}
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


def initialize_schema(secret: str):
    """Initialize schema in graph database."""
    if secret != Configuration.THOTH_MANAGEMENT_API_TOKEN:
        return {"error": "Wrong secret provided"}, 401

    graph = GraphDatabase()
    graph.connect()
    try:
        graph.initialize_schema()
    except Exception as exc:
        return {
            "error": str(exc)
        }, 500

    return {}, 201


def schedule_solver_unsolvable(secret: str, solver_name: str) -> tuple:
    """Schedule solving of unsolvable packages for the given solver."""
    if secret != Configuration.THOTH_MANAGEMENT_API_TOKEN:
        return {"error": "Wrong secret provided"}, 401

    parameters = {"solver_name": solver_name}

    graph = GraphDatabase()
    graph.connect()

    solvers_installed = _OPENSHIFT.get_solver_names()
    if solver_name not in solvers_installed:
        return {
            "parameters": parameters,
            "error": f"Solver with name {solver_name!r} is not installed, "
            f"installed solvers: {', '.join(list(solvers_installed))}",
        }, 404

    indexes = list(graph.get_python_package_index_urls())
    analyses = []
    for package_name, versions in graph.retrieve_unsolvable_python_packages(solver_name).items():
        for package_version in versions:
            analysis_id = _OPENSHIFT.schedule_solver(
                packages=f"{package_name}=={package_version}",
                output=Configuration.THOTH_SOLVER_OUTPUT,
                solver=solver_name,
                indexes=indexes,
                subgraph_check_api=Configuration.THOTH_SOLVER_SUBGRAPH_CHECK_API,
                transitive=False,
            )

            analyses.append({
                "package_name": package_name,
                "package_version": package_version,
                "analysis_id": analysis_id,
            })

    response = {
        "parameters": parameters,
        "index_urls": indexes,
        "analyses": analyses,
    }

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
    adapter_class, analysis_id: str, name_prefix: str = None, namespace: str = None
) -> tuple:
    """Perform actual document retrieval."""
    # Parameters to be reported back to a user of API.
    parameters = {"analysis_id": analysis_id}
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


def _get_job_log(parameters: dict, name_prefix: str, namespace: str):
    """Get job log based on analysis id."""
    job_id = parameters.get("analysis_id")
    if not job_id.startswith(name_prefix):
        return {"error": "Wrong analysis id provided", "parameters": parameters}, 400

    try:
        log = _OPENSHIFT.get_job_log(job_id, namespace=namespace)
    except OpenShiftNotFound:
        return (
            {
                "parameters": parameters,
                "error": f"No job with id {job_id} found",
            },
            404,
        )

    return (
        {
            "parameters": parameters,
            "log": log,
        },
        200,
    )


def _get_job_status(parameters: dict, name_prefix: str, namespace: str):
    """Get status for a job."""
    job_id = parameters.get("analysis_id")
    if not job_id.startswith(name_prefix):
        return {"error": "Wrong analysis id provided", "parameters": parameters}, 400

    try:
        status = _OPENSHIFT.get_job_status_report(job_id, namespace=namespace)
    except OpenShiftNotFound:
        return (
            {
                "parameters": parameters,
                "error": f"No job with id {job_id} found"
            },
            404,
        )
    return {"parameters": parameters, "status": status}


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


def post_analyze_package(
    secret: str,
    package_name: str,
    package_version: str,
    index_url: str,
    debug: bool,
    dry_run: bool
):
    """Fetch digests for packages in Python ecosystem."""
    if secret != Configuration.THOTH_MANAGEMENT_API_TOKEN:
        return {"error": "Wrong secret provided"}, 401

    parameters = locals()
    parameters.pop("secret")

    return _do_schedule(
        parameters,
        _OPENSHIFT.schedule_package_analyzer,
        output=Configuration.THOTH_PACKAGE_ANALYZER_OUTPUT
    )


def get_analyze_package(analysis_id: str):
    """Retrieve the given package analyzer result."""
    return _get_document(
        PackageAnalysisResultsStore,
        analysis_id,
        name_prefix="package-analyzer-",
        namespace=Configuration.THOTH_MIDDLETIER_NAMESPACE,
    )


def get_analyze_package_log(analysis_id: str):
    """Get package analyzer log."""
    return _get_job_log(locals(), "package-analyzer", Configuration.THOTH_MIDDLETIER_NAMESPACE)


def get_analyze_package_status(analysis_id: str):
    """Get status of an ecosystem package-analyzer."""
    return _get_job_status(locals(), "package-analyzer", Configuration.THOTH_MIDDLETIER_NAMESPACE)
