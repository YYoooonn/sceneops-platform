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
# Streaming acquisition vertical + transport-preservation equivalence
# (ADR-007 §29.12, §29.19 step 9): the LOCKED reference MCAP of one fixture is
# the shared logical source. The Recording Import arm is the persistent reference baseline
# (read as it is; created only if missing); the streaming arm replays the same
# MCAP -> ROS 2 -> bridge -> Kafka -> capture -> publish-pending ->
# reconcile --apply -> RobotRun -> Scenes + Episodes with the same build configs,
# through FastAPI. Equivalence is proven on acquisition and on canonical Scene /
# Episode content, with negative controls. No raw source dataset is read: the
# replay container mounts no raw dataset. Containers + FastAPI only: no host
# uv, no PostgreSQL/MinIO access, no worker CLI.
#
# Selection: REFERENCE_SCOPE (default smoke-1) or SCENE=<fixture> (one fixture is
# replayed per run); RATE overrides the fixture's replay rate; BASELINE_ID names
# the Recording Import baseline. Prerequisites: `make local-up` with images built from the
# current tree, and `make reference-data-bootstrap` for the fixture.
# --------------------

.PHONY: e2e-streaming-equivalence
e2e-streaming-equivalence: acquisition-image
	$(COMPOSE) --profile ros2 build ros2
	chmod +x scripts/e2e/e2e_streaming_equivalence.sh scripts/canonical/*.sh
	$(E2E_ENV) REFERENCE_SCOPE=$(REFERENCE_SCOPE) $(if $(RATE),RATE=$(RATE)) \
	scripts/e2e/e2e_streaming_equivalence.sh

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
# fixtures; BASELINE_ID (default stream-ref-<scope> / stream-ref-<fixture>) names
# the baseline; RATE overrides the replay rate. The Recording Import baseline
# `ref-<scope>` is never touched. Prerequisites: `make local-up` and
# `make reference-data-bootstrap REFERENCE_SCOPE=<scope>`.
# --------------------

FIXTURE ?=

STREAMING_ENV = $(E2E_ENV) REFERENCE_SCOPE=$(REFERENCE_SCOPE) $(if $(FIXTURE),FIXTURE=$(FIXTURE)) $(if $(RATE),RATE=$(RATE))

.PHONY: streaming-bootstrap
streaming-bootstrap: acquisition-image
	$(COMPOSE) --profile ros2 build ros2
	chmod +x scripts/streaming/*.sh scripts/canonical/*.sh
	$(STREAMING_ENV) scripts/streaming/streaming_bootstrap.sh

.PHONY: streaming-verify
streaming-verify:
	chmod +x scripts/streaming/*.sh scripts/canonical/*.sh
	$(STREAMING_ENV) scripts/streaming/streaming_verify.sh

.PHONY: streaming-compare
streaming-compare:
	chmod +x scripts/streaming/*.sh scripts/canonical/*.sh
	$(STREAMING_ENV) $(if $(BATCH_BASELINE_ID),BATCH_BASELINE_ID=$(BATCH_BASELINE_ID)) \
		$(if $(STREAM_BASELINE_ID),STREAM_BASELINE_ID=$(STREAM_BASELINE_ID)) \
		scripts/streaming/streaming_compare.sh
