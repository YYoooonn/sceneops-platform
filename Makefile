COMPOSE_FILE ?= docker-compose.local.yml
API_HOST     ?= http://localhost:8000
API_PREFIX   ?= /api/v1
ALEMBIC_CONFIG ?= migrations/alembic.ini

JOB_ID          ?=
PIPELINE_RUN_ID ?=
TASK_ID         ?=
MSG             ?=

MODEL_ID        ?= dummy-detector
MODEL_VERSION   ?= v1
DATASET_ID      ?= nuscenes
DATASET_VERSION ?= v1.0-mini

GDINO_MODEL_ID      ?= grounding-dino
GDINO_MODEL_VERSION ?= tiny
INFERENCE_ENDPOINT_URL ?= http://sceneops-inference:8001

.DEFAULT_GOAL := help

# --------------------
# Help
# --------------------

.PHONY: help
help:
	@echo "SceneOps Platform"
	@echo ""
	@echo "Quick start:"
	@echo "  make setup                    Install deps, hooks"
	@echo "  make local-up                 Start full local stack (MinIO + DB migrate + API + workers)"
	@echo "  make e2e                      Run all E2E tests"
	@echo "  make local-down               Stop all services"
	@echo ""
	@echo "Setup:"
	@echo "  make setup"
	@echo "  make uv-sync"
	@echo "  make uv-lock"
	@echo "  make install-hooks"
	@echo "  make check"
	@echo "  make lint"
	@echo ""
	@echo "Local stack:"
	@echo "  make local-up                 MinIO + migrate + API + workers"
	@echo "  make local-down               Stop everything"
	@echo "  make local-reset              Wipe volumes, restart from scratch"
	@echo "  make local-logs               Follow logs for main services"
	@echo "  make local-ps                 Show service status"
	@echo ""
	@echo "Docker Compose (raw):"
	@echo "  make compose-build"
	@echo "  make compose-build-no-cache"
	@echo "  make compose-up"
	@echo "  make compose-down"
	@echo "  make compose-down-volumes"
	@echo "  make compose-logs"
	@echo "  make compose-ps"
	@echo ""
	@echo "MinIO:"
	@echo "  make minio-up"
	@echo "  make minio-down"
	@echo "  make minio-logs"
	@echo "  make minio-console"
	@echo "  make check-minio"
	@echo ""
	@echo "Database:"
	@echo "  make db-migrate"
	@echo "  make db-revision MSG='create table'"
	@echo "  make db-current"
	@echo "  make db-history"
	@echo "  make db-reset"
	@echo "  make db-shell"
	@echo ""
	@echo "API:"
	@echo "  make api-logs"
	@echo "  make api-shell"
	@echo "  make api-health"
	@echo "  make api-openapi"
	@echo ""
	@echo "Worker:"
	@echo "  make worker-logs"
	@echo "  make worker-shell"
	@echo "  make worker-python"
	@echo "  make worker-imports"
	@echo "  make worker-cli"
	@echo "  make worker-run-job JOB_ID=job-xxx"
	@echo "  make worker-run-pipeline PIPELINE_RUN_ID=pipe-xxx"
	@echo ""
	@echo "Inference (local CPU):"
	@echo "  make inference-local-build"
	@echo "  make inference-local-up"
	@echo "  make inference-local-down"
	@echo "  make inference-local-logs"
	@echo "  make check-inference-server"
	@echo "  make check-inference-server-ready"
	@echo ""
	@echo "Inference (GPU):"
	@echo "  make inference-gpu-build"
	@echo "  make inference-gpu-up"
	@echo "  make inference-gpu-down"
	@echo "  make inference-gpu-logs"
	@echo ""
	@echo "Checks:"
	@echo "  make check-env"
	@echo "  make check-imports"
	@echo "  make check-celery"
	@echo "  make check-minio"
	@echo ""
	@echo "Fixtures:"
	@echo "  make register-nuscenes-dataset"
	@echo ""
	@echo "E2E:"
	@echo "  make e2e                                  Run all E2E tests (mock backend)"
	@echo "  make e2e-api-smoke"
	@echo "  make e2e-dataset-ingestion"
	@echo "  make e2e-detection-evaluation"
	@echo "  make e2e-pipeline-contracts"
	@echo "  make e2e-detection-evaluation-real"
	@echo ""
	@echo "Debug:"
	@echo "  make show-runs"
	@echo "  make show-pipeline PIPELINE_RUN_ID=pipe-xxx"
	@echo "  make show-job-events JOB_ID=job-xxx"
	@echo "  make tail-worker-logs"
	@echo ""
	@echo "Cleanup:"
	@echo "  make prepare-data"
	@echo "  make clean-artifacts"
	@echo "  make clean-python"
	@echo "  make reset-local"

