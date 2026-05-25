IMAGE_NAME=sceneops-platform
IMAGE_TAG=local

COMPOSE_FILE=docker-compose.local.yml
DB_ENV_FILE=.env
DB_ALEMBIC_CONFIG=packages/sceneops-db/alembic.ini

JOB_ID ?=

.DEFAULT_GOAL := help

.PHONY: help
help:
	@echo "SceneOps Platform"
	@echo ""
	@echo "Setup:"
	@echo "  make prepare-data"
	@echo "  make install-dev"
	@echo ""
	@echo "Docker Compose:"
	@echo "  make compose-build"
	@echo "  make compose-up"
	@echo "  make compose-down"
	@echo "  make compose-logs"
	@echo ""
	@echo "Database:"
	@echo "  make db-up"
	@echo "  make db-migrate"
	@echo "  make db-revision MSG='create jobs table'"
	@echo "  make db-current"
	@echo ""
	@echo "API:"
	@echo "  make api-up"
	@echo "  make api-logs"
	@echo ""
	@echo "Worker:"
	@echo "  make worker-ingest"
	@echo "  make worker-predict-mock-detection"
	@echo "  make worker-evaluate-detection"
	@echo "  make worker-run-job JOB_ID=job-xxx"

# --------------------
# ------SETUP---------
# --------------------

.PHONY: install-dev
install-dev:
	python -m pip install -r requirements-dev.txt
	pre-commit install

.PHONY: check
check:
	pre-commit run --all-files

.PHONY: uninstall-hooks
uninstall-hooks:
	pre-commit uninstall

.PHONY: prepare-data
prepare-data:
	mkdir -p data/raw data/manifests data/artifacts data/runs

.PHONY: clean-all
clean-all:
	rm -rf data/manifests/*
	rm -rf data/artifacts/*
	rm -rf data/runs/*
	$(MAKE) prepare-data

# --------------------
# -----COMPOSE--------
# --------------------

.PHONY: compose-build
compose-build:
	docker compose -f $(COMPOSE_FILE) build api worker

.PHONY: compose-up
compose-up: prepare-data
	docker compose -f $(COMPOSE_FILE) up -d postgres api

.PHONY: compose-down
compose-down:
	docker compose -f $(COMPOSE_FILE) down

.PHONY: compose-down-volumes
compose-down-volumes:
	docker compose -f $(COMPOSE_FILE) down -v

.PHONY: compose-logs
compose-logs:
	docker compose -f $(COMPOSE_FILE) logs -f

# --------------------
# -------DB-----------
# --------------------

.PHONY: db-up
db-up:
	docker compose -f $(COMPOSE_FILE) up -d postgres

.PHONY: db-revision
db-revision:
	@if [ -z "$(MSG)" ]; then \
		echo "MSG is required. Usage: make db-revision MSG='create jobs table'"; \
		exit 1; \
	fi
	$(MAKE) api-build
	docker compose -f $(COMPOSE_FILE) --profile tools run --rm migrate \
		alembic -c packages/sceneops-db/alembic.ini revision --autogenerate -m "$(MSG)"

.PHONY: db-migrate
db-migrate: api-build
	docker compose -f $(COMPOSE_FILE) --profile tools run --rm migrate

.PHONY: db-current
db-current:
	docker compose -f $(COMPOSE_FILE) --profile tools run --rm migrate \
		alembic -c packages/sceneops-db/alembic.ini current

.PHONY: db-history
db-history:
	docker compose -f $(COMPOSE_FILE) --profile tools run --rm migrate \
		alembic -c packages/sceneops-db/alembic.ini history

.PHONY: db-reset
db-reset:
	docker compose -f $(COMPOSE_FILE) down -v
	$(MAKE) db-up
	$(MAKE) db-migrate

# --------------------
# --------API---------
# --------------------

.PHONY: api-build
api-build:
	docker compose -f $(COMPOSE_FILE) build api

.PHONY: api-up
api-up: prepare-data
	docker compose -f $(COMPOSE_FILE) up -d postgres api

.PHONY: api-logs
api-logs:
	docker compose -f $(COMPOSE_FILE) logs -f api

.PHONY: api-shell
api-shell:
	docker compose -f $(COMPOSE_FILE) run --rm api sh

# --------------------
# -------WORKER-------
# --------------------

.PHONY: worker-build
worker-build:
	docker compose -f $(COMPOSE_FILE) build worker

.PHONY: worker-run-job
worker-run-job: prepare-data
	@if [ -z "$(JOB_ID)" ]; then \
		echo "JOB_ID is required. Usage: make worker-run-job JOB_ID=job-xxx"; \
		exit 1; \
	fi
	docker compose -f $(COMPOSE_FILE) run --rm worker \
		sceneops-worker jobs run \
			--job-id $(JOB_ID)

.PHONY: worker-shell
worker-shell:
	docker compose -f $(COMPOSE_FILE) run --rm worker sh

PIPELINE_RUN_ID ?=

.PHONY: worker-run-pipeline
worker-run-pipeline:
	@if [ -z "$(PIPELINE_RUN_ID)" ]; then \
		echo "PIPELINE_RUN_ID is required. Usage: make worker-run-pipeline PIPELINE_RUN_ID=pipe-xxx"; \
		exit 1; \
	fi
	docker compose -f $(COMPOSE_FILE) --profile worker run --rm worker \
		sceneops-worker pipelines run \
			--pipeline-run-id $(PIPELINE_RUN_ID)

# --------------------
# -------DEBUG--------
# --------------------

MAX_SCENES ?= 2
INGEST_MODE ?= upsert

MODEL_ID ?= centerpoint-mock
MODEL_VERSION ?= v0
RUN_ID ?= run-centerpoint-mock-001
EVALUATION_RUN_ID ?= eval-centerpoint-mock-001
MAX_SAMPLES ?= 20
MATCH_DISTANCE_M ?= 2.0

DATASET_ID ?= nuscenes
DATASET_VERSION ?= v1.0-mini

.PHONY: worker-ingest
worker-ingest: prepare-data
	@echo "INGESTING scene"
	docker compose -f $(COMPOSE_FILE) run --rm worker \
		sceneops-worker ingest nuscenes \
			--dataset-id $(DATASET_ID) \
			--dataset-version $(DATASET_VERSION) \
			--max-scenes $(MAX_SCENES) \
			--mode $(INGEST_MODE)

.PHONY: worker-predict-mock-detection
worker-predict-mock-detection: prepare-data
	@echo "mock detection"
	docker compose -f $(COMPOSE_FILE) run --rm worker \
		sceneops-worker predict mock-detection \
			--dataset-id $(DATASET_ID) \
			--dataset-version $(DATASET_VERSION) \
			--model-id $(MODEL_ID) \
			--model-version $(MODEL_VERSION) \
			--run-id $(RUN_ID) \
			--max-samples $(MAX_SAMPLES)

.PHONY: worker-evaluate-detection
worker-evaluate-detection: prepare-data
	@echo "evaluate detection"
	docker compose -f $(COMPOSE_FILE) run --rm worker \
		sceneops-worker evaluate detection \
			--dataset-id $(DATASET_ID) \
			--dataset-version $(DATASET_VERSION) \
			--inference-run-id $(RUN_ID) \
			--evaluation-run-id $(EVALUATION_RUN_ID) \
			--match-distance-m $(MATCH_DISTANCE_M)
