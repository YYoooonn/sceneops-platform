COMPOSE_FILE ?= docker-compose.local.yml
API_PREFIX ?= /api/v1
ALEMBIC_CONFIG ?= migrations/alembic.ini

JOB_ID ?=
PIPELINE_RUN_ID ?=
MSG ?=

.DEFAULT_GOAL := help

.PHONY: help
help:
	@echo "SceneOps Platform"
	@echo ""
	@echo "Setup:"
	@echo "  make uv-sync"
	@echo "  make uv-lock"
	@echo "  make install-hooks"
	@echo "  make check"
	@echo ""
	@echo "Docker Compose:"
	@echo "  make compose-build"
	@echo "  make compose-build-no-cache"
	@echo "  make compose-up"
	@echo "  make compose-down"
	@echo "  make compose-down-volumes"
	@echo "  make compose-logs"
	@echo "  make compose-ps"
	@echo ""
	@echo "MinIO (object storage):"
	@echo "  make minio-up       Start MinIO + create bucket"
	@echo "  make minio-down     Stop MinIO"
	@echo "  make check-minio    Verify S3ArtifactStore against MinIO"
	@echo ""
	@echo "Database:"
	@echo "  make db-migrate"
	@echo "  make db-revision MSG='create jobs table'"
	@echo "  make db-current"
	@echo "  make db-history"
	@echo "  make db-reset"
	@echo ""
	@echo "API:"
	@echo "  make api-logs"
	@echo "  make api-shell"
	@echo ""
	@echo "Worker:"
	@echo "  make worker-logs"
	@echo "  make worker-shell"
	@echo "  make worker-cli"
	@echo "  make worker-run-job JOB_ID=job-xxx"
	@echo "  make worker-run-pipeline PIPELINE_RUN_ID=pipe-xxx"
	@echo ""
	@echo "Checks:"
	@echo "  make check-env"
	@echo "  make check-imports"
	@echo "  make check-celery"
	@echo "  make check-minio"
	@echo ""
	@echo "E2E:"
	@echo "  make e2e-mock-celery"
	@echo "  make e2e-onnx-celery"
	@echo ""
	@echo "Debug:"
	@echo "  make show-runs"
	@echo "  make show-pipeline PIPELINE_RUN_ID=pipe-xxx"
	@echo "  make show-job-events JOB_ID=job-xxx"
	@echo "  make reset-local"

# --------------------
# Setup / Quality
# --------------------
.PHONY: setup-dev
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

.PHONY: lint
lint:
	uv run ruff check apps/ packages/

.PHONY: prepare-data
prepare-data:
	mkdir -p data/raw data/datasets data/runs data/models

