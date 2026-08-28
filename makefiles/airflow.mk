# --------------------
# Airflow (PoC pipeline execution backend)
# --------------------

.PHONY: airflow-up
airflow-up:
	docker compose -f $(COMPOSE_FILE) --profile airflow up -d --build

.PHONY: airflow-down
airflow-down:
	docker compose -f $(COMPOSE_FILE) --profile airflow down

.PHONY: airflow-logs
airflow-logs:
	docker compose -f $(COMPOSE_FILE) --profile airflow logs -f airflow-webserver airflow-scheduler