# --------------------
# Setup / Quality
# --------------------

.PHONY: setup
setup:
	chmod +x scripts/setup_dev.sh
	./scripts/setup_dev.sh

.PHONY: uv-sync
uv-sync:
	uv sync --all-packages --group dev

.PHONY: uv-lock
uv-lock:
	uv lock

.PHONY: install-hooks
install-hooks:
	uv run pre-commit install

.PHONY: uninstall-hooks
uninstall-hooks:
	uv run pre-commit uninstall

.PHONY: check
check:
	uv run pre-commit run --all-files

.PHONY: test
test:
	uv run pytest apps/worker/tests/ apps/api/tests/ -v

.PHONY: lint
lint:
	uv run ruff check apps/ packages/

.PHONY: format
format:
	uv run ruff format apps/ packages/

# --------------------
# Cleanup
# --------------------

.PHONY: prepare-data
prepare-data:
	mkdir -p data/raw data/datasets data/runs data/models data/artifacts cache/hf

.PHONY: clean-artifacts
clean-artifacts:
	rm -rf data/datasets/* data/runs/* data/models/* data/artifacts/*
	$(MAKE) prepare-data

.PHONY: clean-python
clean-python:
	find . -name "__pycache__" -type d -prune -exec rm -rf {} + 2>/dev/null || true
	find . -name ".pytest_cache" -type d -prune -exec rm -rf {} + 2>/dev/null || true
	find . -name ".mypy_cache" -type d -prune -exec rm -rf {} + 2>/dev/null || true
	find . -name ".ruff_cache" -type d -prune -exec rm -rf {} + 2>/dev/null || true

.PHONY: reset-local
reset-local:
	chmod +x scripts/dev/reset_local_state.sh
	scripts/dev/reset_local_state.sh

# --------------------
# Local stack
# --------------------

.PHONY: local-up
local-up: prepare-data minio-up
	docker compose -f $(COMPOSE_FILE) up -d postgres redis
	docker compose -f $(COMPOSE_FILE) up -d api worker-pipeline worker-jobs

.PHONY: local-down
local-down: minio-down
	docker compose -f $(COMPOSE_FILE) down api worker-pipeline worker-jobs
	docker compose -f $(COMPOSE_FILE) down postgres redis

.PHONY: local-reset
local-reset:
	docker compose -f $(COMPOSE_FILE) --profile minio down -v
	$(MAKE) clean-artifacts
	$(MAKE) local-up

.PHONY: local-logs
local-logs:
	docker compose -f $(COMPOSE_FILE) logs -f postgres redis minio api worker-pipeline worker-jobs

.PHONY: local-ps
local-ps:
	docker compose -f $(COMPOSE_FILE) --profile worker --profile minio ps

# --------------------
# Docker Compose (raw)
# --------------------

.PHONY: compose-build
compose-build:
	uv lock
	docker compose -f $(COMPOSE_FILE) build api worker-pipeline

.PHONY: compose-build-no-cache
compose-build-no-cache:
	uv lock
	docker compose -f $(COMPOSE_FILE) build --no-cache api worker-pipeline

.PHONY: compose-up
compose-up: prepare-data minio-up
	docker compose -f $(COMPOSE_FILE) up -d postgres redis api worker-pipeline worker-jobs

.PHONY: compose-down
compose-down:
	docker compose -f $(COMPOSE_FILE) down

.PHONY: compose-down-volumes
compose-down-volumes:
	docker compose -f $(COMPOSE_FILE) down -v

.PHONY: compose-logs
compose-logs:
	docker compose -f $(COMPOSE_FILE) logs -f postgres redis api worker-pipeline worker-jobs

.PHONY: compose-ps
compose-ps:
	docker compose -f $(COMPOSE_FILE) ps

# --------------------
# Database
# --------------------

.PHONY: migrate-build
migrate-build:
	uv lock
	docker compose -f $(COMPOSE_FILE) build migrate

.PHONY: db-migrate
db-migrate: migrate-build
	docker compose -f $(COMPOSE_FILE) up -d postgres
	docker compose -f $(COMPOSE_FILE) --profile tools run --rm migrate

.PHONY: db-revision
db-revision: migrate-build
	@if [ -z "$(MSG)" ]; then \
		echo "MSG is required. Usage: make db-revision MSG='create table'"; \
		exit 1; \
	fi
	docker compose -f $(COMPOSE_FILE) --profile tools run --rm migrate \
		alembic -c $(ALEMBIC_CONFIG) revision --autogenerate -m "$(MSG)"

.PHONY: db-current
db-current:
	docker compose -f $(COMPOSE_FILE) --profile tools run --rm migrate \
		alembic -c $(ALEMBIC_CONFIG) current

.PHONY: db-history
db-history:
	docker compose -f $(COMPOSE_FILE) --profile tools run --rm migrate \
		alembic -c $(ALEMBIC_CONFIG) history

.PHONY: db-reset
db-reset:
	docker compose -f $(COMPOSE_FILE) down -v
	$(MAKE) db-migrate

.PHONY: db-shell
db-shell:
	docker compose -f $(COMPOSE_FILE) exec postgres psql -U sceneops -d sceneops

# --------------------
# API
# --------------------

.PHONY: api-logs
api-logs:
	docker compose -f $(COMPOSE_FILE) logs -f api

.PHONY: api-shell
api-shell:
	docker compose -f $(COMPOSE_FILE) exec api sh

.PHONY: api-health
api-health:
	curl -sf $(API_HOST)/health | python3 -m json.tool

.PHONY: api-openapi
api-openapi:
	docker compose -f $(COMPOSE_FILE) exec api python -c \
		"from app.main import app; app.openapi(); print('api openapi ok')"

# --------------------
# Worker
# --------------------

.PHONY: worker-logs
worker-logs:
	docker compose -f $(COMPOSE_FILE) logs -f worker-pipeline worker-jobs

.PHONY: worker-shell
worker-shell:
	docker compose -f $(COMPOSE_FILE) --profile debug run --rm --entrypoint sh worker-cli

.PHONY: worker-python
worker-python:
	docker compose -f $(COMPOSE_FILE) --profile debug run --rm --entrypoint python worker-cli

.PHONY: worker-imports
worker-imports:
	docker compose -f $(COMPOSE_FILE) --profile debug run --rm --entrypoint python worker-cli \
		-c "import sceneops_worker; from sceneops_worker.jobs.registry import create_default_job_handler_registry; print('worker import ok'); print(create_default_job_handler_registry())"

.PHONY: worker-cli
worker-cli:
	docker compose -f $(COMPOSE_FILE) --profile debug run --rm worker-cli

.PHONY: worker-run-job
worker-run-job:
	@if [ -z "$(JOB_ID)" ]; then \
		echo "JOB_ID is required. Usage: make worker-run-job JOB_ID=job-xxx"; \
		exit 1; \
	fi
	docker compose -f $(COMPOSE_FILE) --profile debug run --rm worker-cli \
		sceneops-worker jobs run --job-id $(JOB_ID)

.PHONY: worker-run-pipeline
worker-run-pipeline:
	@if [ -z "$(PIPELINE_RUN_ID)" ]; then \
		echo "PIPELINE_RUN_ID is required. Usage: make worker-run-pipeline PIPELINE_RUN_ID=pipe-xxx"; \
		exit 1; \
	fi
	docker compose -f $(COMPOSE_FILE) --profile debug run --rm worker-cli \
		sceneops-worker pipelines run --pipeline-run-id $(PIPELINE_RUN_ID)


.PHONY: worker-run-pipeline-task
worker-run-pipeline-task:
	@if [ -z "$(PIPELINE_RUN_ID)" ]; then \
		echo "PIPELINE_RUN_ID is required. Usage: make worker-run-pipeline PIPELINE_RUN_ID=pipe-xxx"; \
		exit 1; \
	fi
	docker compose -f $(COMPOSE_FILE) --profile debug run --rm worker-cli \
		sceneops-worker run-pipeline-task --pipeline-run-id $(PIPELINE_RUN_ID) --task-id $(TASK_ID)

# --------------------
# MinIO
# --------------------

.PHONY: minio-up
minio-up:
	docker compose -f $(COMPOSE_FILE) --profile minio up -d minio

.PHONY: minio-down
minio-down:
	docker compose -f $(COMPOSE_FILE) --profile minio stop minio

minio-migrate:
	docker compose -f $(COMPOSE_FILE) --profile minio run --rm minio-init

.PHONY: minio-logs
minio-logs:
	docker compose -f $(COMPOSE_FILE) --profile minio logs -f minio

.PHONY: minio-console
minio-console:
	@echo "MinIO API:     http://localhost:9000"
	@echo "MinIO Console: http://localhost:9001"

# --------------------
# Inference (local CPU)
# --------------------

.PHONY: inference-local-build
inference-local-build:
	docker compose -f $(COMPOSE_FILE) --profile inference build inference-server-local

.PHONY: inference-local-up
inference-local-up:
	mkdir -p cache/hf
	docker compose -f $(COMPOSE_FILE) --profile inference up -d inference-server-local

.PHONY: inference-local-down
inference-local-down:
	docker compose -f $(COMPOSE_FILE) --profile inference stop inference-server-local
	docker compose -f $(COMPOSE_FILE) --profile inference rm -f inference-server-local

.PHONY: inference-local-logs
inference-local-logs:
	docker compose -f $(COMPOSE_FILE) --profile inference logs -f inference-server-local

.PHONY: check-inference-server
check-inference-server:
	curl -sf http://localhost:8001/healthz | python3 -m json.tool

.PHONY: check-inference-server-ready
check-inference-server-ready:
	curl -sf http://localhost:8001/readyz | python3 -m json.tool

# --------------------
# Inference (GPU)
# --------------------

.PHONY: inference-gpu-build
inference-gpu-build:
	docker compose -f $(COMPOSE_FILE) --profile gpu build inference-server

.PHONY: inference-gpu-up
inference-gpu-up:
	mkdir -p cache/hf
	docker compose -f $(COMPOSE_FILE) --profile gpu up -d inference-server

.PHONY: inference-gpu-down
inference-gpu-down:
	docker compose -f $(COMPOSE_FILE) --profile gpu stop inference-server
	docker compose -f $(COMPOSE_FILE) --profile gpu rm -f inference-server

.PHONY: inference-gpu-logs
inference-gpu-logs:
	docker compose -f $(COMPOSE_FILE) --profile gpu logs -f inference-server

# --------------------
# Checks
# --------------------

.PHONY: check-env
check-env:
	chmod +x scripts/checks/check_env.sh
	scripts/checks/check_env.sh

.PHONY: check-imports
check-imports:
	chmod +x scripts/checks/check_python_imports.sh
	scripts/checks/check_python_imports.sh

.PHONY: check-celery
check-celery:
	chmod +x scripts/checks/check_celery_broker.sh
	scripts/checks/check_celery_broker.sh

.PHONY: check-minio
check-minio:
	chmod +x scripts/checks/check_minio.sh
	scripts/checks/check_minio.sh

# --------------------
# Fixtures
# --------------------

.PHONY: register-nuscenes-dataset
register-nuscenes-dataset:
	chmod +x scripts/fixtures/register_nuscenes_dataset.sh
	API_PREFIX=$(API_PREFIX) scripts/fixtures/register_nuscenes_dataset.sh

# --------------------
# E2E
# --------------------

.PHONY: e2e-api-smoke
e2e-api-smoke:
	chmod +x scripts/e2e/e2e_api_smoke.sh
	API_PREFIX=$(API_PREFIX) scripts/e2e/e2e_api_smoke.sh

.PHONY: e2e-dataset-ingestion
e2e-dataset-ingestion:
	chmod +x scripts/e2e/e2e_dataset_scene_ingestion.sh
	API_PREFIX=$(API_PREFIX) scripts/e2e/e2e_dataset_scene_ingestion.sh

.PHONY: e2e-raw-log-scene-building
e2e-raw-log-scene-building:
	chmod +x scripts/e2e/e2e_raw_log_scene_building.sh
	API_PREFIX=$(API_PREFIX) scripts/e2e/e2e_raw_log_scene_building.sh

.PHONY: e2e-detection-evaluation
e2e-detection-evaluation:
	chmod +x scripts/e2e/e2e_detection_evaluation.sh
	API_PREFIX=$(API_PREFIX) \
	DATASET_ID=$(DATASET_ID) DATASET_VERSION=$(DATASET_VERSION) \
	MODEL_ID=$(MODEL_ID) MODEL_VERSION=$(MODEL_VERSION) \
	scripts/e2e/e2e_detection_evaluation.sh

.PHONY: e2e-pipeline-contracts
e2e-pipeline-contracts:
	chmod +x scripts/e2e/e2e_pipeline_contracts.sh
	API_PREFIX=$(API_PREFIX) \
	DATASET_ID=$(DATASET_ID) DATASET_VERSION=$(DATASET_VERSION) \
	scripts/e2e/e2e_pipeline_contracts.sh

.PHONY: e2e-detection-evaluation-real
e2e-detection-evaluation-real: e2e-detection-evaluation-groundingdino

.PHONY: e2e-detection-evaluation-groundingdino
e2e-detection-evaluation-groundingdino:
	chmod +x scripts/e2e/e2e_detection_evaluation_groundingdino.sh
	API_PREFIX=$(API_PREFIX) \
	DATASET_ID=$(DATASET_ID) DATASET_VERSION=$(DATASET_VERSION) \
	MODEL_ID=$(GDINO_MODEL_ID) MODEL_VERSION=$(GDINO_MODEL_VERSION) \
	INFERENCE_ENDPOINT_URL=$(INFERENCE_ENDPOINT_URL) \
	SCENARIO_SET_ID=$(SCENARIO_SET_ID) \
	SCENARIO_CURATION_PIPELINE_RUN_ID=$(SCENARIO_CURATION_PIPELINE_RUN_ID) \
	PIPELINE_RUN_ID=$(PIPELINE_RUN_ID) \
	scripts/e2e/e2e_detection_evaluation_groundingdino.sh

.PHONY: e2e-scenario-curation
e2e-scenario-curation:
	chmod +x scripts/e2e/e2e_scenario_curation.sh
	API_PREFIX=$(API_PREFIX) \
	DATASET_ID=$(DATASET_ID) DATASET_VERSION=$(DATASET_VERSION) \
	scripts/e2e/e2e_scenario_curation.sh

.PHONY: e2e
e2e: e2e-api-smoke e2e-dataset-ingestion e2e-detection-evaluation e2e-pipeline-contracts

# --------------------
# Debug
# --------------------

.PHONY: show-runs
show-runs:
	chmod +x scripts/debug/show_runs.sh
	API_PREFIX=$(API_PREFIX) scripts/debug/show_runs.sh

.PHONY: show-pipeline
show-pipeline:
	@if [ -z "$(PIPELINE_RUN_ID)" ]; then \
		echo "PIPELINE_RUN_ID is required. Usage: make show-pipeline PIPELINE_RUN_ID=pipe-xxx"; \
		exit 1; \
	fi
	chmod +x scripts/debug/show_pipeline.sh
	API_PREFIX=$(API_PREFIX) PIPELINE_RUN_ID=$(PIPELINE_RUN_ID) scripts/debug/show_pipeline.sh

.PHONY: show-job-events
show-job-events:
	@if [ -z "$(JOB_ID)" ]; then \
		echo "JOB_ID is required. Usage: make show-job-events JOB_ID=job-xxx"; \
		exit 1; \
	fi
	chmod +x scripts/debug/show_job_events.sh
	API_PREFIX=$(API_PREFIX) JOB_ID=$(JOB_ID) scripts/debug/show_job_events.sh

.PHONY: tail-worker-logs
tail-worker-logs:
	chmod +x scripts/debug/tail_worker_logs.sh
	scripts/debug/tail_worker_logs.sh

.PHONY: compare-detection
compare-detection:
	@if [ -z "$(PIPELINE_RUN_ID)" ]; then \
		echo "PIPELINE_RUN_ID is required. Usage: make show-pipeline PIPELINE_RUN_ID=pipe-xxx"; \
		exit 1; \
	fi
	chmod +x scripts/debug/compare_detection_run.sh
	API_PREFIX=$(API_PREFIX) PIPELINE_RUN_ID=$(PIPELINE_RUN_ID) scripts/debug/compare_detection_run.sh
