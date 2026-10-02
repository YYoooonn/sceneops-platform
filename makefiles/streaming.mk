# --------------------
# Kafka streaming transport -- single-node KRaft broker, opt-in via the
# `streaming` compose profile (compose/streaming.yaml), mirroring
# ros2.mk's opt-in `ros2` profile (makefiles/ros2.mk). Never part of
# `make local-up`'s default stack, and never a dependency of it -- Kafka is
# non-canonical transport, proven by `make smoke-streaming` only.
# See docs/architecture/streaming-transport.md.
# --------------------

.PHONY: streaming-up
streaming-up:
	$(COMPOSE) --profile streaming up -d --wait kafka

.PHONY: streaming-down
# Named service, not `--profile streaming down` bare -- a bare profile
# `down` also tears down every default-profile service (postgres/redis/
# api/...), not just this one, since profile flags only ADD services to
# the "down" set (see ros2-down's identical reasoning, makefiles/ros2.mk).
streaming-down:
	$(COMPOSE) --profile streaming stop kafka
	$(COMPOSE) --profile streaming rm -f kafka

.PHONY: smoke-streaming
# Publishes a deterministic binary sequence to the real local Kafka broker
# and verifies exact envelope/payload recovery, per-RobotRun Kafka
# ordering, and partitioning -- zero Postgres/MinIO domain state.
smoke-streaming:
	chmod +x scripts/e2e/smoke_streaming.sh
	scripts/e2e/smoke_streaming.sh

# --------------------
# ROS2 streaming bridge -- real nuScenes CAN replay -> real ROS2 DDS ->
# ros2/nodes/streaming_bridge_node.py -> real Kafka -> TelemetryConsumer.
# Independent of smoke-streaming (that proves the Kafka transport itself;
# this proves the ROS2 -> transport adapter) -- neither replaces the
# other.
# --------------------

.PHONY: e2e-ros2-streaming
e2e-ros2-streaming:
	chmod +x scripts/e2e/e2e_ros2_streaming.sh
	SCENE=$(or $(SCENE),scene-0061) RATE=$(or $(RATE),10.0) \
	scripts/e2e/e2e_ros2_streaming.sh

# --------------------
# Durable MCAP capture -- real CAN replay -> ROS2 -> bridge -> real
# Kafka -> ros2/capture (run-scoped Kafka consumer) -> validated,
# finalized MCAP -> RosbagAdapter compatibility check -> semantic
# comparison against a direct `ros2 bag record` of the same scene.
# Independent of e2e-ros2-streaming (that proves messages reach Kafka;
# this proves they can be durably captured back out of Kafka into a
# rosbag2-compatible file) -- neither replaces the other. Creates zero
# canonical RobotRun/Scene/Episode/ArtifactRecord and writes no
# Postgres/MinIO state; captured/direct-recorded MCAPs are scratch files
# under data/tmp_streaming_capture/, never the canonical
# data/raw/rosbag/<scene> baseline location.
# --------------------

.PHONY: e2e-streaming-capture
e2e-streaming-capture:
	chmod +x scripts/e2e/e2e_streaming_capture.sh
	SCENE=$(or $(SCENE),scene-0061) RATE=$(or $(RATE),10.0) \
	scripts/e2e/e2e_streaming_capture.sh

# --------------------
# Canonical RobotRun registration -- real CAN replay -> ROS2 -> bridge ->
# real Kafka -> ros2/capture -> finalized MCAP -> ArtifactStore (real
# MinIO) -> ArtifactRecord -> canonical RobotRun (real Postgres) ->
# retrieve stored MCAP -> RosbagAdapter, plus idempotent-retry and
# conflict verification. Independent of e2e-streaming-capture (that
# proves the MCAP is durably captured; this proves it can be registered
# as canonical state) -- neither replaces the other. Prerequisite:
# `make local-up` (Postgres/MinIO/worker-cli's deps) in addition to
# `make streaming-up`. Creates real RobotRun/ArtifactRecord/MinIO-object
# canonical state for a fresh, uniquely-generated robot_run_id each run
# -- that is this E2E's own deliverable, not a leak; the frozen
# sceneops-canonical/v0.0 baseline dataset is untouched (different
# tables entirely).
# --------------------

.PHONY: e2e-robot-run-registration
e2e-robot-run-registration:
	chmod +x scripts/e2e/e2e_robot_run_registration.sh
	SCENE=$(or $(SCENE),scene-0061) RATE=$(or $(RATE),10.0) \
	scripts/e2e/e2e_robot_run_registration.sh

# --------------------
# Episode / learning-data bridge -- real CAN replay -> ROS2 -> Kafka ->
# MCAP capture -> ArtifactStore -> canonical RobotRun -> verified recording
# resolver (sceneops_worker.robots.resolver) -> existing
# raw_log_episode_building pipeline -> Episode -> existing align/profile/
# validate/export jobs -> real SceneOpsDataset.open() + step reads
# (scripts/canonical/verify_learning_export.py, reused unmodified).
# Independent of e2e-robot-run-registration (that proves canonical
# registration; this proves the registered RobotRun can actually be
# consumed by the existing Episode/learning pipeline) -- neither replaces
# the other. Prerequisites: `make local-up` and `make streaming-up`.
# Uses a fresh, isolated test-e2e-* dataset_id per invocation -- never
# mutates the frozen sceneops-canonical/v0.0 baseline.
# --------------------

.PHONY: e2e-robot-run-learning
e2e-robot-run-learning:
	chmod +x scripts/e2e/e2e_robot_run_learning.sh
	SCENE=$(or $(SCENE),scene-0061) RATE=$(or $(RATE),10.0) \
	scripts/e2e/e2e_robot_run_learning.sh
