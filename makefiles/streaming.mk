# --------------------
# Kafka streaming transport -- single-node KRaft broker, opt-in via the
# `streaming` compose profile (compose/streaming.yaml), which also holds the
# processes attached to it: the ROS 2 -> Kafka bridge integration
# (integrations/ros2-kafka-bridge) and Capture (apps/capture). Never part of `make local-up`'s default stack, and never
# a dependency of it -- Kafka is non-canonical transport, proven by `make
# test-infrastructure SUITE=kafka` (streaming-test + smoke-streaming below) only.
# See docs/architecture/streaming-transport.md.
# --------------------

.PHONY: streaming-up
streaming-up:
	$(COMPOSE) --profile streaming up -d --wait kafka

.PHONY: streaming-down
# Named service, not `--profile streaming down` bare -- a bare profile
# `down` also tears down every default-profile service (postgres/redis/
# api/...), not just this one, since profile flags only ADD services to
# the "down" set.
streaming-down:
	$(COMPOSE) --profile streaming stop kafka
	$(COMPOSE) --profile streaming rm -f kafka

.PHONY: smoke-streaming
# Publishes a deterministic binary sequence to the real local Kafka broker
# and verifies exact envelope/payload recovery, per-RobotRun Kafka
# ordering, and partitioning -- zero Postgres/MinIO domain state.
smoke-streaming:
	chmod +x tools/e2e/smoke_streaming.sh
	tools/e2e/smoke_streaming.sh

# --------------------
# ROS 2 -> Kafka -> MCAP tests: the ROS 2 -> Kafka bridge integration (integrations/ros2-kafka-bridge), the
# capture suite (packages/sceneops-recording/tests/capture) and the one test that
# spans both (tests/streaming). Each runs inside the ROS 2 image of the app it
# tests (rclpy / ROS 2 interface definitions); the sources under test are baked
# into the images, so they are rebuilt first (a no-op when nothing changed), and
# only the test directories are mounted. The capture suite includes real-Kafka
# integration tests (the lifecycle envelope of a run: the real bridge's RUN_START,
# telemetry and RUN_END, and the capture receipt's offsets over them), so run
# `make streaming-up` first.
# --------------------

ROS_RUN = $(COMPOSE) --profile streaming run --rm -T --entrypoint /ros_entrypoint.sh

.PHONY: streaming-test
streaming-test: streaming-ros-build
	$(ROS_RUN) -v $(CURDIR)/integrations/ros2-kafka-bridge/tests:/workspace/tests/bridge:ro \
		streaming-bridge python3 -m pytest /workspace/tests/bridge -q -p no:cacheprovider
	$(ROS_RUN) -v $(CURDIR)/packages/sceneops-recording/tests/capture:/workspace/tests/capture:ro \
		capture python3 -m pytest /workspace/tests/capture -q -p no:cacheprovider
	$(ROS_RUN) -v $(CURDIR)/tests/streaming:/workspace/tests/streaming:ro \
		-v $(CURDIR)/integrations/ros2-kafka-bridge/sceneops_streaming_bridge:/workspace/bridge/sceneops_streaming_bridge:ro \
		-e PYTHONPATH=/workspace/bridge \
		capture python3 -m pytest /workspace/tests/streaming -q -p no:cacheprovider

.PHONY: streaming-ros-build
streaming-ros-build:
	$(COMPOSE) --profile streaming build streaming-bridge capture

.PHONY: bridge-shell
bridge-shell:
	$(COMPOSE) --profile streaming run --rm --entrypoint /ros_entrypoint.sh streaming-bridge bash

.PHONY: bridge-check
bridge-check:
	$(ROS_RUN) streaming-bridge sh -c \
		"python3 -c 'import rclpy; print(\"rclpy ok\")' && \
		 ros2 pkg list | grep -q rosbag2_storage_mcap && echo 'mcap storage plugin ok'"

