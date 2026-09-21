# --------------------
# E2E
#
# `make e2e` = every workflow E2E whose required services are provided by
# `make local-up` alone (postgres/redis/minio/api/workers) — no Airflow,
# ROS2, or a real inference-server/GPU required. Those live in the "Optional
# environment E2E" section below (e2e-airflow-pipeline, e2e-robot-can-replay,
# e2e-detection-evaluation-groundingdino / e2e-detection-evaluation-real).
#
# Assumes the standard local fixtures already exist under ./data/raw
# (nuScenes mini, and — for e2e-episode-building specifically — the CAN-
# replay MCAP recording from at least one prior `make e2e-robot-can-replay`
# run). That's a one-time data precondition, not a service dependency; see
# docs/development/local-development.md.
# --------------------

.PHONY: e2e
e2e: e2e-api-smoke e2e-pipeline-contracts e2e-dataset-ingestion e2e-raw-log-scene-building \
	e2e-episode-building e2e-episode-curation e2e-scenario-curation e2e-detection-evaluation \
	e2e-analytics-export e2e-reliability

.PHONY: e2e-api-smoke
e2e-api-smoke:
	chmod +x scripts/e2e/e2e_api_smoke.sh
	API_PREFIX=$(API_PREFIX) scripts/e2e/e2e_api_smoke.sh

.PHONY: e2e-pipeline-contracts
e2e-pipeline-contracts:
	chmod +x scripts/e2e/e2e_pipeline_contracts.sh
	API_PREFIX=$(API_PREFIX) \
	DATASET_ID=$(DATASET_ID) DATASET_VERSION=$(DATASET_VERSION) \
	scripts/e2e/e2e_pipeline_contracts.sh

.PHONY: e2e-dataset-ingestion
e2e-dataset-ingestion:
	chmod +x scripts/e2e/e2e_dataset_scene_ingestion.sh
	API_PREFIX=$(API_PREFIX) scripts/e2e/e2e_dataset_scene_ingestion.sh

.PHONY: e2e-raw-log-scene-building
# Uses its own dataset id, not the shared DATASET_ID (nuscenes/v1.0-mini):
# raw-log building produces non-ground-truth scenes by design (it segments
# a raw sensor log, it doesn't extract nuScenes' official 3D box
# annotations), so running it against the SAME dataset_id that
# e2e-pipeline-contracts/e2e-dataset-ingestion populate with real GT scenes
# permanently drags that dataset's aggregate /quality readiness down to
# "warning" — a live cross-script persistent-state interaction found while
# verifying Stabilization Request 4's expanded default `make e2e`.
RAW_LOG_DATASET_ID ?= nuscenes-raw-log
e2e-raw-log-scene-building:
	chmod +x scripts/e2e/e2e_raw_log_scene_building.sh
	API_PREFIX=$(API_PREFIX) DATASET_ID=$(RAW_LOG_DATASET_ID) scripts/e2e/e2e_raw_log_scene_building.sh

.PHONY: e2e-episode-building
# Reuses the MCAP fixture recorded by e2e-robot-can-replay (data/raw/rosbag/
# <scene>/<scene>_0.mcap). Does not itself need the ROS2 profile/sandbox —
# only the already-recorded file — so it stays in the default `make e2e` set.
#
# Uses EPISODE_DATASET_ID/EPISODE_DATASET_VERSION, not the shared DATASET_ID/
# DATASET_VERSION: the root Makefile defaults those to nuscenes/v1.0-mini for
# the Scene-focused targets, which made the old `$(or $(DATASET_ID),
# episodes-e2e)` fallback here dead code — DATASET_ID was never actually
# unset, so it silently ran Episode E2E data into the shared nuScenes
# dataset instead of its own isolated one. Found via Stabilization Request 4.
EPISODE_DATASET_ID      ?= episodes-e2e
EPISODE_DATASET_VERSION ?= v1
e2e-episode-building:
	chmod +x scripts/e2e/e2e_episode_building.sh
	API_BASE_URL=$(API_HOST) \
	SCENE=$(or $(SCENE),scene-0061) \
	ROBOT_ID=$(or $(ROBOT_ID),robot-nuscenes-01) \
	DATASET_ID=$(EPISODE_DATASET_ID) DATASET_VERSION=$(EPISODE_DATASET_VERSION) \
	scripts/e2e/e2e_episode_building.sh

