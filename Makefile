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

# Shared local MinIO defaults -- the single source of truth `make
# e2e-bootstrap`/`make test-integration` both pass into their bootstrap
# commands, instead of each hardcoding its own copy of the same literals.
# Overridable exactly like the POSTGRES_* vars above.
MINIO_ROOT_USER     ?= minioadmin
MINIO_ROOT_PASSWORD ?= minioadmin
MINIO_BUCKET        ?= sceneops

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

# E2E selection knobs -- the only user-facing selection variables the
# primary E2E surface exposes. MAX_SCENES is the one authoritative name
# across e2e-scene/e2e-scene-rawlog/e2e-robot-learning. BACKEND selects
# e2e-perception's inference backend (mock|grounding_dino).
MAX_SCENES      ?=
MAX_SAMPLES     ?=
BACKEND         ?=

# MODEL_ID/MODEL_VERSION are deliberately not declared here -- e2e-perception
# derives the right model identity from BACKEND itself (dummy-detector/v1
# for mock, grounding-dino/tiny for grounding_dino; see
# scripts/e2e/e2e_perception.sh), so a top-level Makefile default would just
# be a second, redundant place the same value lived. A manual override
# (`MODEL_ID=my-model make e2e-perception`) still works via ordinary
# environment inheritance, with no Makefile declaration needed for that.

# Shared E2E fixture catalog ("core"/"interop"/"raw-log", see
# scripts/e2e/lib.sh's resolve_e2e_fixture for the full catalog) and
# canonical-vs-source identity separation.
#
# DATASET_ID/DATASET_VERSION below are the "core" fixture's default --
# SceneOps' own canonical identity, used by e2e-scene, e2e-perception
# (scenario curation + detection evaluation), e2e-scene-analytics-export,
# verify-reliability, verify-airflow-backend, e2e-robot-learning,
# e2e-episode-building, and e2e-episode-curation. Canonical identity is
# never constrained by what an external format's SDK happens to require:
# SOURCE_FORMAT_VERSION below is the separate, real nuScenes SDK version
# (apps/worker/sceneops_worker/jobs/dataset/ingest_scenes.py reads this,
# never dataset_version, and passes it through to the isolated
# nuscenes-integration service's `nuscenes-devkit` `NuScenes(...)` call --
# apps/worker itself does not import nuscenes-devkit at all).
#
# See scripts/e2e/lib.sh's resolve_e2e_fixture "raw-log" case for the
# raw-log fixture's own identity (test-e2e-raw-log), which stays isolated
# from "core" on purpose but shares this same SOURCE_FORMAT_VERSION (both
# read the same physical nuScenes mini fixture). An explicit
# `DATASET_ID=my-dataset make e2e-...` always overrides these defaults and
# is never coerced into the test-e2e-* form.
DEFAULT_E2E_DATASET_PREFIX  ?= test-e2e
DEFAULT_E2E_DATASET_VERSION ?= test-v1
DATASET_ID            ?= $(DEFAULT_E2E_DATASET_PREFIX)-core
DATASET_VERSION       ?= $(DEFAULT_E2E_DATASET_VERSION)

