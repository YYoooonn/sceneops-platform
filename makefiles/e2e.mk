# ============================================================================
# End-to-end journeys -- each a user journey through production paths from a
# real source to a persisted result (ADR-007 §36):
#
#   e2e-streaming-equivalence    one source acquired in batch and by ROS 2 ->
#                                Kafka -> capture, canonically equivalent
#                                (makefiles/streaming.mk)
#   e2e-scene-ml                 Scenes -> labels -> sample views -> ScenarioSet
#                                -> prediction -> evaluation
#   e2e-episode-learning         Episodes -> AlignedEpisodes -> learning export
#                                -> export verification + LeRobot round trip
#   e2e-cleanroom                fresh platform state -> canonical-bootstrap
#                                -> both L3 journeys -> final verification
#
# Platform operations go through FastAPI; bulk data moves through one-shot
# containers and the ArtifactStore. The host needs Docker Compose, curl and jq
# (no uv, no PostgreSQL / MinIO access, no worker CLI).
#
# There is deliberately no bare `make e2e` aggregate: the journeys differ in
# what infrastructure they need (default stack / ROS 2 + Kafka / LeRobot
# image), and an aggregate would hide which one a failure needed.
# `make e2e-cleanroom` is the only full-platform acceptance entry point.
#
# Selection: SCENE=<nuScenes scene> (default scene-0061). Test-state classes
# (docs/development/test-matrix.md):
#   READ_ONLY_REFERENCE         e2e-scene-ml, e2e-episode-learning: the RobotRun is the golden
#                               reference contract's; they register none and write into a
#                               DatasetVersion of their own (DATASET_ID=<id> names it)
#   MUTATING_ACQUISITION_TEST   e2e-streaming-equivalence: it registers RobotRuns of its own
#                               and refuses to run unless DISPOSABLE_RUNTIME=1 says the
#                               runtime will be reset afterwards
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
# THE full-platform acceptance workflow. DESTRUCTIVE: `make local-reset`
# (fresh Postgres/Redis/MinIO; PRESERVES data/raw), images from the current
# tree, canonical-bootstrap, e2e-scene-ml and e2e-episode-learning on that
# baseline, final verification through the API. Needs no GPU, Airflow or Kafka.
# Requires interactive confirmation (same as local-reset) unless FORCE=1.
e2e-cleanroom:
	chmod +x scripts/e2e/e2e_cleanroom.sh scripts/canonical/*.sh
	$(E2E_ENV) scripts/e2e/e2e_cleanroom.sh
