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

"""Configuration of management API service."""

import logging
import os

from jaeger_client import Config as JaegerConfig
from jaeger_client.metrics.prometheus import PrometheusMetricsFactory

_LOGGER = logging.getLogger(__name__)

_AMUN_API_URL = os.getenv('AMUN_API_URL') or '-'
if _AMUN_API_URL == '-':
    _LOGGER.error("Amun API URL was not configured, Dependency Monkey results will not "
                  "be submitted to Amun for inspection!")


class Configuration:
    """Configuration of management API service."""

    APP_SECRET_KEY = os.environ['THOTH_APP_SECRET_KEY']
    SWAGGER_YAML_PATH = os.path.join(os.path.dirname(os.path.realpath(__file__)), "../../openapi")
    THOTH_RESULT_API_URL = os.environ['THOTH_RESULT_API_URL']
    THOTH_SOLVER_OUTPUT = THOTH_RESULT_API_URL + '/api/v1/solver-result'
    THOTH_DEPENDENCY_MONKEY_STACK_OUTPUT = _AMUN_API_URL
    THOTH_DEPENDENCY_MONKEY_REPORT_OUTPUT = THOTH_RESULT_API_URL + '/api/v1/dependency-monkey-report'
    THOTH_MANAGEMENT_API_TOKEN = os.environ['THOTH_MANAGEMENT_API_TOKEN']
    THOTH_MIDDLETIER_NAMESPACE = os.environ['THOTH_MIDDLETIER_NAMESPACE']
    THOTH_SOLVER_SUBGRAPH_CHECK_API = THOTH_RESULT_API_URL + '/api/v1/subgraph-check'
    THOTH_PACKAGE_ANALYZER_OUTPUT = THOTH_RESULT_API_URL + '/api/v1/package-analysis-result'

    JAEGER_HOST = os.getenv("JAEGER_HOST", "localhost")

    OPENAPI_PORT = 8080
    GRPC_PORT = 8443

    tracer = None


def init_jaeger_tracer(service_name):
    """Create a Jaeger/OpenTracing configuration."""
    config = JaegerConfig(
        config={
            "sampler": {"type": "const", "param": 1},
            "logging": True,
            "local_agent": {"reporting_host": Configuration.JAEGER_HOST},
        },
        service_name=service_name,
        validate=True,
        metrics_factory=PrometheusMetricsFactory(namespace=service_name),
    )

    return config.initialize_tracer()
