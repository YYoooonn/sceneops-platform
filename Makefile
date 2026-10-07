ENV_FILE     ?= .env.local
COMPOSE      := docker compose --env-file $(ENV_FILE)
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

# The disposable PostgreSQL database and MinIO bucket of test-integration and the
# infrastructure suites (makefiles/setup.mk): created for the run and dropped after
# it, never the reference environment's POSTGRES_DB / MINIO_BUCKET.
TEST_POSTGRES_DB  ?= sceneops_test
TEST_MINIO_BUCKET ?= sceneops-test

JOB_ID          ?=
PIPELINE_RUN_ID ?=
MSG             ?=
ROS2_CMD        ?=

# Journey selection -- the only user-facing variables of the E2E / baseline
# surface (see makefiles/e2e.mk). SCENE: the nuScenes scene (default
# scene-0061). FIXTURE: one fixture of the reference scope for the canonical /
# streaming baselines (default: the whole scope). RATE: streaming replay rate of streaming-bootstrap (default: the
# fixture's replay definition). BASELINE_ID: the baseline whose RobotRuns a journey uses (default: the
# golden reference contract's; a different one registers non-contract RobotRuns and
# needs DISPOSABLE_RUNTIME=1). DATASET_ID: the Dataset an L3 journey writes into
# (default: its fixed sceneops-test-<journey> identity, reused by every run). DISPOSABLE_RUNTIME=1: this
# runtime will be reset afterwards (docs/development/test-matrix.md). BACKEND /
# MAX_SAMPLES: the detection backend and sample cap of the Scene ML journey.
SCENE           ?=
FIXTURE         ?=
RATE            ?=
BASELINE_ID     ?=
DATASET_ID      ?=
DISPOSABLE_RUNTIME ?=
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
	@echo "  make reference-contract-bootstrap   Converge the reference environment on the golden contract (needs the prepared corpus)"
	@echo "  make local-down               Stop services, KEEP all data"
	@echo "  make status / make logs       Service status / follow logs"
	@echo ""
	@echo "=================================================================="
	@echo "Validation (docs/development/test-matrix.md) -- the whole acceptance surface:"
	@echo "=================================================================="
	@echo "  make test                          Unit suites, no infrastructure"
	@echo "  make test-integration              Real Postgres/MinIO in a disposable database + bucket (needs local-up); a skipped test fails the run"
	@echo "  make test-infrastructure [SUITE=pipelines|recovery|kafka|boundaries]   Real-infrastructure suites (pipelines / recovery: a skipped test fails the run)"
	@echo "      pipelines   (default) execution contracts -- dedup/force/convergence/replacement/resumption/concurrency -- on a disposable execution runtime (needs local-up + reference-data-bootstrap)"
	@echo "      recovery    acquisition-recovery fault injection + the full-lifecycle acceptance + Job worker loss under leases + lost broker messages, in the disposable database + bucket with a throwaway Redis and workers (Docker; needs local-up)"
	@echo "      kafka       ROS 2 bridge + capture tests in the ros2 image, then the transport smoke (needs streaming-up)"
	@echo "      boundaries  tool isolation: acquisition tool + LeRobot adapter (own uv projects), acquisition images, raw-source mount of the runtime services"
	@echo "  make reference-contract-verify     READ-ONLY: exactly the golden contract's 20 RobotRuns / Scenes / Episodes; REQUIRE_PRISTINE=1 also requires no other state"
	@echo "  make e2e-streaming-equivalence [SCENE=..]   READ-ONLY: the contract's Recording Import and Streaming Acquisition RobotRuns of one fixture are equivalent"
	@echo "  make e2e-scene-ml [SCENE=scene-0061]        DERIVED: reference RobotRun -> Scenes -> labels -> sample views -> ScenarioSet -> prediction -> evaluation (mock backend)"
	@echo "  make e2e-episode-learning [SCENE=scene-0061]   DERIVED: reference RobotRun -> Episodes -> AlignedEpisodes -> learning export -> verification + LeRobot round trip"
	@echo "  make e2e-cleanroom                 DESTRUCTIVE: the acceptance of reconstruction (reset -> golden contract from the preserved inputs -> both journeys -> contract unchanged); hours"
	@echo ""
	@echo "  Test-state classes: REFERENCE_CONTRACT / REFERENCE_READ_ONLY / REFERENCE_DERIVED / MUTATING_ACQUISITION / CLEANROOM_ACCEPTANCE / DISPOSABLE_ENVIRONMENT."
	@echo "  The derived journeys write into fixed Datasets (sceneops-test-scene-ml / sceneops-test-episode-learning; DATASET_ID=<id> overrides) that a repeated run reuses."
	@echo ""
	@echo "=================================================================="
	@echo "Opt-in acceptance:"
	@echo "=================================================================="
	@echo "  make acceptance-grounding-dino     Model-backend acceptance of e2e-scene-ml with GroundingDINO (needs an inference server)"
	@echo ""
	@echo "=================================================================="
	@echo "Reference environment (config/reference/, docs/development/reference-corpus.md, reference-contract.md):"
	@echo "=================================================================="
	@echo "  make reference-data-bootstrap [REFERENCE_SCOPE=smoke-1|nuscenes-mini-full-10 UPDATE_LOCK=1]   Source preparation: materialize + verify the locked MCAPs (no database/MinIO/Redis/Kafka)"
	@echo "  make reference-contract-bootstrap   Converge on the contract's 20 RobotRuns (10 fixtures x Recording Import + Streaming Acquisition); reuses what is complete, fails on a conflicting identity"
	@echo "  make local-reset                    [DESTRUCTIVE] wipe all local Postgres/Redis/MinIO/Kafka data + generated ./data, keep images and the preserved reference inputs"
	@echo ""
	@echo "=================================================================="
	@echo "Infrastructure:"
	@echo "=================================================================="
	@echo "  make local-up                 Idempotent bootstrap, see Quick start"
	@echo "  make local-down               Stop everything, PRESERVE Postgres/Redis/MinIO data"
	@echo "  make status                   Show service status"
	@echo "  make logs                     Follow logs for core services"
	@echo "  make compose-build / compose-build-no-cache  Rebuild the api/worker images (docs/development/local-development.md)"
	@echo "  make acquisition-image / lerobot-image       dataset-acquisition + dataset-replay / LeRobot integration images"
	@echo "  make minio-up / minio-down / minio-logs"
	@echo "  make minio-init               Idempotent bucket bootstrap (also run by local-up)"
	@echo "  make minio-console            Print MinIO API/console URLs"
	@echo "  make db-migrate               alembic upgrade head (also run by local-up)"
	@echo "  make migrate-build            Force-rebuild the migrate image (after changing migration deps)"
	@echo "  make db-revision MSG='create table'"
	@echo "  make db-current / make db-history"
	@echo "  make db-reset                 [DESTRUCTIVE] wipe Postgres only, then re-migrate"
	@echo "  make db-shell"
	@echo "  make inference-local-build / inference-local-up / inference-local-down / inference-local-logs   (CPU, opt-in)"
	@echo "  make inference-gpu-build / inference-gpu-up / inference-gpu-down / inference-gpu-logs           (GPU, opt-in)"
	@echo "  make ros2-up / ros2-down / ros2-shell / ros2-logs / ros2-run ROS2_CMD='ros2 topic list'"
	@echo "  make streaming-up / streaming-down   Local Kafka broker (opt-in)"
	@echo "  make recovery-up / recovery-down / recovery-logs   Opt-in polling loops: publish-pending + reconcile --apply + sceneops-worker recover (compose/recovery.yaml)"
	@echo "  make prepare-data / clean-artifacts / clean-python"
	@echo ""
	@echo "=================================================================="
	@echo "Operator tools (diagnostics and manual operations -- NOT acceptance gates; docs/development/local-development.md#operator-tools):"
	@echo "=================================================================="
	@echo "  make check-env / check-imports / check-celery        Host tooling / api + worker image imports / broker and worker liveness"
	@echo "  make check-runtime-boundary                          Raw source is mounted only by the acquisition / reference-preparation services (starts nothing)"
	@echo "  make check-inference-server / check-inference-server-ready   Inference server liveness / readiness"
	@echo "  make ros2-check                                      rclpy and the MCAP storage plugin in the ros2 image"
	@echo "  make api-logs / api-shell / api-health / api-openapi"
	@echo "  make worker-logs / worker-shell / worker-python / worker-cli"
	@echo "  make worker-run-job JOB_ID=job-xxx"
	@echo "  make worker-advance-pipeline PIPELINE_RUN_ID=pipe-xxx   One orchestration step of a queued / running pipeline run"
	@echo "  make show-runs / show-pipeline PIPELINE_RUN_ID=pipe-xxx / show-job-events JOB_ID=job-xxx"
	@echo "  make reconcile-once / reconcile-apply   Acquisition reconciliation: observe only / bounded registration recovery (ADR-008)"
	@echo "  make execution-recovery   One execution recovery pass: requeue Jobs of lost workers, re-send lost job / advance messages"
	@echo "  make artifact-lifecycle-once [ARGS=..]  Read-only lifecycle classification of robot_runs/ objects (ADR-008 §6; deletes nothing)"
	@echo "  make acquisition-status [ARGS=..]       Read-only operational report: a derived AcquisitionStatus per run + aggregates (ADR-008 §7)"
	@echo "  make disk-report                        Read-only: host headroom, volumes, Kafka log, test leftovers, what Docker could reclaim"
	@echo ""
	@echo "Benchmarks (benchmarks/README.md) are measurement tooling, not acceptance; no Make command runs one."
	@echo ""
	@echo "=================================================================="
	@echo "Development:"
	@echo "=================================================================="
	@echo "  make setup                    Install deps + pre-commit hooks"
	@echo "  make uv-sync / make uv-lock"
	@echo "  make install-hooks / make uninstall-hooks"
	@echo "  make check                    Run pre-commit on all files"
	@echo "  make check-commands           The command surface is consistent (no stack, no pytest)"
	@echo "  make lint / make format       Ruff check / format"

# --------------------
# Includes (see makefiles/)
# --------------------

include makefiles/setup.mk
include makefiles/cleanup.mk
include makefiles/local.mk
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