.PHONY: e2e-episode-curation
# Reuses the episode registered by e2e-episode-building (same
# EPISODE_DATASET_ID/EPISODE_DATASET_VERSION) — run that target at least once
# first. Exercises ALIGN_EPISODE(x2) -> PROFILE/VALIDATE_ALIGNED_EPISODE(x2)
# -> EXPORT_LEARNING_DATA -> CURATE_EPISODES (SceneOps V2 Request 2.6) as
# standalone jobs (not a pipeline — no CURATE_EPISODES PipelineType exists
# yet, by design).
e2e-episode-curation:
	chmod +x scripts/e2e/e2e_episode_curation.sh
	API_BASE_URL=$(API_HOST) \
	DATASET_ID=$(EPISODE_DATASET_ID) DATASET_VERSION=$(EPISODE_DATASET_VERSION) \
	scripts/e2e/e2e_episode_curation.sh

.PHONY: e2e-scenario-curation
e2e-scenario-curation:
	chmod +x scripts/e2e/e2e_scenario_curation.sh
	API_PREFIX=$(API_PREFIX) \
	DATASET_ID=$(DATASET_ID) DATASET_VERSION=$(DATASET_VERSION) \
	scripts/e2e/e2e_scenario_curation.sh

.PHONY: e2e-detection-evaluation
# Mock inference backend — no real model server required, stays in the
# default set. For the real GroundingDINO path see e2e-detection-evaluation-
# groundingdino under "Optional environment E2E" below.
e2e-detection-evaluation:
	chmod +x scripts/e2e/e2e_detection_evaluation.sh
	API_PREFIX=$(API_PREFIX) \
	DATASET_ID=$(DATASET_ID) DATASET_VERSION=$(DATASET_VERSION) \
	MODEL_ID=$(MODEL_ID) MODEL_VERSION=$(MODEL_VERSION) \
	scripts/e2e/e2e_detection_evaluation.sh

.PHONY: e2e-analytics-export
e2e-analytics-export:
	chmod +x scripts/e2e/e2e_analytics_export.sh
	API_PREFIX=$(API_PREFIX) \
	DATASET_ID=$(DATASET_ID) DATASET_VERSION=$(DATASET_VERSION) \
	scripts/e2e/e2e_analytics_export.sh

.PHONY: e2e-reliability
e2e-reliability:
	chmod +x scripts/e2e/e2e_reliability.sh
	API_PREFIX=$(API_PREFIX) \
	DATASET_ID=$(DATASET_ID) DATASET_VERSION=$(DATASET_VERSION) \
	scripts/e2e/e2e_reliability.sh

# --------------------
# Optional environment E2E — each needs infrastructure beyond `make local-up`.
# Not part of `make e2e`. See make help for the exact prerequisite per target.
# --------------------

.PHONY: e2e-airflow-pipeline
# Requires: make airflow-up, AND the api service restarted with
# SCENEOPS_API_EXECUTION__PIPELINE_BACKEND=airflow (see the script's own
# header comment — this is a process-startup setting, not automatable here).
e2e-airflow-pipeline:
	chmod +x scripts/e2e/e2e_airflow_pipeline.sh
	API_PREFIX=$(API_PREFIX) \
	DATASET_ID=$(DATASET_ID) DATASET_VERSION=$(DATASET_VERSION) \
	scripts/e2e/e2e_airflow_pipeline.sh

.PHONY: e2e-robot-can-replay
# Requires: the ROS2 sandbox image (built on demand via --profile ros2).
# The only ROS2-dependent E2E — this is "the ROS2 E2E".
e2e-robot-can-replay:
	chmod +x scripts/e2e/e2e_robot_can_replay.sh
	API_BASE_URL=$(API_HOST) \
	SCENE=$(or $(SCENE),scene-0061) RATE=$(or $(RATE),10.0) \
	ROBOT_ID=$(or $(ROBOT_ID),robot-nuscenes-01) \
	scripts/e2e/e2e_robot_can_replay.sh

.PHONY: e2e-detection-evaluation-real
e2e-detection-evaluation-real: e2e-detection-evaluation-groundingdino

.PHONY: e2e-detection-evaluation-groundingdino
# Requires: a real inference server reachable at INFERENCE_ENDPOINT_URL —
# either `make inference-local-up` (CPU, real GroundingDINO weights) or
# `make inference-gpu-up` (GPU). Same script either way; which one you start
# determines whether this runs on CPU or GPU — there is no separate
# `e2e-gpu` target, since it would just be this script again.
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
