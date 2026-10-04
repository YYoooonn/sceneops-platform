# --------------------
# Fixtures
# --------------------

# --------------------
# Persistent E2E fixture bootstrap
#
# Materializes the shared E2E fixture catalog (core/interop --
# scripts/e2e/lib.sh's resolve_e2e_fixture, mirrored in Python by
# scripts/e2e/e2e_fixture_bootstrap.py) into the real Postgres + MinIO
# `make local-up` already started, so future E2Es can start from known
# fixture state instead of recreating ad-hoc datasets independently.
# Idempotent -- safe to re-run; bootstrap itself always verifies before
# reporting success (create/reuse/verify contract), and --verify
# additionally re-checks that state independently afterward (for interop:
# through a real SceneOpsDataset).
#
# Connects from the HOST, like `make test-integration` -- overrides
# SCENEOPS_DATABASE_URL/MINIO_ENDPOINT_URL to their localhost forms rather
# than .env.local's container-internal postgres/minio hostnames.
#
# Common infra config (all fixtures) vs. source-specific config (only
# fixtures whose verification actually reads an external source fixture
# from disk) are kept in separate variables: interop's bootstrap/
# verification never touches the nuScenes source path, only core's
# does. E2E_BOOTSTRAP_SOURCE_ROOT_URI overrides that nuScenes source check
# to the host filesystem path -- the pipelines' own in-container default
# ("/data/raw/nuscenes") only resolves inside api/worker, where
# ./data:/data is bind-mounted; from the host it's $(CURDIR)/data/raw/nuscenes.
# --------------------

E2E_BOOTSTRAP_ENV = \
	SCENEOPS_DATABASE_URL="postgresql+asyncpg://$(POSTGRES_USER):$(POSTGRES_PASSWORD)@localhost:$${POSTGRES_PORT:-5432}/$(POSTGRES_DB)" \
	MINIO_ENDPOINT_URL="http://localhost:$${MINIO_API_PORT:-9000}" \
	MINIO_ROOT_USER=$(MINIO_ROOT_USER) \
	MINIO_ROOT_PASSWORD=$(MINIO_ROOT_PASSWORD) \
	MINIO_BUCKET=$(MINIO_BUCKET)

E2E_BOOTSTRAP_NUSCENES_ENV = \
	E2E_BOOTSTRAP_SOURCE_ROOT_URI=$(CURDIR)/data/raw/nuscenes

.PHONY: e2e-bootstrap
e2e-bootstrap:
	$(E2E_BOOTSTRAP_ENV) $(E2E_BOOTSTRAP_NUSCENES_ENV) uv run python scripts/e2e/bootstrap_e2e_fixtures.py --fixture all --verify

.PHONY: e2e-bootstrap-core
e2e-bootstrap-core:
	$(E2E_BOOTSTRAP_ENV) $(E2E_BOOTSTRAP_NUSCENES_ENV) uv run python scripts/e2e/bootstrap_e2e_fixtures.py --fixture core --verify

.PHONY: e2e-bootstrap-interop
e2e-bootstrap-interop:
	$(E2E_BOOTSTRAP_ENV) uv run python scripts/e2e/bootstrap_e2e_fixtures.py --fixture interop --verify


# ============================================================================
# E2E Workflows -- primary surface
#
# Each target below is a meaningful domain workflow from a real source
# through multiple production boundaries to a real persisted result --
# engineering-level checks (transport smoke tests, execution-model
# properties, backend-substitution PoCs) live under smoke-*/verify-*/
# test-integration instead (further down this file).
#
# There is no bare `make e2e` aggregate -- Scene/robot-learning/perception/
# interop have materially different infrastructure requirements (default
# stack / ROS2 sandbox / inference service / isolated LeRobot venv
# respectively), and a single aggregate would hide which of those a failure
# actually needed. The only full-platform acceptance entry point is
# `make e2e-cleanroom`, which is explicit about what it runs and in what
# order.
# ============================================================================

