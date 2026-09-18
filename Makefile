COMPOSE_FILE ?= docker-compose.local.yml
ENV_FILE     ?= .env.local
COMPOSE      := docker compose -f $(COMPOSE_FILE) --env-file $(ENV_FILE)
API_HOST     ?= http://localhost:8000
API_PREFIX   ?= /api/v1
ALEMBIC_CONFIG ?= migrations/alembic.ini
POSTGRES_USER ?= sceneops
POSTGRES_DB   ?= sceneops

JOB_ID          ?=
PIPELINE_RUN_ID ?=
TASK_ID         ?=
MSG             ?=
ROS2_CMD        ?=
SCENE           ?=
RATE            ?=
DURATION        ?=
ROBOT_ID        ?=
RUN_ID          ?=
MCAP_URI        ?=

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
	@echo "  make local-up                 Start full local stack (idempotent: infra -> health -> MinIO buckets -> migrate -> API + workers)"
	@echo "  make e2e                      Run the default E2E subset (mock backend) -- NOT all E2E scripts, see 'E2E' below"
	@echo "  make local-down               Stop services, KEEP all data"
	@echo "  make status / make logs       Service status / follow logs"
	@echo ""
	@echo "Setup:"
	@echo "  make setup                    Install deps + pre-commit hooks"
	@echo "  make uv-sync / make uv-lock"
	@echo "  make install-hooks / make uninstall-hooks"
	@echo "  make check                    Run pre-commit on all files"
	@echo "  make lint / make format       Ruff check / format"
	@echo "  make test                     Run worker+api+core+analytics unit tests"
	@echo ""
	@echo "Local stack (see docs/local-development.md):"
	@echo "  make local-up                 Idempotent bootstrap, see Quick start"
	@echo "  make local-down               Stop everything, PRESERVE Postgres/Redis/MinIO data"
	@echo "  make local-reset              [DESTRUCTIVE] wipe all local data, rebuild clean"
	@echo "  make status                   Show service status"
	@echo "  make logs                     Follow logs for core services"
	@echo ""
	@echo "Docker Compose (raw escape hatches -- prefer the targets above):"
	@echo "  make compose-build / compose-build-no-cache"
	@echo "  make compose-logs / compose-ps"
	@echo ""
	@echo "MinIO (started by default as part of local-up):"
	@echo "  make minio-up / minio-down / minio-logs"
	@echo "  make minio-init                Idempotent bucket bootstrap (also run by local-up)"
	@echo "  make minio-console             Print MinIO API/console URLs"
	@echo "  make check-minio"
	@echo ""
	@echo "Database:"
	@echo "  make db-migrate                alembic upgrade head (also run by local-up)"
	@echo "  make migrate-build             Force-rebuild the migrate image (after changing migration deps)"
	@echo "  make db-revision MSG='create table'"
	@echo "  make db-current / make db-history"
	@echo "  make db-reset                  [DESTRUCTIVE] wipe Postgres only, then re-migrate"
	@echo "  make db-shell"
	@echo ""
	@echo "API:"
	@echo "  make api-logs / api-shell / api-health / api-openapi"
	@echo ""
	@echo "Worker:"
	@echo "  make worker-logs / worker-shell / worker-python / worker-imports / worker-cli"
	@echo "  make worker-run-job JOB_ID=job-xxx"
	@echo "  make worker-run-pipeline PIPELINE_RUN_ID=pipe-xxx"
	@echo "  make worker-run-pipeline-task PIPELINE_RUN_ID=pipe-xxx TASK_ID=task-xxx"
	@echo "  make worker-register-robot-run ROBOT_ID=.. RUN_ID=.. MCAP_URI=.."
	@echo ""
	@echo "Inference server (opt-in, not part of local-up):"
	@echo "  make inference-local-build / inference-local-up / inference-local-down / inference-local-logs   (CPU)"
	@echo "  make inference-gpu-build / inference-gpu-up / inference-gpu-down / inference-gpu-logs           (GPU)"
	@echo "  make check-inference-server / check-inference-server-ready"
	@echo ""
	@echo "Airflow (opt-in pipeline execution backend, not part of local-up):"
	@echo "  make airflow-up / airflow-down / airflow-logs"
	@echo ""
	@echo "Checks:"
	@echo "  make check-env / check-imports / check-celery / check-minio"
	@echo ""
	@echo "Fixtures:"
	@echo "  make register-nuscenes-dataset"
	@echo ""
	@echo "E2E (make e2e runs only the first 4; see docs/local-development.md for why):"
	@echo "  make e2e                                  api-smoke + dataset-ingestion + detection-evaluation + pipeline-contracts"
	@echo "  make e2e-api-smoke"
	@echo "  make e2e-dataset-ingestion"
	@echo "  make e2e-detection-evaluation"
	@echo "  make e2e-pipeline-contracts"
	@echo "  make e2e-raw-log-scene-building"
	@echo "  make e2e-scenario-curation                 Prints scenario_set_id and pipeline_run_id"
	@echo "  make e2e-episode-building"
	@echo "  make e2e-analytics-export"
	@echo "  make e2e-reliability                       Asserts execution-key dedup/force semantics"
	@echo "  make e2e-airflow-pipeline                  Requires: make airflow-up"
	@echo "  make e2e-detection-evaluation-real          = e2e-detection-evaluation-groundingdino; requires inference server"
	@echo "  make e2e-robot-can-replay                  nuScenes CAN -> ROS2 -> rosbag2/MCAP -> RobotState/Mission"
	@echo "  make compare-detection PIPELINE_RUN_ID=pipe-xxx"
	@echo ""
	@echo "ROS2 (Jazzy dev sandbox):"
	@echo "  make ros2-up / ros2-down / ros2-shell / ros2-check / ros2-logs"
	@echo "  make ros2-run ROS2_CMD='ros2 topic list'"
	@echo "  make ros2-can-replay SCENE=scene-0061 RATE=1.0"
	@echo "  make ros2-can-replay-record SCENE=scene-0061 RATE=5.0   (records to data/raw/rosbag/<scene>)"
	@echo ""
	@echo "Debug:"
	@echo "  make show-runs"
	@echo "  make show-pipeline PIPELINE_RUN_ID=pipe-xxx"
	@echo "  make show-job-events JOB_ID=job-xxx"
	@echo "  make tail-worker-logs"
	@echo ""
	@echo "Cleanup:"
	@echo "  make prepare-data / clean-artifacts / clean-python"
	@echo "  (destructive local-state reset is 'make local-reset', see Local stack above)"

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
