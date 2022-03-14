Thoth Core Management API
=========================

.. image:: https://img.shields.io/github/v/tag/thoth-station/management-api?style=plastic
  :target: https://github.com/thoth-station/management-api/releases
  :alt: GitHub tag (latest by date)

.. image:: https://quay.io/repository/thoth-station/management-api/status
  :target: https://quay.io/repository/thoth-station/management-api?tab=tags
  :alt: Quay - Build

.. image:: https://api.codacy.com/project/badge/Grade/d8f62cde59b84854ac425d148570f1ab
   :alt: Codacy Badge
   :target: https://app.codacy.com/app/thoth-station/management-api?utm_source=github.com&utm_medium=referral&utm_content=thoth-station/management-api&utm_campaign=Badge_Grade_Dashboard

This API service is used for administrative and operational tasks for a Thoth
deployment. For Management API interaction, one needs a token that can be
obtained by contacting Thoth deployment administrator.

Installation and deployment
###########################

The service is built using OpenShift Source-to-Image and deployed
automatically via Argo CD - see `thoth-station/thoth-application
repository <https://github.com/thoth-station/thoth-application>`_.

Running Management API locally
##############################

Management API can be run locally in a mode when it still talks to the cluster.
To run Management API locally, create ``.env`` file out out ``.env.template``
and adjust environment variable values as desired (see `thoth-station/storages
<https://github.com/thoth-station/storages/>`__ for more info):

.. code-block:: console

  cp .env.template .env
  vim .env

Once the environment is properly setup, you can run Management API locally:

.. code-block:: console

  pipenv install
  pipenv run gunicorn thoth.management_api.openapi_server:app --config gunicorn.conf.py