.PHONY: e2e-recording-scene
# The canonical Scene-domain E2E (ADR-007 §29.19 step 7): nuScenes ->
# dataset-acquisition container -> MCAP -> recording-publisher container ->
# POST /robot-runs:register -> RobotRun -> recording_scene_building
# pipeline (build_recording_scenes -> register_scenes -> validate_scene /
# profile_scene) -> retry / replacement checks. Data-plane steps run as
# one-shot containers; every platform operation goes through FastAPI.
# Prerequisite: `make local-up` with images built from the current tree.
e2e-recording-scene: acquisition-image
	chmod +x scripts/e2e/e2e_recording_scene.sh
	SOURCE_UNIT=$(or $(SCENE),scene-0061) API_BASE_URL=$(API_BASE_URL) ENV_FILE=$(ENV_FILE) \
	scripts/e2e/e2e_recording_scene.sh

.PHONY: e2e-recording-episode
# The Episode-domain vertical (ADR-007 §29.19 step 8): nuScenes ->
# dataset-acquisition container -> MCAP -> recording-publisher container ->
# POST /robot-runs:register -> RobotRun -> recording_episode_building
# pipeline (build_recording_episodes -> register_episodes ->
# validate_episode / profile_episode) -> retry / replacement checks -> a
# sibling recording_scene_building on the same RobotRun. Data-plane steps
# run as one-shot containers; every platform operation goes through
# FastAPI. Prerequisite: `make local-up` with images built from the
# current tree.
e2e-recording-episode: acquisition-image
	chmod +x scripts/e2e/e2e_recording_episode.sh
	SOURCE_UNIT=$(or $(SCENE),scene-0061) API_BASE_URL=$(API_BASE_URL) ENV_FILE=$(ENV_FILE) \
	scripts/e2e/e2e_recording_episode.sh

.PHONY: e2e-robot-learning
# UNAVAILABLE until ADR-007 implementation step 11 (the script exits 3): it
# built Episodes through the removed build_episodes path; the learning chain
# is rebuilt on canonical Episodes in the step-11 consolidation.
# The canonical robot-learning-domain E2E: real nuScenes CAN bus -> ROS2
# replay -> rosbag2/MCAP -> RosbagAdapter -> RobotRun -> Episode -> temporal
# alignment -> profile/validation -> EXPORT_LEARNING_DATA (v2-sharded write
# path) -> episode curation. Composes the same real CAN->ROS2->MCAP->
# RosbagAdapter path e2e-robot-can-replay/e2e-episode-building/e2e-episode-
# curation exercise piecemeal (see "Debug / Stage" below); see
# scripts/e2e/e2e_robot_learning.sh's own header for the full assertion set.
#
# Selection: SCENE=<name> runs exactly that one scene; MAX_SCENES=<N>
# selects the first N real, CAN-bus-eligible nuScenes v1.0-mini scenes
# (deterministic, logged); providing both is a fail-fast error; providing
# neither defaults to one scene. Requires the ROS2 sandbox image (built on
# demand via --profile ros2) and the nuScenes CAN bus expansion at
# data/raw/nuscenes/can_bus/.
e2e-robot-learning:
	chmod +x scripts/e2e/e2e_robot_learning.sh
	API_BASE_URL=$(API_BASE_URL) \
	DATASET_ID=$(DATASET_ID) DATASET_VERSION=$(DATASET_VERSION) \
	ROBOT_ID=$(or $(ROBOT_ID),robot-nuscenes-01) \
	RATE=$(or $(RATE),10.0) \
	SCENE=$(SCENE) MAX_SCENES=$(MAX_SCENES) \
	scripts/e2e/e2e_robot_learning.sh

