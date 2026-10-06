ENV_FILE     ?= .env.local
COMPOSE      := docker compose --env-file $(ENV_FILE)
# API_HOST -- used only by the debug/status targets below (api-health,
# show-pipeline, show-job-events, worker-cli helpers). Every E2E/smoke/verify
# script-facing target instead uses API_BASE_URL -- one authoritative name
# for the same concept on that surface, no Makefile-side translation needed.
# The two are not merged because they serve genuinely separate target groups.
API_HOST     ?= http://localhost:8000
API_BASE_URL ?= http://localhost:8000
API_PREFIX   ?= /api/v1
ALEMBIC_CONFIG ?= migrations/alembic.ini
POSTGRES_USER ?= sceneops
POSTGRES_DB   ?= sceneops
POSTGRES_PASSWORD ?= sceneops

# Shared local MinIO defaults -- the single source of truth the host-side
# integration / infrastructure test targets pass to their pytest runs, instead
# of each hardcoding its own copy of the same literals. Overridable exactly
# like the POSTGRES_* vars above.
MINIO_ROOT_USER     ?= minioadmin
MINIO_ROOT_PASSWORD ?= minioadmin
MINIO_BUCKET        ?= sceneops

JOB_ID          ?=
PIPELINE_RUN_ID ?=
TASK_ID         ?=
MSG             ?=
ROS2_CMD        ?=

