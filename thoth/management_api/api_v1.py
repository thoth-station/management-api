#!/usr/bin/env python3
# thoth-management-api
# Copyright(C) 2018 Fridolin Pokorny
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
import asyncio
import logging
import typing

from thoth.common import OpenShift
from thoth.storages import GraphDatabase
from thoth.storages import SolverResultsStore
from thoth.storages import DependencyMonkeyReportsStore
from thoth.storages.exceptions import NotFoundError

from .configuration import Configuration


PAGINATION_SIZE = 100
_LOGGER = logging.getLogger(__name__)
_OPENSHIFT = OpenShift()


def post_register_python_package_index(url: str, warehouse_api_url: str = None, verify_ssl: bool = True):
    """Register the given Python package index in the graph database."""
    graph = GraphDatabase()
    graph.connect()
    graph.register_python_package_index(
        url=url,
        warehouse_api_url=warehouse_api_url,
        verify_ssl=verify_ssl if verify_ssl is not None else True
    )
    return {}, 201


def post_solve_python(package_name: str, version_specifier: str = None, debug: bool = False):
    """Register the given Python package in Thoth."""
    packages = package_name + (version_specifier if version_specifier else '')
    response, status_code = _do_run(parameters, _OPENSHIFT.run_solver, output=Configuration.THOTH_SOLVER_OUTPUT)

    # Handle a special case where no solvers for the given name were found.
    if status_code == 202 and not response['analysis_id']:
        if solver:
            return {
                'error': "No solver was run",
                'parameters': parameters
            }, 400
        else:
            return {
                'error': "Please contact administrator - no solvers were installed",
                'parameters': parameters
            }, 500

    return response, status_code


def get_solve_python(analysis_id: str):
    """Retrieve the given solver result."""
    return _get_document(
        SolverResultsStore, analysis_id,
        name_prefix='solver-', namespace=Configuration.THOTH_MIDDLETIER_NAMESPACE
    )


def get_solve_python_log(analysis_id: str):
    """Get solver log."""
    return _get_job_log(locals(), 'solver', Configuration.THOTH_MIDDLETIER_NAMESPACE)


def get_solve_python_status(analysis_id: str):
    """Get status of an ecosystem solver."""
    return _get_job_status(locals(), 'solver', Configuration.THOTH_MIDDLETIER_NAMESPACE)


def list_solve_python_results(page: int = 0):
    """Retrieve a listing of available solver results."""
    return _do_listing(SolverResultsStore, page)


def list_solvers():
    """List available registered solvers."""
    # We are fine with 500 here in case of some OpenShift/configuration failures.
    return {
        'solvers': {'python': _OPENSHIFT.get_solver_names()},
        'parameters': {}
    }


def post_dependency_monkey_python(input: dict, seed: int = None, dry_run: bool = False, limit: int = None,
                                  decision: str = None, debug: bool = False):
    """Run dependency monkey on the given application stack to produce all the possible software stacks."""
    requirements = input.pop('requirements')
    context = input.pop('context')
    parameters = locals()
    parameters.pop('input')

    return _do_run(
        parameters,
        _OPENSHIFT.run_dependency_monkey,
        report_output=Configuration.THOTH_DEPENDENCY_MONKEY_REPORT_OUTPUT,
        stack_output=Configuration.THOTH_DEPENDENCY_MONKEY_STACK_OUTPUT
    )


def get_dependency_monkey_python_log(analysis_id: str):
    """Get dependency monkey container log."""
    return _get_job_log(locals(), 'dependency-monkey-', Configuration.THOTH_MIDDLETIER_NAMESPACE)


def get_dependency_monkey_python_status(analysis_id: str):
    """Get dependency monkey container status."""
    return _get_job_status(locals(), 'dependency-monkey-', Configuration.THOTH_MIDDLETIER_NAMESPACE)


def sync(secret: str, force_sync: bool = False):
    """Sync results to graph database."""
    parameters = locals()
    if secret != Configuration.THOTH_MANAGEMENT_API_TOKEN:
        return {
            'error': 'Wrong secret provided'
        }, 401

    return {
        'sync_id': _OPENSHIFT.run_sync(force_sync=force_sync),
        'parameters': parameters
    }, 202


