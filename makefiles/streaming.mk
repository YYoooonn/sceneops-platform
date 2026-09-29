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
