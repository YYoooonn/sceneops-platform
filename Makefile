COMPOSE_FILE ?= docker-compose.local.yml
API_HOST     ?= http://localhost:8000
API_PREFIX   ?= /api/v1
ALEMBIC_CONFIG ?= migrations/alembic.ini

JOB_ID          ?=
PIPELINE_RUN_ID ?=
TASK_ID         ?=
MSG             ?=
ROS2_CMD        ?=

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
	@echo "ROS2 (Jazzy dev sandbox):"
	@echo "  make ros2-up"
	@echo "  make ros2-down"
	@echo "  make ros2-shell"
	@echo "  make ros2-run ROS2_CMD='ros2 topic list'"
	@echo "  make ros2-check"
	@echo "  make ros2-logs"
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
# Includes (see makefiles/)
# --------------------

include makefiles/setup.mk
include makefiles/cleanup.mk
include makefiles/local.mk
include makefiles/airflow.mk
include makefiles/ros2.mk
include makefiles/compose.mk
include makefiles/db.mk
include makefiles/api.mk
include makefiles/worker.mk
include makefiles/minio.mk
include makefiles/inference.mk
include makefiles/checks.mk
include makefiles/fixtures.mk
include makefiles/e2e.mk
include makefiles/debug.mk