.PHONY: clean-artifacts
clean-artifacts:
	rm -rf data/datasets/*
	rm -rf data/runs/*
	rm -rf data/models/*
	$(MAKE) prepare-data

# --------------------
# Docker Compose
# --------------------

.PHONY: compose-build
compose-build:
	uv lock
	docker compose -f $(COMPOSE_FILE) build api worker-pipeline

.PHONY: compose-build-no-cache
compose-build-no-cache:
	uv lock
	docker compose -f $(COMPOSE_FILE) build --no-cache api worker-pipeline worker-cli

.PHONY: compose-up
compose-up: prepare-data
	docker compose -f $(COMPOSE_FILE) up -d postgres redis api worker-pipeline worker-jobs

.PHONY: compose-down
compose-down:
	docker compose -f $(COMPOSE_FILE) down postgres redis api worker-pipeline worker-jobs

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
	docker compose -f $(COMPOSE_FILE) up -d postgres
	$(MAKE) db-migrate

# --------------------
# API
# --------------------

.PHONY: api-logs
api-logs:
	docker compose -f $(COMPOSE_FILE) logs -f api

.PHONY: api-shell
api-shell:
	docker compose -f $(COMPOSE_FILE) exec api sh

# --------------------
# Worker
# --------------------

.PHONY: worker-logs
worker-logs:
	docker compose -f $(COMPOSE_FILE) logs -f worker-pipeline worker-jobs

.PHONY: worker-shell
worker-shell:
	docker compose -f $(COMPOSE_FILE) --profile debug run --rm worker-cli sh

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
		jobs run --job-id $(JOB_ID)

.PHONY: worker-run-pipeline
worker-run-pipeline:
	@if [ -z "$(PIPELINE_RUN_ID)" ]; then \
		echo "PIPELINE_RUN_ID is required. Usage: make worker-run-pipeline PIPELINE_RUN_ID=pipe-xxx"; \
		exit 1; \
	fi
	docker compose -f $(COMPOSE_FILE) --profile debug run --rm worker-cli \
		pipelines run --pipeline-run-id $(PIPELINE_RUN_ID)

# --------------------
# MinIO
# --------------------

.PHONY: minio-up
minio-up:
	docker compose -f $(COMPOSE_FILE) --profile minio up -d minio minio-init

.PHONY: minio-down
minio-down:
	docker compose -f $(COMPOSE_FILE) --profile minio stop minio
	docker compose -f $(COMPOSE_FILE) --profile minio rm -f minio minio-init

# --------------------
# Inference Server (GPU)
# --------------------

.PHONY: inference-server-build
inference-server-build:
	docker compose -f $(COMPOSE_FILE) --profile gpu build inference-server

.PHONY: inference-server-up
inference-server-up:
	docker compose -f $(COMPOSE_FILE) --profile gpu up -d inference-server

.PHONY: inference-server-down
inference-server-down:
	docker compose -f $(COMPOSE_FILE) --profile gpu stop inference-server
	docker compose -f $(COMPOSE_FILE) --profile gpu rm -f inference-server

.PHONY: inference-server-logs
inference-server-logs:
	docker compose -f $(COMPOSE_FILE) --profile gpu logs -f inference-server

.PHONY: check-inference-server
check-inference-server:
	curl -sf http://localhost:8001/healthz | python3 -m json.tool

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
# Prepare
# --------------------

.PHONY: register-nuscenes-dataset
register-nuscenes-dataset:
	chmod +x scripts/fixtures/register_nuscenes_dataset.sh
	API_PREFIX=$(API_PREFIX) scripts/fixtures/register_nuscenes_dataset.sh

# --------------------
# E2E
# --------------------

.PHONY: e2e-dataset-ingest
e2e-dataset-ingest:
	chmod +x scripts/e2e/e2e_ingestion_pipeline_celery.sh
	API_PREFIX=$(API_PREFIX) scripts/e2e/e2e_ingestion_pipeline_celery.sh

.PHONY: e2e-mock-celery
e2e-mock-celery:
	chmod +x scripts/e2e/e2e_mock_pipeline_celery.sh
	API_PREFIX=$(API_PREFIX) scripts/e2e/e2e_mock_pipeline_celery.sh

.PHONY: e2e-onnx-celery
e2e-onnx-celery:
	chmod +x scripts/e2e/e2e_onnx_pipeline_celery.sh
	API_PREFIX=$(API_PREFIX) scripts/e2e/e2e_onnx_pipeline_celery.sh

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
	API_PREFIX=$(API_PREFIX) PIPELINE_RUN_ID=$(PIPELINE_RUN_ID) scripts/debug/show_pipeline.sh

.PHONY: show-job-events
show-job-events:
	@if [ -z "$(JOB_ID)" ]; then \
		echo "JOB_ID is required. Usage: make show-job-events JOB_ID=job-xxx"; \
		exit 1; \
	fi
	chmod +x scripts/debug/show_job_events.sh
	API_PREFIX=$(API_PREFIX) JOB_ID=$(JOB_ID) scripts/debug/show_job_events.sh

.PHONY: reset-local
reset-local:
	scripts/dev/reset_local_state.sh