# --------------------
# Transport-preservation equivalence (ADR-007 §29.12, I-35), read-only: the
# golden reference contract's Recording Import and Streaming Acquisition
# RobotRuns of one fixture are read from the ArtifactStore and compared on
# acquisition (per-channel payloads, source times, /tf_static) and on canonical
# Scene / Episode content, with negative controls. Nothing is replayed, captured,
# registered or built, and no Kafka, ROS 2 or replay container is involved; it
# creates no durable state and runs on the reference environment
# (REFERENCE_READ_ONLY, docs/development/test-matrix.md). The Kafka lifecycle
# records of a streamed run are proven by `make streaming-test` (SUITE=kafka).
#
# Selection: SCENE=<fixture> (default smoke-1, i.e. scene-0061). Prerequisites:
# `make local-up` and the contract's RobotRuns (`make reference-contract-bootstrap`).
# --------------------

.PHONY: e2e-streaming-equivalence
e2e-streaming-equivalence:
	chmod +x tools/e2e/e2e_streaming_equivalence.sh
	API_BASE_URL=$(API_BASE_URL) API_PREFIX=$(API_PREFIX) ENV_FILE=$(ENV_FILE) \
	REFERENCE_SCOPE=$(REFERENCE_SCOPE) $(if $(SCENE),SOURCE_UNIT=$(SCENE)) \
	tools/e2e/e2e_streaming_equivalence.sh

# --------------------
# Streaming reference baseline (docs/development/canonical-baseline.md)
#
# streaming-bootstrap -- developer/test orchestration, not a Pipeline: each fixture
#                        of the selection is replayed from its LOCKED reference MCAP
#                        through replay -> ROS 2 -> bridge -> Kafka -> capture ->
#                        receipt -> publish-pending -> reconcile --apply into one
#                        streamed RobotRun, then built into one Scene and one
#                        Episode with the canonical baseline's configurations.
#                        create-or-verify: a complete fixture is reused, never
#                        replayed over; an incomplete one is recovered through the
#                        ADR-008 commands. Reads no raw dataset.
# streaming-verify    -- read-only re-check of the same baseline through the API.
# streaming-compare   -- read-only corpus-level comparison with the Recording Import baseline.
#
# REFERENCE_SCOPE (default smoke-1; nuscenes-mini-full-10) or FIXTURE selects the
# fixtures; BASELINE_ID (default: the reference contract's stream-ref baseline) names
# the baseline; RATE overrides the replay rate. The Recording Import baseline
# of the contract is never touched. Prerequisites: `make local-up` and
# `make reference-data-bootstrap REFERENCE_SCOPE=<scope>`.
# --------------------

STREAMING_ENV = $(E2E_ENV) REFERENCE_SCOPE=$(REFERENCE_SCOPE) $(if $(FIXTURE),FIXTURE=$(FIXTURE)) $(if $(RATE),RATE=$(RATE))

.PHONY: streaming-bootstrap
streaming-bootstrap: acquisition-image
	$(COMPOSE) --profile streaming build streaming-bridge capture
	chmod +x tools/baselines/streaming/*.sh tools/baselines/canonical/*.sh
	$(STREAMING_ENV) tools/baselines/streaming/streaming_bootstrap.sh

.PHONY: streaming-verify
streaming-verify:
	chmod +x tools/baselines/streaming/*.sh tools/baselines/canonical/*.sh
	$(STREAMING_ENV) tools/baselines/streaming/streaming_verify.sh

.PHONY: streaming-compare
streaming-compare:
	chmod +x tools/baselines/streaming/*.sh tools/baselines/canonical/*.sh
	$(STREAMING_ENV) $(if $(BATCH_BASELINE_ID),BATCH_BASELINE_ID=$(BATCH_BASELINE_ID)) \
		$(if $(STREAM_BASELINE_ID),STREAM_BASELINE_ID=$(STREAM_BASELINE_ID)) \
		tools/baselines/streaming/streaming_compare.sh
