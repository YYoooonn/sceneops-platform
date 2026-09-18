# --------------------
# Airflow (PoC pipeline execution backend)
# --------------------

.PHONY: airflow-up
airflow-up:
	$(COMPOSE) --profile airflow up -d --build

.PHONY: airflow-down
airflow-down:
	$(COMPOSE) --profile airflow down

.PHONY: airflow-logs
airflow-logs:
	$(COMPOSE) --profile airflow logs -f airflow-webserver airflow-scheduler