.PHONY: e2e-perception
# The canonical perception-domain E2E: scenario curation -> scenario
# selection -> prediction -> evaluation -> persisted metrics/lineage.
# Scenario curation is always composed in (no manual SCENARIO_SET_ID/
# PIPELINE_RUN_ID hand-off needed -- see scripts/e2e/e2e_perception.sh's own
# header). BACKEND=mock (default) needs nothing beyond local-up;
# BACKEND=grounding_dino requires a real inference server already running
# (make inference-local-up/-gpu-up).
# UNAVAILABLE until ADR-007 implementation step 10: recording-derived Scenes
# carry no ground truth or keyframe groups; the script exits 3 with a message.
e2e-perception:
	chmod +x scripts/e2e/e2e_perception.sh
	API_BASE_URL=$(API_BASE_URL) \
	BACKEND=$(or $(BACKEND),mock) \
	DATASET_ID=$(DATASET_ID) DATASET_VERSION=$(DATASET_VERSION) \
	MAX_SCENES=$(MAX_SCENES) MAX_SAMPLES=$(MAX_SAMPLES) \
	INFERENCE_ENDPOINT_URL=$(INFERENCE_ENDPOINT_URL) \
	scripts/e2e/e2e_perception.sh

.PHONY: e2e-cleanroom
# THE full-platform acceptance workflow -- the only place a fresh clone/
# environment should start from. DESTRUCTIVE: runs `make local-reset` first
# (wipes Postgres/Redis/MinIO, PRESERVES data/raw/nuscenes and the CAN bus
# expansion), then e2e-recording-scene -> e2e-recording-episode -> a final
# query of real persisted API state (see scripts/e2e/e2e_cleanroom.sh for
# what is not in the chain until step 11). Does not require GPU, a real inference server, Airflow, or the
# isolated LeRobot venv -- see scripts/e2e/e2e_cleanroom.sh's own header for
# the optional follow-up verification commands. Requires interactive
# confirmation (same as local-reset) unless FORCE=1.
e2e-cleanroom:
	chmod +x scripts/e2e/e2e_cleanroom.sh
	API_BASE_URL=$(API_BASE_URL) \
	DATASET_ID=$(DATASET_ID) DATASET_VERSION=$(DATASET_VERSION) \
	scripts/e2e/e2e_cleanroom.sh

# --------------------
# Secondary E2E -- real nuScenes data, specialized/non-primary coverage.
# Not part of e2e-cleanroom's core path.
# --------------------

.PHONY: e2e-scene-analytics-export
# Scene-domain analytical Parquet export (scenes/samples/sensor_frames/
# annotations) -- a deliberately different concept from EXPORT_LEARNING_DATA
# (exercised by e2e-robot-learning), not merged with it.
e2e-scene-analytics-export:
	chmod +x scripts/e2e/e2e_scene_analytics_export.sh
	API_BASE_URL=$(API_BASE_URL) API_PREFIX=$(API_PREFIX) \
	DATASET_ID=$(DATASET_ID) DATASET_VERSION=$(DATASET_VERSION) \
	scripts/e2e/e2e_scene_analytics_export.sh

# ============================================================================
# Smoke -- transport/liveness checks only. STRICT RULE: a smoke-* target
# must never create or leave behind persistent application-domain data.
# ============================================================================

.PHONY: smoke-api
# Read-only API liveness/transport check -- creates no persistent
# Dataset/DatasetVersion/Model/PipelineRun (see scripts/e2e/smoke_api.sh's
# own header for the zero-seed-data rationale).
smoke-api:
	chmod +x scripts/e2e/smoke_api.sh
	API_BASE_URL=$(API_BASE_URL) scripts/e2e/smoke_api.sh

# smoke-lerobot-container lives in makefiles/lerobot.mk -- it isolates "does
# the lerobot-integration container execute its IntegrationRequest ->
# IntegrationResult contract" from the domain workflows.

# ============================================================================
# Verification -- execution-model properties / alternate-orchestrator
# compatibility checks, not domain workflows from a real source to a
# persisted result.
# ============================================================================

.PHONY: verify-reliability
# Verifies reliability primitives (execution-key dedup/force, pipeline
# partial-retry-after-BLOCKED on recording_scene_building) -- an
# execution-model property, not a domain workflow. Acquires a camera
# RobotRun through the acquisition containers once, then reuses it.
verify-reliability:
	chmod +x scripts/e2e/verify_reliability.sh
	API_BASE_URL=$(API_BASE_URL) API_PREFIX=$(API_PREFIX) \
	DATASET_ID=$(DATASET_ID) DATASET_VERSION=$(DATASET_VERSION) \
	scripts/e2e/verify_reliability.sh

