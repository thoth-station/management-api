Thoth Core Management API
=========================

.. image:: https://api.codacy.com/project/badge/Grade/d8f62cde59b84854ac425d148570f1ab
   :alt: Codacy Badge
   :target: https://app.codacy.com/app/thoth-station/management-api?utm_source=github.com&utm_medium=referral&utm_content=thoth-station/management-api&utm_campaign=Badge_Grade_Dashboard

.. image:: https://zuul-ci.org/gated.svg
   :alt: Zuul gated

This API service is used for administrative and operational tasks for a Thoth
deployment. For Management API interaction, one needs a token that can be
obtained by contacting Thoth deployment administrator.

Installation and deployment
###########################

The service is built using OpenShift Source-to-Image and deployed
automatically via Argo CD - see `thoth-station/thoth-application
repository <https://github.com/thoth-station/thoth-application>`_.