# SOURCE_FORMAT/SOURCE_FORMAT_VERSION/SOURCE_ROOT_URI are deliberately not
# declared here -- every current E2E fixture is nuScenes, so exposing them
# as normal operator-overridable Make variables would misrepresent them as
# a real choice, and would just be a second, redundant place the same
# value lived. scripts/e2e/lib.sh's resolve_e2e_fixture is the single real
# source of truth for their defaults; an ambient `SOURCE_ROOT_URI=...`
# still overrides it via ordinary environment inheritance, no Makefile
# declaration needed for that to work.

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
	@echo "  make canonical-bootstrap      Create-or-verify the sceneops-canonical/v0.0 dev baseline (see below)"
	@echo "  make test                     All infrastructure-independent unit tests"
	@echo "  make test-integration         Real-Postgres/MinIO tests -- requires local-up"
	@echo "  make e2e-cleanroom            THE full-platform acceptance workflow -- see 'E2E Workflows' below"
	@echo "  make local-down               Stop services, KEEP all data"
	@echo "  make status / make logs       Service status / follow logs"
	@echo ""
	@echo "=================================================================="
	@echo "Tests (see docs/development/local-development.md):"
	@echo "=================================================================="
	@echo "  make test                     worker+api+core+analytics+inference-server unit tests -- no infra needed"
	@echo "  make test-integration         sceneops-db/storage (real Postgres/MinIO) + pipeline-definitions contract tests -- requires local-up"
	@echo ""
	@echo "=================================================================="
	@echo "Smoke (transport/liveness only -- never creates persistent domain data):"
	@echo "=================================================================="
	@echo "  make smoke-api                               API liveness/transport, read-only"
	@echo "  make smoke-nuscenes-container                nuscenes-integration container's IntegrationRequest/Result contract"
	@echo "  make smoke-lerobot-container                 lerobot-integration container's IntegrationRequest/Result contract"
	@echo ""
	@echo "=================================================================="
	@echo "Verification (execution-model/backend-substitution properties, not domain workflows):"
	@echo "=================================================================="
	@echo "  make verify-reliability                     execution-key dedup/force + partial-retry semantics"
	@echo "  make verify-airflow-backend                 Requires: make airflow-up; alternate-orchestrator compatibility check"
	@echo "                                               (dataset_scene_ingestion only -- not a general backend substitution)"
	@echo ""
	@echo "=================================================================="
	@echo "E2E Workflows -- primary surface (needs only local-up, unless noted):"
	@echo "=================================================================="
	@echo "  make e2e-scene                              real nuScenes -> SceneRecord -> validation/profile/manifest"
	@echo "  make e2e-robot-learning [SCENE=scene-0061 | MAX_SCENES=N]"
	@echo "                                               real CAN bus -> ROS2 -> MCAP -> Episode -> learning export/curation"
	@echo "                                               Requires: ROS2 sandbox (--profile ros2, built on demand)"
	@echo "  make e2e-perception [BACKEND=mock|grounding_dino]"
	@echo "                                               scenario curation -> prediction -> evaluation (default mock)"
	@echo "                                               BACKEND=grounding_dino requires inference-local-up/-gpu-up"
	@echo "  make e2e-interop                            real Postgres/MinIO -> SceneOpsDataset -> LeRobot -> golden comparison"
	@echo "                                               Requires: make lerobot-sync (once)"
	@echo "  make e2e-cleanroom                          THE full-platform acceptance workflow: local-reset -> e2e-scene ->"
	@echo "                                               e2e-robot-learning -> e2e-perception(mock) -> persisted-state validation"
	@echo "                                               [DESTRUCTIVE -- wipes Postgres/Redis/MinIO, preserves data/raw]"
	@echo ""
	@echo "=================================================================="
	@echo "Secondary E2E (real nuScenes data, specialized/non-primary coverage):"
	@echo "=================================================================="
	@echo "  make e2e-scene-rawlog                       real nuScenes via the raw-log representation (RawLogAdapter/"
	@echo "                                               segmentation/sampling machinery, shared with the robot-log path)"
	@echo "  make e2e-scene-analytics-export              Scene-domain analytical Parquet export (distinct from the"
	@echo "                                               learning-data export, which e2e-robot-learning already exercises)"
	@echo "  make e2e-lerobot-container                  containerized variant of e2e-interop's golden round trip"
	@echo ""
	@echo "=================================================================="
	@echo "Debug / Stage commands (individual pipeline stages, for manual debugging -- not primary E2E workflows):"
	@echo "=================================================================="
	@echo "  make e2e-robot-can-replay [SCENE=scene-0061 RATE=10.0]   one CAN replay -> record -> register -> ingest"
	@echo "  make e2e-episode-building [SCENE=scene-0061]             one Episode build (reuses an existing MCAP recording)"
	@echo "  make e2e-episode-curation [EPISODE_ID=... | RUN_ID=...]  align/profile/validate/export/curate for one episode"
	@echo "                                                            (curation-policy selection/rejection mechanism test)"
	@echo "  make e2e-scenario-curation                                one scenario_curation pipeline dispatch"
	@echo "  make compare-detection PIPELINE_RUN_ID=pipe-xxx"
	@echo "  make show-runs"
	@echo "  make show-pipeline PIPELINE_RUN_ID=pipe-xxx"
	@echo "  make show-job-events JOB_ID=job-xxx"
	@echo "  (worker logs: make worker-logs)"
	@echo ""
	@echo "=================================================================="
	@echo "Canonical development baseline (docs/development/canonical-baseline.md):"
	@echo "=================================================================="
	@echo "  make canonical-bootstrap      Create-or-verify sceneops-scenes/episodes/canonical @ v0.0 from real"
	@echo "                                nuScenes data (never mutates an already-matching baseline)"
	@echo "  make canonical-verify         Read-only re-check of the same v0.0 contract"
	@echo ""
	@echo "=================================================================="
	@echo "Infrastructure:"
	@echo "=================================================================="
	@echo "  make local-up                 Idempotent bootstrap, see Quick start"
	@echo "  make local-down               Stop everything, PRESERVE Postgres/Redis/MinIO data"
	@echo "  make local-reset              [DESTRUCTIVE] wipe all local Postgres/Redis/MinIO data + generated ./data, keep images"
	@echo "  make status                   Show service status"
	@echo "  make logs                     Follow logs for core services"
	@echo "  make compose-build / compose-build-no-cache  Rebuild the api/worker images (rarely needed -- see docs/development/local-development.md)"
	@echo "  make minio-up / minio-down / minio-logs"
	@echo "  make minio-init                Idempotent bucket bootstrap (also run by local-up)"
	@echo "  make minio-console             Print MinIO API/console URLs"
	@echo "  make db-migrate                alembic upgrade head (also run by local-up)"
	@echo "  make migrate-build             Force-rebuild the migrate image (after changing migration deps)"
	@echo "  make db-revision MSG='create table'"
	@echo "  make db-current / make db-history"
	@echo "  make db-reset                  [DESTRUCTIVE] wipe Postgres only, then re-migrate"
	@echo "  make db-shell"
	@echo "  make api-logs / api-shell / api-health / api-openapi"
	@echo "  make worker-logs / worker-shell / worker-python / worker-imports / worker-cli"
	@echo "  make worker-run-job JOB_ID=job-xxx"
	@echo "  make worker-run-pipeline PIPELINE_RUN_ID=pipe-xxx"
	@echo "  make worker-run-pipeline-task PIPELINE_RUN_ID=pipe-xxx TASK_ID=task-xxx"
	@echo "  make worker-register-robot-run ROBOT_ID=.. RUN_ID=.. MCAP_URI=..   Stopgap CLI (no ArtifactStore)"
	@echo "  sceneops-worker robots register-capture --robot-id .. --robot-run-id .. --mcap-path ..   (via worker-cli; ArtifactStore + ArtifactRecord + RobotRun)"
	@echo "  make inference-local-build / inference-local-up / inference-local-down / inference-local-logs   (CPU, opt-in)"
	@echo "  make inference-gpu-build / inference-gpu-up / inference-gpu-down / inference-gpu-logs           (GPU, opt-in)"
	@echo "  make check-inference-server / check-inference-server-ready"
	@echo "  make airflow-up / airflow-down / airflow-logs   (opt-in pipeline execution backend, not part of local-up)"
	@echo "  make check-env / check-imports / check-celery / check-minio"
	@echo "  make ros2-up / ros2-down / ros2-shell / ros2-check / ros2-logs   (Jazzy dev sandbox)"
	@echo "  make ros2-run ROS2_CMD='ros2 topic list'"
	@echo "  make ros2-can-replay SCENE=scene-0061 RATE=1.0"
	@echo "  make ros2-can-replay-record SCENE=scene-0061 RATE=5.0   (records to data/raw/rosbag/<scene>)"
	@echo "  make streaming-up / streaming-down             Local Kafka broker (opt-in)"
	@echo "  make smoke-streaming                            Kafka transport smoke test"
	@echo "  make e2e-ros2-streaming SCENE=scene-0061 RATE=10.0"
	@echo "  make e2e-streaming-capture SCENE=scene-0061 RATE=10.0   Durable MCAP capture from Kafka"
	@echo "  make e2e-robot-run-registration SCENE=scene-0061 RATE=10.0   Capture -> canonical RobotRun"
	@echo "  make e2e-robot-run-learning SCENE=scene-0061 RATE=10.0   RobotRun -> materialize -> Episode -> learning data"
	@echo "  make register-nuscenes-dataset"
	@echo "  make e2e-bootstrap / e2e-bootstrap-core / e2e-bootstrap-interop / e2e-bootstrap-raw-log"
	@echo "                                Persistent E2E fixture bootstrap (idempotent; requires local-up)"
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
include makefiles/nuscenes.mk
include makefiles/canonical.mk
include makefiles/streaming.mk