.PHONY: verify-airflow-backend
# An ALTERNATE-ORCHESTRATOR COMPATIBILITY CHECK, not a general
# pipeline-backend substitution: the Airflow backend is a PoC whose DAG
# (airflow/dags/sceneops_pipeline_run.py) is hardcoded to
# recording_scene_building.
# Requires: make airflow-up, AND the api service restarted with
# SCENEOPS_API_EXECUTION__PIPELINE_BACKEND=airflow (see the script's own
# header comment -- this is a process-startup setting, not automatable here).
verify-airflow-backend:
	chmod +x scripts/e2e/verify_airflow_backend.sh
	API_BASE_URL=$(API_BASE_URL) API_PREFIX=$(API_PREFIX) \
	DATASET_ID=$(DATASET_ID) DATASET_VERSION=$(DATASET_VERSION) \
	scripts/e2e/verify_airflow_backend.sh

# ============================================================================
# Debug / Stage commands -- individual pipeline stages, kept runnable for
# manual debugging. NOT presented as primary E2E workflows in `make help`'s
# main E2E Workflows section -- use e2e-robot-learning/e2e-perception for
# the composed, primary path.
# ============================================================================

.PHONY: e2e-episode-building
# UNAVAILABLE until ADR-007 implementation step 11 (the script exits 3);
# use e2e-recording-episode.
# One real Episode build from an already-recorded MCAP -- standalone stage
# of e2e-robot-learning, kept for debugging build_episodes/register_episode/
# validate_episode/profile_episode in isolation. Reuses the MCAP fixture
# recorded by an earlier run -- does not itself record one.
e2e-episode-building:
	chmod +x scripts/e2e/e2e_episode_building.sh
	API_BASE_URL=$(API_BASE_URL) \
	SCENE=$(or $(SCENE),scene-0061) \
	ROBOT_ID=$(or $(ROBOT_ID),robot-nuscenes-01) \
	DATASET_ID=$(DATASET_ID) DATASET_VERSION=$(DATASET_VERSION) \
	scripts/e2e/e2e_episode_building.sh

.PHONY: e2e-episode-curation
# UNAVAILABLE until ADR-007 implementation step 11 (the script exits 3).
# The curation-POLICY-MECHANISM test (deliberately curates two revisions of
# ONE episode via two different alignment configs, to force one selected/
# one rejected and verify CurationEvaluator's selection semantics) -- NOT
# the same as e2e-robot-learning's own curate_episodes step, which uses an
# unrestricted policy over real, distinct episodes as an acceptance check.
# Keep using this script directly to re-verify selection/rejection
# mechanics specifically. EPISODE_ID (explicit) or RUN_ID (resolved via a
# real API query scoped to that RobotRun) selects which episode to curate --
# never reconstructed from a bash formula (see the script's own header).
e2e-episode-curation:
	chmod +x scripts/e2e/e2e_episode_curation.sh
	API_BASE_URL=$(API_BASE_URL) \
	DATASET_ID=$(DATASET_ID) DATASET_VERSION=$(DATASET_VERSION) \
	scripts/e2e/e2e_episode_curation.sh

.PHONY: e2e-scenario-curation
# One scenario_curation pipeline dispatch in isolation -- e2e-perception
# composes this same step automatically; kept standalone for debugging
# mine_scenarios/score_scenario_readiness without also running detection.
# UNAVAILABLE until ADR-007 implementation step 10 (its detection_ready
# profile needs ground truth); the script exits 3 with a message.
e2e-scenario-curation:
	chmod +x scripts/e2e/e2e_scenario_curation.sh
	API_PREFIX=$(API_PREFIX) \
	DATASET_ID=$(DATASET_ID) DATASET_VERSION=$(DATASET_VERSION) \
	scripts/e2e/e2e_scenario_curation.sh
