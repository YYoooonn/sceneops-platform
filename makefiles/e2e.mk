# ============================================================================
# End-to-end journeys -- each a user journey through production paths from a
# real source to a persisted result (ADR-007 §36):
#
#   e2e-streaming-equivalence    the contract's Recording Import and Streaming
#                                Acquisition RobotRuns of one fixture, read
#                                from the ArtifactStore: equivalent
#                                (makefiles/streaming.mk)
#   e2e-scene-ml                 Scenes -> labels -> sample views -> ScenarioSet
#                                -> prediction -> evaluation
#   e2e-episode-learning         Episodes -> AlignedEpisodes -> learning export
#                                -> export verification + LeRobot round trip
#   e2e-cleanroom                reset generated runtime -> golden contract
#                                reconstructed from the preserved reference inputs
#                                -> both L3 journeys on one fixture -> contract
#                                verified unchanged
#
# Platform operations go through FastAPI; bulk data moves through one-shot
# containers and the ArtifactStore. The host needs Docker Compose, curl and jq
# (no uv, no PostgreSQL / MinIO access, no worker CLI).
#
# There is deliberately no bare `make e2e` aggregate: the journeys differ in
# what infrastructure they need (default stack / LeRobot
# image), and an aggregate would hide which one a failure needed.
# `make e2e-cleanroom` is the only full-platform acceptance entry point.
#
# Selection: SCENE=<nuScenes scene> (default scene-0061). Test-state classes
# (docs/development/test-matrix.md):
#   REFERENCE_DERIVED           e2e-scene-ml, e2e-episode-learning: the RobotRun is the golden
#                               reference contract's; they register none and write into a
#                               fixed, test-owned Dataset (sceneops-test-scene-ml,
#                               sceneops-test-episode-learning; DATASET_ID=<id> names another)
#                               that a repeated run reuses and converges on
#   REFERENCE_READ_ONLY         e2e-streaming-equivalence: reads both golden RobotRuns of a
#                               fixture and creates no durable state at all (it fingerprints
#                               the platform before and after)
#   CLEANROOM_ACCEPTANCE        e2e-cleanroom: resets the runtime first
# ============================================================================

# Baseline identity, target DatasetVersion and scene selection, passed to the scripts
# through the environment (unset variables keep the scripts' own defaults).
E2E_ENV = API_BASE_URL=$(API_BASE_URL) API_PREFIX=$(API_PREFIX) ENV_FILE=$(ENV_FILE) \
	$(if $(SCENE),SOURCE_UNIT=$(SCENE)) $(if $(BASELINE_ID),BASELINE_ID=$(BASELINE_ID)) \
	$(if $(DATASET_ID),DATASET_ID=$(DATASET_ID)) $(if $(DISPOSABLE_RUNTIME),DISPOSABLE_RUNTIME=$(DISPOSABLE_RUNTIME))

.PHONY: e2e-scene-ml
# canonical Scenes -> IMPORT_LABELS -> BUILD_SCENE_SAMPLE_VIEWS ->
# scene_ml_evaluation (views -> ScenarioSet -> prediction -> evaluation), every
# revision pinned to what it consumed, with the mock backend (needs nothing
# beyond local-up). The model-backend acceptance of the same journey is
# `make acceptance-grounding-dino`.
e2e-scene-ml: acquisition-image
	chmod +x scripts/e2e/e2e_scene_ml.sh scripts/canonical/*.sh
	$(E2E_ENV) BACKEND=mock MAX_SAMPLES=$(MAX_SAMPLES) scripts/e2e/e2e_scene_ml.sh

.PHONY: acceptance-grounding-dino
# Model-backend acceptance of the Scene ML journey: the same script with the
# GroundingDINO backend, lifting boxes through the real lidar payload. Needs a
# running inference server (make inference-local-up / inference-gpu-up).
acceptance-grounding-dino: acquisition-image
	chmod +x scripts/e2e/e2e_scene_ml.sh scripts/canonical/*.sh
	$(E2E_ENV) BACKEND=grounding_dino MAX_SAMPLES=$(MAX_SAMPLES) \
	INFERENCE_ENDPOINT_URL=$(INFERENCE_ENDPOINT_URL) scripts/e2e/e2e_scene_ml.sh

.PHONY: e2e-episode-learning
# canonical Episodes -> episode_learning_data_building (align -> export) ->
# VALIDATE / PROFILE_ALIGNED_EPISODE -> export verification (worker image) ->
# LeRobot export (isolated container) read back with the official reader.
# Prerequisite: `make local-up`; builds the acquisition and LeRobot images.
e2e-episode-learning: acquisition-image lerobot-image
	chmod +x scripts/e2e/e2e_episode_learning.sh scripts/canonical/*.sh
	$(E2E_ENV) scripts/e2e/e2e_episode_learning.sh

.PHONY: e2e-cleanroom
# The acceptance of reconstruction: can an empty generated runtime be rebuilt from the
# preserved reference inputs into the golden contract, and do the primary journeys
# consume it without changing it. DESTRUCTIVE: images from the current tree, then
# `make local-reset` (PostgreSQL, Redis, MinIO, Kafka log, capture volume, generated
# ./data; PRESERVES data/raw, data/reference, config/reference), proof that it is empty,
# reference-data-verify, reference-contract-bootstrap + verify REQUIRE_PRISTINE=1, a second
# bootstrap that must converge without re-executing anything, e2e-scene-ml and
# e2e-episode-learning on one fixture (each twice), and reference-contract-verify.
# Needs ROS 2 and Kafka (the contract's Streaming Acquisition RobotRuns); no GPU or Airflow.
# Requires interactive confirmation (same as local-reset) unless FORCE=1.
e2e-cleanroom:
	chmod +x scripts/e2e/e2e_cleanroom.sh
	$(E2E_ENV) scripts/e2e/e2e_cleanroom.sh