# Journey selection -- the only user-facing variables of the E2E / baseline
# surface (see makefiles/e2e.mk). SCENE: the nuScenes scene (default
# scene-0061). RATE: streaming replay rate (default: the fixture's replay
# definition). BASELINE_ID: run a journey on a named canonical baseline (default:
# a unique one per run; streaming equivalence reads the persistent ref-<selection>
# baseline). BACKEND / MAX_SAMPLES: the detection backend and sample cap of the
# Scene ML journey.
SCENE           ?=
RATE            ?=
BASELINE_ID     ?=
BACKEND         ?=
MAX_SAMPLES     ?=

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
	@echo "  make local-up                 Start the local stack (idempotent: infra -> health -> MinIO buckets -> migrate -> API + workers)"
	@echo "  make test                     All infrastructure-independent unit tests"
	@echo "  make canonical-bootstrap      Build the L1/L2 baseline (RobotRun -> Scenes -> Episodes) on the running stack"
	@echo "  make e2e-cleanroom            THE full-platform acceptance (fresh state -> baseline -> both L3 journeys)"
	@echo "  make local-down               Stop services, KEEP all data"
	@echo "  make status / make logs       Service status / follow logs"
	@echo ""
	@echo "=================================================================="
	@echo "Tests (docs/development/test-matrix.md):"
	@echo "=================================================================="
	@echo "  make test                          Unit suites (worker, api, inference-server, analytics, core, integrations, streaming) -- no infra"
	@echo "  make test-integration              Real Postgres/MinIO: sceneops-db/storage + registrars + acquisition reconciliation + recording Scene/Episode verticals -- needs local-up"
	@echo "  make test-infrastructure           Pipeline contracts on the live stack: the four-pipeline surface, dedup/force/convergence/"
	@echo "                                     replacement/blocked resumption/failure recovery/concurrent registration, Celery, MinIO selective reads"
	@echo "                                     (builds on canonical-bootstrap)"
	@echo "  make test-infrastructure-airflow   The same pipelines through the Airflow per-task DAGs (needs airflow-up + api on the airflow backend)"
	@echo "  make test-recovery                 Acquisition-recovery fault injection and the full-lifecycle acceptance: real Postgres/MinIO + a throwaway Redis and Celery workers (Docker; needs local-up)"
	@echo "  make acquisition-test              tools/dataset-acquisition tests (isolated venv)"
	@echo "  make lerobot-test                  LeRobot adapter / container-entrypoint tests (isolated venv)"
	@echo "  make ros2-test                     Bridge + capture tests (ros2 image; needs streaming-up)"
	@echo "  make smoke-api / smoke-streaming   Transport/liveness only -- never create domain data"
	@echo ""
	@echo "=================================================================="
	@echo "E2E journeys -- exactly five (containers + FastAPI; host needs docker compose, curl, jq):"
	@echo "=================================================================="
	@echo "  make e2e-batch-canonical [SCENE=scene-0061]      dataset fixture -> batch MCAP -> RobotRun -> Scenes -> Episodes"
	@echo "  make e2e-streaming-equivalence [SCENE=.. RATE=..] the locked reference MCAP via the batch baseline and via replay -> ROS 2 -> Kafka -> capture: equivalent"
	@echo "  make e2e-scene-ml [SCENE=scene-0061]             Scenes -> labels -> sample views -> ScenarioSet -> prediction -> evaluation (mock backend)"
	@echo "  make e2e-episode-learning [SCENE=scene-0061]     Episodes -> AlignedEpisodes -> learning export -> verification + LeRobot round trip"
	@echo "  make e2e-cleanroom                               [DESTRUCTIVE] local-reset -> images -> canonical-bootstrap -> both L3 journeys -> verification"
	@echo "  BASELINE_ID=<id> runs a journey on a named baseline; default is a unique one per run."
	@echo "  make acceptance-grounding-dino                   Model-backend acceptance of e2e-scene-ml (needs an inference server)"
	@echo ""
	@echo "=================================================================="
	@echo "Reference corpus (config/reference/, docs/development/reference-corpus.md):"
	@echo "=================================================================="
	@echo "  make reference-data-bootstrap [REFERENCE_SCOPE=smoke-1|nuscenes-mini-full-10 UPDATE_LOCK=1]   materialize + verify the batch MCAPs (no database/MinIO/Redis/Kafka)"
	@echo "  make reference-data-verify [REFERENCE_SCOPE=..]                                               read-only re-check against corpus.lock.json"
	@echo ""
	@echo "=================================================================="
	@echo "Canonical baseline (docs/development/canonical-baseline.md):"
	@echo "=================================================================="
	@echo "  make canonical-bootstrap [REFERENCE_SCOPE=smoke-1|nuscenes-mini-full-10 | FIXTURE=<id>] [BASELINE_ID=ref-<scope>]"
	@echo "                                create-or-verify the L1/L2 baseline from the prepared reference recordings (converts nothing)"
	@echo "  make canonical-verify [same selection]   read-only re-check through the API"
	@echo ""
	@echo "=================================================================="
	@echo "Infrastructure:"
	@echo "=================================================================="
	@echo "  make local-up                 Idempotent bootstrap, see Quick start"
	@echo "  make local-down               Stop everything, PRESERVE Postgres/Redis/MinIO data"
	@echo "  make local-reset              [DESTRUCTIVE] wipe all local Postgres/Redis/MinIO data + generated ./data, keep images"
	@echo "  make status                   Show service status"
	@echo "  make logs                     Follow logs for core services"
	@echo "  make compose-build / compose-build-no-cache  Rebuild the api/worker images (docs/development/local-development.md)"
	@echo "  make acquisition-image / acquisition-image-check   dataset-acquisition + dataset-replay images / their I-36 boundary check"
	@echo "  make lerobot-image            LeRobot integration image"
	@echo "  make minio-up / minio-down / minio-logs"
	@echo "  make minio-init               Idempotent bucket bootstrap (also run by local-up)"
	@echo "  make minio-console            Print MinIO API/console URLs"
	@echo "  make db-migrate               alembic upgrade head (also run by local-up)"
	@echo "  make migrate-build            Force-rebuild the migrate image (after changing migration deps)"
	@echo "  make db-revision MSG='create table'"
	@echo "  make db-current / make db-history"
	@echo "  make db-reset                 [DESTRUCTIVE] wipe Postgres only, then re-migrate"
	@echo "  make db-shell"
	@echo "  make api-logs / api-shell / api-health / api-openapi"
	@echo "  make worker-logs / worker-shell / worker-python / worker-imports / worker-cli"
	@echo "  make worker-run-job JOB_ID=job-xxx"
	@echo "  make worker-run-pipeline PIPELINE_RUN_ID=pipe-xxx"
	@echo "  make worker-run-pipeline-task PIPELINE_RUN_ID=pipe-xxx TASK_ID=task-xxx"
	@echo "  make worker-register-robot-run MANIFEST_URI=..   REGISTER_ROBOT_RUN for a published RobotRunManifest"
	@echo "  make show-runs / show-pipeline PIPELINE_RUN_ID=pipe-xxx / show-job-events JOB_ID=job-xxx"
	@echo "  make inference-local-build / inference-local-up / inference-local-down / inference-local-logs   (CPU, opt-in)"
	@echo "  make inference-gpu-build / inference-gpu-up / inference-gpu-down / inference-gpu-logs           (GPU, opt-in)"
	@echo "  make check-inference-server / check-inference-server-ready"
	@echo "  make airflow-up / airflow-down / airflow-logs   (opt-in orchestrator, not part of local-up)"
	@echo "  make check-env / check-imports / check-celery / check-minio / check-commands"
	@echo "  make ros2-up / ros2-down / ros2-shell / ros2-check / ros2-logs / ros2-run ROS2_CMD='ros2 topic list'"
	@echo "  make streaming-up / streaming-down   Local Kafka broker (opt-in)"
	@echo "  make reconcile-once / reconcile-apply   Acquisition reconciliation: observe only / bounded registration recovery (ADR-008)"
	@echo "  make artifact-lifecycle-once [ARGS=..]  Read-only lifecycle classification of robot_runs/ objects: referenced / pending / orphan candidate / incident (ADR-008 §6; deletes nothing)"
	@echo "  make acquisition-status [ARGS=..]       Read-only operational report: a derived AcquisitionStatus per run + aggregates (ADR-008 §7; nothing is stored)"
	@echo "  make recovery-up / recovery-down / recovery-logs   Opt-in polling loops: publish-pending + reconcile --apply (compose/recovery.yaml)"
	@echo "  make prepare-data / clean-artifacts / clean-python"
	@echo ""
	@echo "=================================================================="
	@echo "Development:"
	@echo "=================================================================="
	@echo "  make setup                    Install deps + pre-commit hooks"
	@echo "  make uv-sync / make uv-lock"
	@echo "  make install-hooks / make uninstall-hooks"
	@echo "  make check                    Run pre-commit on all files"
	@echo "  make lint / make format       Ruff check / format"

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
include makefiles/e2e.mk
include makefiles/debug.mk
include makefiles/lerobot.mk
include makefiles/reference.mk
include makefiles/canonical.mk
include makefiles/streaming.mk
include makefiles/acquisition.mk
include makefiles/recovery.mk
