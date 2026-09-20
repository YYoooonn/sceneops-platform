FROM apache/airflow:2.10.4-python3.11
# Pinned to 2.x on purpose: this whole integration (webserver command,
# /api/v1 REST API, AIRFLOW__API__AUTH_BACKENDS basic auth) is built against
# Airflow 2.x's architecture. `slim-latest` previously resolved to 3.3.1,
# whose "webserver" command was removed (renamed to "api-server") and whose
# REST API/auth model changed — do not switch back to `latest` without
# porting the integration.

# The (previously slim) base image excludes provider packages/drivers. We need:
# - apache-airflow-providers-docker: DockerOperator (spawns worker containers)
# - apache-airflow-providers-postgres: pulls in psycopg2, required by
#   AIRFLOW__DATABASE__SQL_ALCHEMY_CONN (postgresql+psycopg2://...)
# Installed against the official constraints file for this exact
# Airflow+Python combo so pip can't silently pick a provider version that
# assumes newer core internals than 2.10.4 actually has.
RUN pip install --no-cache-dir \
    apache-airflow-providers-docker \
    apache-airflow-providers-postgres \
    --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-2.10.4/constraints-3.11.txt"
