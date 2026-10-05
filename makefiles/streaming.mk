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
# ROS 2 -> Kafka -> MCAP unit tests: the streaming bridge node, the capture
# consumer/router/writer, and the channel registry they share. Runs inside the
# ros2 image (rclpy, ROS 2 interface definitions); the capture suite includes
# real-Kafka integration tests, so run `make streaming-up` first.
# --------------------

.PHONY: ros2-test
ros2-test:
	$(COMPOSE) --profile ros2 run --rm -T ros2 sh -c \
		"python3 -m pytest /workspace/nodes/tests /workspace/capture/tests -q -p no:cacheprovider"

# --------------------
# Streaming acquisition vertical + batch-vs-streaming equivalence
# (ADR-007 §29.12, §29.19 step 9): one nuScenes scene acquired in batch and by
# paced ROS 2 replay -> bridge -> Kafka -> capture, both registered as
# RobotRuns, built into Scenes and Episodes with identical configs through
# FastAPI, and proven semantically equivalent. Containers + FastAPI only: no
# host uv, no PostgreSQL/MinIO access, no worker CLI. Prerequisites:
# `make local-up` with images built from the current tree.
# --------------------

.PHONY: e2e-streaming-equivalence
e2e-streaming-equivalence: acquisition-image
	$(COMPOSE) --profile ros2 build ros2
	chmod +x scripts/e2e/e2e_streaming_equivalence.sh
	SOURCE_UNIT=$(or $(SCENE),scene-0061) RATE=$(or $(RATE),2) API_BASE_URL=$(API_BASE_URL) ENV_FILE=$(ENV_FILE) \
	scripts/e2e/e2e_streaming_equivalence.sh
