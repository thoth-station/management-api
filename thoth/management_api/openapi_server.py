#!/usr/bin/env python3
# management-api
# Copyright(C) 2019 - 2021 Christoph Görn
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

"""Thoth Management API entrypoint."""


import os
import sys
import logging
from datetime import datetime

import connexion
from connexion.resolver import RestyResolver

from flask import redirect, jsonify, request
from flask_script import Manager
from prometheus_flask_exporter import PrometheusMetrics

from thoth.common import __version__ as __common__version__
from thoth.storages import __version__ as __storages__version__
from thoth.common import datetime2datetime_str
from thoth.common import init_logging
from thoth.storages import GraphDatabase
from thoth.storages.exceptions import DatabaseNotInitialized
from thoth.management_api import __version__
from thoth.management_api.configuration import Configuration
from thoth.management_api.configuration import init_jaeger_tracer


# Configure global application logging using Thoth's init_logging.
init_logging(logging_env_var_start="THOTH_MANAGEMENT_API_LOG_")

_LOGGER = logging.getLogger("thoth.management_api")
_LOGGER.setLevel(
    logging.DEBUG
    if bool(int(os.getenv("THOTH_MANAGEMENT_API_DEBUG", 0)))
    else logging.INFO
)

__service_version__ = (
    f"{__version__}+storage.{__storages__version__}.common.{__common__version__}"
)

_LOGGER.info("This is Management API v%s", __service_version__)
_LOGGER.debug("DEBUG mode is enabled!")

_THOTH_API_HTTPS = bool(int(os.getenv("THOTH_API_HTTPS", 1)))

# Expose for uWSGI.
app = connexion.FlaskApp(
    __name__, specification_dir=Configuration.SWAGGER_YAML_PATH, debug=True
)

app.add_api(
    "openapi.yaml",
    options={"swagger_ui": True},
    arguments={"title": "Management API"},
    resolver=RestyResolver("thoth.management_api"),
    strict_validation=True,
    validate_responses=bool(int(os.getenv("THOTH_API_VALIDATE_RESPONSES", 0))),
)


application = app.app


# create tracer and put it in the application configuration
Configuration.tracer = init_jaeger_tracer("management_api")

# create metrics and manager
metrics = PrometheusMetrics(application)
manager = Manager(application)

# Needed for session.
application.secret_key = Configuration.APP_SECRET_KEY

# static information as metric
metrics.info("management_api_info", "Management API info", version=__service_version__)
_API_GAUGE_METRIC = metrics.info(
    "management_api_schema_up2date", "Management API schema up2date"
)

# Instantiate one GraphDatabase adapter in the whole application (one per wsgi worker) to correctly
# reuse connection pooling from one instance.
GRAPH = GraphDatabase()

# custom metric to expose head revision from thoth-storages library
schema_revision_metric = metrics.info(
    "thoth_database_schema_revision_script",
    "Thoth database schema revision from script",
    component="management-api",  # label
    revision=GRAPH.get_script_alembic_version_head(),  # label
    env=Configuration.THOTH_DEPLOYMENT_NAME,  # label
)

GRAPH.connect()


@application.before_request
def before_request_callback():
    """Register callback, runs before each request to this service."""
    method = request.method
    path = request.path

    # Update up2date metric exposed.
    if method == "GET" and path == "/metrics":
        try:
            _API_GAUGE_METRIC.set(int(GRAPH.is_schema_up2date()))
        except DatabaseNotInitialized as exc:
            # This can happen if database is erased after the service has been started as we
            # have passed readiness probe with this check.
            _LOGGER.exception(
                "Cannot determine database schema as database is not initialized: %s",
                str(exc),
            )
            _API_GAUGE_METRIC.set(0)


@application.before_first_request
def before_first_request_callback():
    """Register callback, runs before first request to this service."""
    schema_revision_metric.set(1)

    if bool(int(os.getenv("THOTH_MANAGEMENT_API_RUN_MIGRATIONS", 0))):
        GRAPH.initialize_schema()


@app.route("/")
@metrics.do_not_track()
def base_url():
    """Redirect to UI by default."""
    # https://github.com/pallets/flask/issues/773
    request.environ["wsgi.url_scheme"] = "https" if _THOTH_API_HTTPS else "http"
    return redirect("api/v1/ui/")


@app.route("/api/v1")
@metrics.do_not_track()
def api_v1():
    """Provide a listing of all available endpoints."""
    paths = []

    for rule in application.url_map.iter_rules():
        rule = str(rule)
        if rule.startswith("/api/v1"):
            paths.append(rule)

    return jsonify({"paths": paths})


def _healthiness():
    return (
        jsonify({"status": "ready", "version": __version__}),
        200,
        {"ContentType": "application/json"},
    )


@app.route("/readiness")
@metrics.do_not_track()
def api_readiness():
    """Report readiness for OpenShift readiness probe."""
    return _healthiness()


@app.route("/liveness")
@metrics.do_not_track()
def api_liveness():
    """Report liveness for OpenShift readiness probe."""
    return _healthiness()


@application.errorhandler(404)
@metrics.do_not_track()
def page_not_found(exc):
    """Adjust 404 page to be consistent with errors reported back from API."""
    # Flask has a nice error message - reuse it.
    return jsonify({"error": str(exc)}), 404


@application.errorhandler(500)
@metrics.do_not_track()
def internal_server_error(exc):
    """Adjust 500 page to be consistent with errors reported back from API."""
    # Provide some additional information so we can easily find exceptions in logs (time and exception type).
    # Later we should remove exception type (for security reasons).
    return (
        jsonify(
            {
                "error": "Internal server error occurred, please contact administrator with provided details.",
                "details": {
                    "type": exc.__class__.__name__,
                    "datetime": datetime2datetime_str(datetime.utcnow()),
                },
            }
        ),
        500,
    )


@application.after_request
def apply_headers(response):
    """Add headers to each response."""
    response.headers["X-Management-API-Version"] = __version__
    response.headers["X-Management-API-Service-Version"] = __service_version__
    return response


if __name__ == "__main__":
    app.run()

    Configuration.tracer.close()

    sys.exit(1)