def erase_graph(secret: str):
    """Clean content of the graph database."""
    if secret != Configuration.THOTH_MANAGEMENT_API_TOKEN:
        return {
            'error': 'Wrong secret provided'
        }, 401

    adapter = GraphDatabase()
    adapter.connect()
    loop = asyncio.get_event_loop()
    loop.run_until_complete(adapter.g.V().drop().next())
    return {}, 201


def get_dependency_monkey_report(analysis_id: str) -> dict:
    """Retrieve a dependency monkey run report."""
    parameters = {'analysis_id': analysis_id}

    adapter = DependencyMonkeyReportsStore()
    adapter.connect()

    try:
        document = adapter.retrieve_document(analysis_id)
    except NotFoundError:
        return {
            'parameters': parameters,
            'error': f"Report with the given id {analysis_id} was not found"
        }, 404

    return {
        'parameters': parameters,
        'report': document
    }


def _do_listing(adapter_class, page: int) -> tuple:
    """Perform actual listing of documents available."""
    adapter = adapter_class()
    adapter.connect()
    result = adapter.get_document_listing()
    # We will need to abandon this logic later anyway once we will be
    # able to query results on data hub side.
    results = list(islice(result, page * PAGINATION_SIZE, page * PAGINATION_SIZE + PAGINATION_SIZE))
    return {
        'results': results,
        'parameters': {'page': page}
    }, 200, {
        'page': page,
        'page_size': PAGINATION_SIZE,
        'results_count': len(results)
    }


def _get_document(adapter_class, analysis_id: str, name_prefix: str = None, namespace: str = None) -> tuple:
    """Perform actual document retrieval."""
    # Parameters to be reported back to a user of API.
    parameters = {'analysis_id': analysis_id}
    if not analysis_id.startswith(name_prefix):
        return {
            'error': 'Wrong analysis id provided',
            'parameters': parameters
        }, 400

    try:
        adapter = adapter_class()
        adapter.connect()
        result = adapter.retrieve_document(analysis_id)
        return result, 200
    except NotFoundError:
        if namespace:
            try:
                status = _OPENSHIFT.get_job_status_report(analysis_id, namespace=namespace)
                if status['state'] == 'running' or \
                        (status['state'] == 'terminated' and status['exit_code'] == 0):
                    # In case we hit terminated and exit code equal to 0, the analysis has just finished and
                    # before this call (document retrieval was unsuccessful, pod finished and we asked later
                    # for status). To fix this time-dependent issue, let's user ask again. Do not do pod status
                    # check before document retrieval - this solution is more optimal as we do not ask master
                    # status each time.
                    return {
                        'error': 'Analysis is still in progress',
                        'status': status,
                        'parameters': parameters
                    }, 202
                elif status['state'] == 'terminated':
                    return {
                        'error': 'Analysis was not successful',
                        'status': status,
                        'parameters': parameters
                    }, 404
                elif status['state'] in ('scheduling', 'waiting'):
                    return {
                        'error': 'Analysis is being scheduled',
                        'status': status,
                        'parameters': parameters
                    }, 202
                else:
                    # Can be:
                    #   - return 500 to user as this is our issue
                    raise ValueError(f"Unreachable - unknown job state: {status}")
            except OpenShiftNotFound:
                pass
        return {
            'error': f'Requested result for analysis {analysis_id!r} was not found',
            'parameters': parameters
        }, 404


def _get_job_log(parameters: dict, name_prefix: str, namespace: str):
    """Get job log based on analysis id."""
    job_id = parameters.get('analysis_id')
    if not job_id.startswith(name_prefix):
        return {
            'error': 'Wrong analysis id provided',
            'parameters': parameters
        }, 400

    return {
        'parameters': parameters,
        'log': _OPENSHIFT.get_job_log(job_id, namespace=namespace)
    }, 200


def _get_job_status(parameters: dict, name_prefix: str, namespace: str):
    """Get status for a job."""
    job_id = parameters.get('analysis_id')
    if not job_id.startswith(name_prefix):
        return {
            'error': 'Wrong analysis id provided',
            'parameters': parameters
        }, 400

    status = _OPENSHIFT.get_job_status_report(job_id, namespace=namespace)
    return {
        'parameters': parameters,
        'status': status
    }


def _do_run(parameters: dict, runner: typing.Callable, **runner_kwargs):
    """Run the given job - a generic method for running any analyzer, solver, ..."""
    return {
        'analysis_id': runner(**parameters, **runner_kwargs),
        'parameters': parameters,
        'cached': False
    }, 202
