# Streaming Transport

> Describes how SceneOps moves robot telemetry through Kafka, and how the
> ROS2 streaming bridge feeds real ROS2 topics into that transport. Like
> [external-integration-runtime.md](./external-integration-runtime.md),
> this is a "what's actually built" document, not aspirational -- every
> claim below is checked against the code and against real, live runs
> (`make streaming-up && make smoke-streaming`, `make e2e-ros2-streaming`).
>
> Two parts: Part 1 covers the Kafka transport itself (envelope contract,
> wire format, delivery/ordering/partitioning semantics, configuration).
> Part 2 covers the ROS2 streaming bridge built on top of it. Neither part
> changes the other -- the bridge is a producer/consumer of the transport
> contract in Part 1, not a modification of it.

## 1. Goal and scope

The Kafka transport moves binary robot telemetry through Kafka as a typed
envelope and recovers it byte-for-byte identical on the other side,
including its metadata. It is transport only.

```text
binary robot telemetry
  -> typed streaming envelope (TelemetryEnvelope)
  -> Kafka producer
  -> Kafka broker
  -> Kafka consumer
  -> exact envelope/payload recovery
```

**Not implemented by the transport itself:** an MCAP writer, `RobotRun`/
`Episode` lifecycle integration, any Postgres/ArtifactStore write, a
DLQ/retry policy, Schema Registry/Avro, or any UI (see §19 for the full
current non-goals list, which also covers the ROS2 bridge). `make
smoke-streaming` leaves zero canonical (Postgres/MinIO) state -- verified
by construction: nothing in `sceneops-streaming` imports `sceneops-db` or
an `ArtifactStore`.

The robot-runtime-communication (ROS2) vs. data-platform-event-stream
(Kafka) boundary this transport lives on is decided in
[ADR-005](../adr/005-ros2-vs-kafka-boundary.md); why streaming was worth
building only once real telemetry needed it is decided in
[ADR-003](../adr/003-batch-first-architecture.md). The existing batch
robot-data path (`ros2/`,
`apps/worker/sceneops_worker/datasets/ingestion/rosbag_raw_log.py`,
`RosbagAdapter` -- replay -> `ros2 bag record` -> MCAP -> decode ->
ingest, see [robot-run-and-mcap.md](../workflows/robot-run-and-mcap.md))
is untouched by this transport and remains a fully independent path.

## 2. Package layout

Mirrors the port/adapter split [overview.md](./overview.md) §1 already
uses for `ArtifactStore` (contract in `sceneops-core`, real backend in
`sceneops-storage`):

```text
sceneops-core (packages/sceneops-core/sceneops_core/streaming/)
  Transport-neutral contract only -- TelemetryEnvelope (Pydantic),
  TelemetryEnvelopeVersion/EnvelopeEncoding enums, ConsumedTelemetryEnvelope,
  TelemetryProducer/TelemetryConsumer Protocols. Zero Kafka SDK import,
  zero I/O. Default topic name constant
  (sceneops_core.constants.streaming.DEFAULT_TELEMETRY_TOPIC).

sceneops-streaming (packages/sceneops-streaming)
  The only package that imports confluent_kafka. StreamingSettings
  (bootstrap servers / topic / consumer group / producer client id),
  wire.py (pure Kafka key/headers/value mapping -- no broker needed),
  KafkaTelemetryProducer, KafkaTelemetryConsumer, EnvelopeDecodeError.
```

`apps/worker`, `apps/api`, and every domain package (`sceneops-analytics`,
Scene, Episode, learning-data) depend on neither package's Kafka-specific
half. The `ros2/` image is the one real dependent outside
`sceneops-streaming`'s own tests -- it pip-installs both packages
directly so the ROS2 streaming bridge can import them; `apps/api` and
`apps/worker` still do not.

`sceneops-streaming` has no dependents within the main workspace's own
dependency graph -- it is listed in root `pyproject.toml`'s dev
dependency-group so `make test` installs it, without adding the Kafka SDK
to `apps/worker`'s or `apps/api`'s own dependency tree.

## 3. TelemetryEnvelope contract

`sceneops_core.streaming.TelemetryEnvelope`:

```text
TelemetryEnvelope
├── version               TelemetryEnvelopeVersion, default V1 -- schema
│                          version, independent of DatasetVersion/
│                          source-format version/MCAP schema version
├── robot_id               str, required
├── robot_run_id           str, required -- Kafka partitioning identity
├── channel                str, required (e.g. "/vehicle/odom")
├── message_type            str, required (e.g. "nav_msgs/msg/Odometry")
├── source_timestamp_ns     int > 0, required -- robot/sensor observation time
├── ingest_timestamp_ns     int > 0, defaults to construction time -- see §4
├── sequence_number         int >= 0, required -- diagnostic only, never identity
├── encoding                EnvelopeEncoding (ros2-cdr | json | raw), required
└── payload                 bytes -- binary-first, never base64
```

A transport primitive, not a universal SceneOps DataUnit and not a domain
record -- domain fields belong inside `payload`, decoded by something that
understands `encoding`/`message_type`. Every field is required with no
silent default except `version`; a malformed/incomplete record fails
Pydantic validation instead of being coerced (§7).

## 4. Time, ordering, delivery semantics

**Time.** `source_timestamp_ns` (the observation/event) and
`ingest_timestamp_ns` (when SceneOps accepted the message at the
transport boundary) are semantically distinct clocks, tracked
independently -- they are two different concepts, not two values required
to differ numerically. Arrival time never *replaces* source time (a
decoder must never conflate the two), but a caller supplying the same
instant for both is legal and round-trips exactly; the schema has no
equality/inequality constraint between them.

**Ownership.** `source_timestamp_ns` is always supplied by the source
adapter -- for the ROS2 bridge, the source ROS2 message's own timestamp
(Part 2). `ingest_timestamp_ns` is assigned at construction of the
`TelemetryEnvelope` itself (`default_factory=time.time_ns`) unless the
caller explicitly supplies its own value -- constructing the envelope IS
accepting the message at the streaming transport boundary in this
architecture, so there is exactly one default-assignment point, never a
second, competing one inside the producer/consumer/wire layers. A caller
with a more precise boundary-acceptance time may always override the
default explicitly.

**Ordering.** Arrival order (Kafka offset order within a partition) and
source timestamp are kept as separate concepts -- the transport layer
never sorts or reorders messages. Kafka partitioning by `robot_run_id`
gives deterministic per-RobotRun ordering (§6); no attempt is made at
ordering across RobotRuns or globally.

**Delivery.** At-least-once. `KafkaTelemetryProducer.publish` buffers a
record and returns once accepted into librdkafka's client-side queue;
`flush` blocks until the broker acknowledges every buffered record (or
raises on the first delivery failure). No exactly-once claim is made
end-to-end -- Kafka delivery and any future canonical-side idempotency
(e.g. a dedup key when this eventually feeds `RobotRun`/MCAP) are separate
concerns, deliberately not conflated here.

## 5. Kafka wire contract

`sceneops_streaming.wire` (`encode_envelope`/`decode_envelope`):

```text
Kafka key       robot_run_id, UTF-8 bytes             (wire.partition_key)
Kafka headers   one header per envelope field, prefixed
                "sceneops.envelope." (sceneops_core.constants.streaming.
                TELEMETRY_HEADER_PREFIX) -- version, robot_id,
                robot_run_id, channel, message_type,
                source_timestamp_ns, ingest_timestamp_ns,
                sequence_number, encoding (all header VALUES are UTF-8
                text, e.g. integers as decimal strings)
Kafka value     payload bytes, exactly as given, never base64
```

`decode_envelope` reconstructs the full logical `TelemetryEnvelope` from
headers + value; `KafkaTelemetryConsumer.poll` wraps the result in
`ConsumedTelemetryEnvelope` (envelope + raw Kafka `key` + `topic`/
`partition`/`offset`). Kafka position metadata is transport metadata,
never folded into `TelemetryEnvelope` itself.

## 6. Topic and partitioning contract

One topic, one configuration source:
`sceneops_core.constants.streaming.DEFAULT_TELEMETRY_TOPIC` =
`sceneops.robot.telemetry.v1`, overridable via
`StreamingSettings.telemetry_topic`
(`SCENEOPS_STREAMING_KAFKA_TELEMETRY_TOPIC`). No topic-per-robot or
topic-per-channel; nothing in the codebase creates a second telemetry
topic. There is no separate "topic prefix" concept anywhere in the
implementation.

Partitioning: **Kafka key = `robot_run_id`** (`wire.partition_key`).
Every message for one RobotRun routes to the same partition
deterministically -- per-RobotRun ordering, by design, not global
ordering. Verified against the real broker by `make smoke-streaming`: a
3-message run-A sequence always lands on exactly one partition, in
publish order.

**Scaling tradeoff (explicit, not resolved here):** Kafka's default key
hashing means the partition a given `robot_run_id` maps to depends on the
topic's current partition count. Growing the topic's partition count
later (to add throughput/parallelism across many concurrent RobotRuns)
remaps future keys, but never retroactively -- an already-written
RobotRun's history stays on its original partition, while some other
RobotRun's *new* messages may land on a different partition than that same
RobotRun's *old* messages did before the repartition. This is normal
Kafka behavior, not a bug, but it does mean per-RobotRun ordering is only
guaranteed *within* one partition-count regime. A production deployment
that needs partition growth without breaking ordering guarantees would
need to either pre-provision partition count generously or move to a
custom partitioner keyed on a stable RobotRun-to-partition mapping decided
once at RobotRun creation -- neither is implemented; both are future work
if/when scale demands it.

## 7. Invalid-message handling

`decode_envelope` never silently coerces or drops a malformed record. It
raises `sceneops_streaming.errors.EnvelopeDecodeError` (a typed
`ValueError` subclass) for:

```text
missing robot_run_id / channel / message_type / robot_id header
missing encoding header
invalid timestamp        (non-integer, or <= 0 -- TelemetryEnvelope's
                          own Field(gt=0) constraint)
unknown envelope version  (anything other than "v1" -- rejected by
                          TelemetryEnvelopeVersion's enum validation)
non-UTF-8 header bytes
missing record value (payload)
```

`KafkaTelemetryConsumer.poll` catches decode failures from `wire.py` and
re-raises with `topic`/`partition`/`offset` attached, so a bad record is
diagnosable without a debugger. No DLQ or automatic retry policy exists
-- that is a reliability-boundary concern, deliberately deferred (§19).
The caller (`make smoke-streaming`, or the ROS2 bridge's own
consumer-side tooling) decides whether to stop, skip, or surface the
failure.

## 8. Local development

```text
make streaming-up      # starts a single-node KRaft Kafka broker
                        # (compose/streaming.yaml, profile `streaming` --
                        # never started by `make local-up`)
make smoke-streaming   # publishes a deterministic run-A/run-B binary
                        # sequence, verifies exact recovery + ordering +
                        # partitioning against the real broker
make streaming-down    # stops/removes only the kafka service; every
                        # other service (postgres/redis/minio/api/
                        # worker/airflow) is untouched
```

`compose/streaming.yaml`'s `kafka` service uses `apache/kafka:3.9.2` in
combined broker+controller KRaft mode (no ZooKeeper), with dual listeners
(`PLAINTEXT` for other containers on the `sceneops-network`,
`PLAINTEXT_HOST` for host-side tooling via `${KAFKA_HOST_PORT:-9092}`) --
matching how `make smoke-streaming` itself runs on the host (`uv run
python`, like every other `scripts/e2e/*` script) and needs a
localhost-reachable broker, same convention as
`makefiles/e2e.mk`'s `E2E_BOOTSTRAP_ENV` localhost overrides for
Postgres/MinIO.

**Topic creation.** `KAFKA_AUTO_CREATE_TOPICS_ENABLE=true` is set on the
broker for local-dev convenience -- the one telemetry topic is created
transparently on first publish. This keeps the public Make surface
minimal (no dedicated `streaming-topic-create` target) while staying
fully deterministic -- the topic name is the one fixed, configured value
(`SCENEOPS_STREAMING_KAFKA_TELEMETRY_TOPIC`, §9), so auto-creation always
converges on the same single topic regardless of which process publishes
to it first. A production deployment would instead provision the topic
explicitly (infra-as-code), out of scope here.

## 9. Configuration

Two distinct configuration domains, deliberately never mixed into one
list of environment variables:

### 9.1 SceneOps streaming-client configuration

`sceneops_streaming.config.StreamingSettings`
(`SCENEOPS_STREAMING_KAFKA_` env prefix) -- how a SceneOps
application/tool reaches Kafka. The complete public surface, four
variables:

```text
env var                                      field                default
SCENEOPS_STREAMING_KAFKA_BOOTSTRAP_SERVERS   bootstrap_servers    kafka:9092
SCENEOPS_STREAMING_KAFKA_TELEMETRY_TOPIC     telemetry_topic      sceneops.robot.telemetry.v1
SCENEOPS_STREAMING_KAFKA_PRODUCER_CLIENT_ID  producer_client_id   sceneops-telemetry-producer
SCENEOPS_STREAMING_KAFKA_CONSUMER_GROUP_ID   consumer_group_id    sceneops-telemetry-consumer
```

- `bootstrap_servers` -- the only SceneOps-side broker-location setting.
  No aliases exist; one concept, one variable. Defaults to the in-network
  address (`kafka:9092`) -- never hardcodes `localhost` (§9.3).
- `telemetry_topic` -- the one telemetry topic name (§6). No topic-prefix
  concept exists; YAGNI applies until a second real topic is needed.
- `producer_client_id` -- passed straight through as `confluent_kafka`'s
  `client.id` (`producer.py`). Useful for broker-side logs/diagnostics/
  client metrics only -- **not canonical identity**, never used for
  partitioning, deduplication, or any decision logic.
- `consumer_group_id` -- the one application-level default consumer
  group. `KafkaTelemetryConsumer(settings=..., group_id=None)` (the
  default) uses this verbatim -- a long-lived consumer needs no override.
  A caller that needs per-instance isolation derives its own group id
  FROM this configured base rather than an unrelated literal -- e.g.
  `f"{settings.consumer_group_id}-smoke-<uuid>"`
  (`scripts/e2e/smoke_streaming.py`, §9.4). This is the single
  configuration ownership model: the base lives in settings, any
  caller-side suffix is always derived from it, never independent.

**Deliberately not exposed as environment variables:** `acks`, `retries`,
`linger.ms`, `batch.size`, compression, `auto.offset.reset`,
`enable.auto.commit`, consumer session timeouts. These stay as explicit
code-level defaults in `producer.py` (`acks=all`) and `consumer.py`
(`auto.offset.reset` is a constructor parameter, not an env var;
`enable.auto.commit=True` is hardcoded) until a demonstrated override
need exists.

**Precedence** (pydantic-settings' own built-in behavior, not a custom
mechanism): explicit constructor kwarg > environment variable >
`.env.local`/`.env` file > the field default shown above. See
`packages/sceneops-streaming/tests/test_config.py` for a test proving
this ordering and confirming that no alias resolves this setting.

### 9.2 Kafka broker / Compose configuration

Infrastructure implementation details, never exposed as
`SCENEOPS_STREAMING_KAFKA_*` application settings and never duplicated
into `.env.example`'s streaming-client section -- they live entirely
inside `compose/streaming.yaml`'s `kafka` service definition:

```text
KAFKA_NODE_ID, KAFKA_PROCESS_ROLES, KAFKA_LISTENERS,
KAFKA_ADVERTISED_LISTENERS, KAFKA_LISTENER_SECURITY_PROTOCOL_MAP,
KAFKA_CONTROLLER_LISTENER_NAMES, KAFKA_INTER_BROKER_LISTENER_NAME,
KAFKA_CONTROLLER_QUORUM_VOTERS, KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR,
KAFKA_LOG_DIRS, KAFKA_AUTO_CREATE_TOPICS_ENABLE, CLUSTER_ID
```

None of these appear in `.env.example`. `KAFKA_HOST_PORT` (default
`9092`, in `.env.example`/`.env.local` under local-development
infrastructure, §9.3) is the one exception that IS user-facing, described
there only as the host-published development Kafka port -- not as part of
SceneOps transport semantics.

### 9.3 Internal vs. host listener addresses

```text
container-to-container (kafka:9092, PLAINTEXT)
  -- used by StreamingSettings.bootstrap_servers' default, and by any
     SceneOps container (e.g. the ROS2 bridge) on the sceneops network

host-to-Kafka (localhost:${KAFKA_HOST_PORT}, PLAINTEXT_HOST)
  -- used ONLY by scripts/e2e/smoke_streaming.sh, which overrides
     SCENEOPS_STREAMING_KAFKA_BOOTSTRAP_SERVERS at invocation time
     because make smoke-streaming runs on the HOST (uv run python, like
     every other scripts/e2e/* script), not inside a container on the
     sceneops network
```

No SceneOps container-side setting hardcodes `localhost`, and
`StreamingSettings`' code default is the in-network address, never the
host one. The `PLAINTEXT_HOST` listener exists specifically because this
real need (the host-run smoke test) requires it -- not for hypothetical
future use.

### 9.4 Smoke test's configuration path

`make smoke-streaming` never hardcodes a broker, topic, or consumer group
independently of `StreamingSettings` -- `scripts/e2e/smoke_streaming.py`
constructs one `StreamingSettings()` and reads `bootstrap_servers`/
`telemetry_topic`/`consumer_group_id` from it throughout;
`smoke_streaming.sh` only overrides the bootstrap-servers env var (§9.3)
before invoking the script. The one smoke-specific value is a
per-invocation consumer-group suffix, always derived from
`settings.consumer_group_id` (§9.1). Repeated `make smoke-streaming` runs
stay reliable because each invocation gets its own fresh group (no stale
committed-offset state to collide with), while still exercising the real
configured base group id as its prefix.

---

# Part 2: ROS2 Streaming Bridge

## 10. Goal and scope

The ROS2 streaming bridge connects real ROS2 telemetry to the Kafka
transport described in Part 1, without changing that transport's
contract:

```text
real nuScenes CAN
  -> ros2/nodes/can_replay_node.py
  -> real ROS2 DDS
  -> ros2/nodes/streaming_bridge_node.py
  -> TelemetryEnvelope
  -> KafkaTelemetryProducer
  -> real Kafka
  -> KafkaTelemetryConsumer
```

`make e2e-ros2-streaming` leaves zero Postgres/MinIO state, verified both
by construction (the bridge imports nothing DB/ArtifactStore-related --
see §12) and by direct observation (dataset row count unchanged across
real runs).

## 11. ROS2 topic mapping and source-timestamp contract

Audited directly against `ros2/nodes/can_replay_node.py` -- the actual
implementation, not assumed from
`docs/workflows/robot-run-and-mcap.md` alone. These five topics are the
only ones the replay node publishes.

| Topic | ROS2 type | Source | Representation | Semantic class |
| --- | --- | --- | --- | --- |
| `/vehicle/odom` | `nav_msgs/msg/Odometry` | nuScenes CAN `pose.utime` | `Odometry.header.stamp` | real observation |
| `/vehicle/imu` | `sensor_msgs/msg/Imu` | nuScenes CAN `ms_imu.utime` | `Imu.header.stamp` | real observation |
| `/vehicle/status` | `sensor_msgs/msg/BatteryState` | nuScenes CAN `vehicle_monitor.utime` | `BatteryState.header.stamp` | real observation |
| `/vehicle/control` | `std_msgs/msg/String` (JSON) | nuScenes CAN `vehicle_monitor.utime` (same record as `/vehicle/status`) | `source_timestamp_ns` JSON field | real observed feedback |
| `/mission/status` | `std_msgs/msg/String` (JSON) | replay-generated event time | `source_timestamp_ns` JSON field | synthetic replay event |

`utime` is nuScenes CAN's own timestamp convention -- integer
microseconds since the Unix epoch, converted to nanoseconds by
`ros2/nodes/can_timestamp.py`'s `can_timestamp_to_ns`, the single
conversion helper every publisher uses.

Header-bearing messages carry `source_timestamp_ns` as
`stamp.sec*1e9 + stamp.nanosec`, read verbatim by the bridge -- never
replaced by bridge receive time. `std_msgs/String` has no ROS `Header`,
so `/vehicle/control` and `/mission/status` thread the same
`source_timestamp_ns` concept through an explicit JSON field instead --
one shared wire convention, two different meanings: `/vehicle/control`'s
value is a real CAN observation time; `/mission/status`'s is this replay
session's own publish-time clock, because `/mission/status` is a
synthetic replay-boundary signal, not an original nuScenes sensor
channel -- there is no CAN time to recover for it. A missing or malformed
`source_timestamp_ns` field fails loudly (caught and counted by the
bridge's own failure handling, §14), never silently defaulting to another
time source.

`/vehicle/status` and `/vehicle/control` are derived from the *same*
`vehicle_monitor` CAN record -- one `utime` owns both emitted messages,
no ambiguity between the two. `pose`/`ms_imu` records map one-to-one to
`Odometry`/`Imu` messages; multiple source records are never combined
into one message.

`/vehicle/control` remains observed vehicle feedback
(`steering`/`throttle`/`brake`) -- the bridge never renames the channel
or reinterprets the payload as an autonomy-policy command.

Verified by direct comparison against the real source data
(`scripts/e2e/ros2_streaming_verify.py`): every consumed envelope's
`source_timestamp_ns` for a real-observation channel is checked for exact
match against the real CAN source file's `utime` values, not merely
checked for being non-zero. `/mission/status`'s timestamps are
independently checked to be disjoint from every real CAN timestamp in the
scene.

The message builders in `can_replay_node.py` never read replay rate or
wall-clock time when computing a CAN-derived timestamp -- only the pacing
between publishes depends on replay speed, so the same scene replayed at
different rates produces identical `source_timestamp_ns` values.

### 11.1 MCAP-readiness assessment

An assessment of whether each channel's timestamp is trustworthy enough
to build a durable capture (MCAP) from, per channel:

| Channel | Timestamp provenance | MCAP-ready? |
| --- | --- | --- |
| `/vehicle/odom` | Real nuScenes CAN `pose.utime` | **Yes** -- exact provenance-verified |
| `/vehicle/imu` | Real nuScenes CAN `ms_imu.utime` | **Yes** -- exact provenance-verified |
| `/vehicle/status` | Real nuScenes CAN `vehicle_monitor.utime` | **Yes** -- exact provenance-verified |
| `/vehicle/control` | Real nuScenes CAN `vehicle_monitor.utime` (same record as status) | **Yes** -- exact provenance-verified |
| `/mission/status` | Synthetic replay-event time (not a CAN observation) | **Documented limitation, not a defect**: an MCAP built from a streamed session preserves *when the replay boundary was observed*, never *an original mission timestamp* (none exists). A consumer that needs mission boundaries aligned to the other channels' real observation timeline must treat `/mission/status` as approximate/replay-relative, not as another real-time observation. |

No channel is unresolved or silently untrustworthy; `/mission/status`'s
limitation is a property of what the data IS (a synthetic boundary
marker), not a bug left to fix.

## 12. Bridge responsibility and boundaries

`ros2/nodes/streaming_bridge_node.py`'s `StreamingBridgeNode` has exactly
one responsibility: `ROS2 message -> TelemetryEnvelope -> TelemetryProducer`.
Verified by construction -- the file imports only `rclpy`/ROS2 message
packages, `sceneops_core.streaming`, and `sceneops_streaming`. It never
imports `sceneops-db`, `sceneops-storage`, Celery, Airflow, or anything
API/worker-side.

**Process boundary:** the bridge is a standalone ROS2 node, run inside the
same `ros2` Docker image/service `can_replay_node.py` already uses
(`compose/ros2.yaml`, profile `ros2`) -- not a separate image. This avoids
duplicating the full ROS2 environment: the `ros2/` image already has
`rclpy`, the message packages, and the MCAP plugin; it additionally
pip-installs `packages/sceneops-core`/`packages/sceneops-streaming`
(`ros2/Dockerfile`) so the bridge node can import them. It is not inside
`apps/api` or `apps/worker`.

## 13. Topic subscriptions, CDR encoding, sequence numbers, ingest timestamp

**Topic subscriptions** are centralized in one map, `TOPIC_SPECS`
(`streaming_bridge_node.py`) -- every `create_subscription` call the node
makes is derived from this one dict; nothing is scattered across ad hoc
callbacks. No dynamic topic discovery exists.

**CDR encoding.** Every payload is produced by real
`rclpy.serialization.serialize_message(msg)` -- never JSON, dict, or a
custom struct. `encoding = EnvelopeEncoding.ROS2_CDR` ("ros2-cdr", the
transport's frozen encoding identifier) is set uniformly. `message_type`
is always the canonical ROS2 interface type string
(`nav_msgs/msg/Odometry`, ...) -- never a SceneOps-specific alias;
`channel` is always the literal ROS2 topic name including its leading
slash (`/vehicle/odom`), never remapped.

**Sequence numbers.** One `int` counter (`StreamingBridgeNode._sequence`)
per `(robot_id, robot_run_id)` bridge process instance, incremented once
per message across all channels, not per-topic -- it represents
bridge-observed arrival order, independent of any per-topic identity. No
lock is needed: the node runs its default single-threaded executor
(`rclpy.spin_once` in a loop, `main()`), so subscription callbacks never
execute concurrently with each other. The sequence is never sorted by
`source_timestamp_ns`; a dedicated unit test
(`test_sequence_not_sorted_by_source_timestamp`) publishes a later source
timestamp first and confirms it still gets the earlier sequence number.

**Ingest timestamp.** Left entirely to `TelemetryEnvelope`'s own
`default_factory=time.time_ns` (§4's ownership rule) -- the bridge does
not capture or override it. Constructing the envelope inside
`_handle_message` IS accepting the message at the transport boundary in
this architecture; there is no earlier, more-precise acceptance instant
the bridge could capture, so introducing a second assignment point would
add complexity with no semantic benefit.

**Robot/RobotRun identity.** `--robot-id`/`--robot-run-id` are required
CLI arguments -- the bridge never derives or defaults them, and never
touches `sceneops-canonical/v0.0` or any canonical DB state. They are
transport metadata only. `scripts/e2e/e2e_ros2_streaming.sh` generates a
fresh `run-ros2-streaming-<epoch>-<pid>` per invocation, mirroring
`smoke_streaming.py`'s own per-invocation uniqueness pattern, so repeated
E2E runs never collide.

## 14. Backpressure, shutdown, and failure semantics

**Backpressure boundary:** `_AsyncProducerBridge` runs one real
`KafkaTelemetryProducer` on a dedicated background asyncio event loop for
the node's whole lifetime. A ROS2 callback's `publish()` call blocks the
calling (ROS2 callback) thread for at most `--publish-timeout-seconds`
(default 5.0s) waiting for the coroutine to complete on that loop. There
is no queue of the bridge's own -- the only buffering in this data path
is librdkafka's own internal client queue (inside
`confluent_kafka.Producer`), bounded by its own default configuration. A
stalled/unreachable broker surfaces as a timeout or exception in the
callback (caught, logged, counted -- never silently dropped), not
unbounded memory growth. This is a deliberately simple design for the
expected message volume (one nuScenes CAN-replay scene, low thousands of
messages over tens of seconds); whether it remains sufficient at
materially higher throughput is left to future benchmarking work, not
decided here.

**Graceful shutdown:** `main()` installs `SIGTERM`/`SIGINT` handlers that
set a `threading.Event`; the spin loop
(`rclpy.spin_once(node, timeout_sec=0.5)`) checks it every 0.5s. On
shutdown: stop spinning (no new callbacks) -> `node.shutdown()` (flush the
producer, bounded by a timeout, then close it) -> `node.destroy_node()`
-> `rclpy.shutdown()`. If any message failed during the run, the process
exits non-zero (`raise SystemExit(1)` in `main()`) -- a clear failure
signal. Verified directly: a real run terminated via `timeout <duration>`
(the same mechanism `ros2-can-replay-record`, `makefiles/ros2.mk`, uses to
bound `ros2 bag record`) logs `received signal 15 -- beginning graceful
shutdown` followed by `shutting down -- published=N failed=0` before
exiting, with every one of the `N` published records independently
confirmed present in Kafka afterward.

**Failure semantics:** `_handle_message` wraps serialize/construct/publish
in one `try`/`except Exception`. On failure: increment `failed_count`, log
a structured error (topic, exception type and message, running
published/failed counts) via `self.get_logger().error(...)`, and continue
processing subsequent messages -- never crash the node, never silently
drop. `self.get_logger()` is rclpy's own node logger, not Python's stdlib
`logging` -- it does not accept an `exc_info` keyword, so failures are
logged with the exception type and message formatted directly into the
text rather than passed as a separate parameter. "Unsupported message
type" cannot occur at runtime by construction: the node only ever
subscribes to topics listed in `TOPIC_SPECS`, so there is no code path
that receives a message type it doesn't already know how to handle. No
DLQ, no persistent retry storage -- deferred to a reliability boundary,
matching the transport's own invalid-message-handling approach (§7).

## 15. Compose/runtime integration and configuration

**Compose:** `compose/ros2.yaml`'s build context is the repo root (`.`)
so `ros2/Dockerfile` can `COPY packages/sceneops-core
packages/sceneops-streaming` in -- Docker `COPY` cannot reach outside its
build context, the same reasoning
`tools/nuscenes-integration/Dockerfile` documents for itself.
`ros2/Dockerfile` installs both via plain system `pip3 install
--break-system-packages` (not `uv`) -- `rclpy` lives in this image's
apt-managed system Python site-packages; a `uv`-managed venv would be
isolated from it and unable to `import rclpy`. The `ros2` service has an
`env_file: - .env.local` entry, matching every other service's own
convention, and a read-only `./scripts:/workspace/scripts:ro` mount
(matching `compose/workers.yaml`'s identical mount) so
`scripts/e2e/ros2_streaming_verify.py` can run inside the container with
real `rclpy`. No new service and no new Compose profile exist -- the
bridge runs as another invocation of the existing `ros2` service/profile,
exactly like `can_replay_node.py`.

**`make local-up` and `make streaming-up` are unaffected** -- neither
starts ROS2 or Kafka implicitly.

**Configuration:** the bridge takes `--robot-id`/`--robot-run-id`/
`--publish-timeout-seconds` as CLI arguments and otherwise reuses the
Kafka transport's `SCENEOPS_STREAMING_KAFKA_*` settings verbatim via
`StreamingSettings()` -- no bridge-specific Kafka setting aliases exist.
No per-topic environment variable exists; the topic/type map is the one
`TOPIC_SPECS` dict in code. `ROS_DOMAIN_ID` is not set -- the replay node
and bridge discover each other over the default ROS2 DDS domain on the
shared `sceneops-network` as separate `docker compose run` invocations.

## 16. Make surface and verification

One target: `make e2e-ros2-streaming` (`SCENE`/`RATE` overridable,
default `scene-0061`/`10.0`, matching `e2e-robot-can-replay`'s own
defaults). No `ros2-bridge-up`/`-down`/`-debug`/`streaming-topic-create`
exist -- internal orchestration (`scripts/e2e/e2e_ros2_streaming.sh`)
stays an implementation detail.

Three stages, all inside the `ros2` container:

1. `ros2/nodes/tests/` -- no Kafka, no live ROS graph.
   `test_can_timestamp.py` covers the pure CAN-to-nanosecond conversion;
   `test_can_replay_node.py` covers the pure message builders in
   `can_replay_node.py` against real CAN fixture timestamp values, with
   no live Node/publisher involved; `test_streaming_bridge_node.py` uses
   a `FakeProducerBridge` in place of `_AsyncProducerBridge`, with every
   payload produced by real
   `rclpy.serialization.serialize_message`/`deserialize_message`, never
   JSON-mocked.
2. Real `can_replay_node.py` + `streaming_bridge_node.py`, mirroring
   `ros2-can-replay-record`'s own background/foreground/`wait` pattern
   (`makefiles/ros2.mk`) -- the bridge is backgrounded, bounded by
   `timeout $DURATION`, the replay runs in the foreground, then the
   script waits for the bridge's own bounded shutdown.
3. `scripts/e2e/ros2_streaming_verify.py` -- consumes everything under
   that run's `robot_run_id` from the real broker, checks it against what
   the bridge itself reported publishing, and cross-checks every
   real-observation channel's `source_timestamp_ns` against the real CAN
   source JSON files directly (§11).

The verification checks, at a glance:

```text
every published message independently consumed from Kafka
robot_id/encoding/Kafka-key exact for every message
every source_timestamp_ns populated
all 5 channels observed; message_type exact per channel
every message on every channel decodes via real rclpy.deserialize_message
  as its declared ROS2 type
/mission/status: exactly 2 messages, "running" then "completed"
/vehicle/status count == /vehicle/control count (1:1 from vehicle_monitor)
/vehicle/control payload still has steering/throttle/brake -- never renamed
all RobotRun records in exactly one Kafka partition
sequence_number strictly increasing with no gaps, sorted by Kafka offset
  -- Kafka consumption order matches bridge publish order
real-observation channels: source_timestamp_ns is an exact member of the
  real CAN source file's utime set, for every message
/mission/status: source_timestamp_ns disjoint from every real CAN
  timestamp in the scene
```

Zero Postgres/MinIO writes -- confirmed both by construction (§12) and by
direct inspection of canonical table row counts before/after (unchanged).
`make smoke-streaming` (the Kafka transport's own smoke test) passes
independently of this E2E -- it proves the Kafka transport itself, and is
never replaced by the ROS2-specific E2E; they verify different
boundaries.

## 17. Terminology

```text
source timestamp      -- source_timestamp_ns; the observation/event time
                          (real CAN observation, or a documented synthetic
                          event time where no CAN observation exists)
ingest timestamp       -- ingest_timestamp_ns; when SceneOps accepted the
                          message at the streaming transport boundary
bridge-observed order  -- the sequence_number ordering; arrival order at
                          the bridge, independent of source_timestamp_ns
```

No other terms (`event time`, `sensor time`, etc.) are used as synonyms
for `source_timestamp_ns` in this document.

## 18. What comes next

Durable capture (writing streamed telemetry to MCAP) is the next
downstream boundary. This document does not describe that as implemented
-- no Kafka consumer in this repository writes MCAP files today.

```text
ROS2 / live robot -> stream envelope -> Kafka -> durable capture -> MCAP
  -> RobotRun -> existing Episode pipeline -> existing learning-data
  pipeline
```

## 19. Non-goals

Not built, not started, not partially wired -- listed so a future pass
doesn't mistake absence for a bug:

```text
MCAP writer / RobotRun streaming lifecycle
Episode generation from streamed data
Any Postgres/ArtifactStore write from the streaming path
Kafka Connect, Schema Registry, Avro
Spark/Flink stream processing
DLQ / automatic retry policy
Consumer lag metrics platform (Prometheus/Grafana)
Streaming UI
Mutation of canonical baseline sceneops-canonical/v0.0
Dynamic ROS2 topic discovery (the topic/type map is static)
Timestamp correction beyond what the bridge already reads from source data
Interpretation of /mission/status boundaries beyond proving transport
  preservation (Episode-building's use of mission boundaries is a
  downstream consumer's concern, not this transport's)
```

## 20. Source-of-truth map

**Kafka transport:**

- Transport-neutral contract: `packages/sceneops-core/sceneops_core/streaming/`, `packages/sceneops-core/tests/test_streaming_envelope.py`
- Default topic / header-prefix constants: `packages/sceneops-core/sceneops_core/constants/streaming.py`
- Kafka wire mapping + client implementation: `packages/sceneops-streaming/sceneops_streaming/{wire,producer,consumer,config,errors}.py`
- Wire/decode unit tests (no broker required): `packages/sceneops-streaming/tests/test_wire.py`
- Config surface/precedence unit tests (no broker required): `packages/sceneops-streaming/tests/test_config.py`
- Compose service: `compose/streaming.yaml`
- Make targets: `makefiles/streaming.mk` (`streaming-up`/`streaming-down`/`smoke-streaming`)
- Smoke test: `scripts/e2e/smoke_streaming.py`, `scripts/e2e/smoke_streaming.sh`

**ROS2 streaming bridge:**

- Bridge node: `ros2/nodes/streaming_bridge_node.py`
- Bridge unit tests (real rclpy, no Kafka, ros2 container only): `ros2/nodes/tests/test_streaming_bridge_node.py`
- CAN timestamp conversion helper + tests: `ros2/nodes/can_timestamp.py`, `ros2/nodes/tests/test_can_timestamp.py`
- CAN replay node (message builders + timestamp mapping) + tests: `ros2/nodes/can_replay_node.py`, `ros2/nodes/tests/test_can_replay_node.py`
- Container/runtime: `ros2/Dockerfile`, `compose/ros2.yaml`
- E2E: `scripts/e2e/e2e_ros2_streaming.sh`, `scripts/e2e/ros2_streaming_verify.py`, `make e2e-ros2-streaming` (`makefiles/streaming.mk`)

**Related ADRs:** [ADR-005](../adr/005-ros2-vs-kafka-boundary.md) (ROS2 vs. Kafka boundary), [ADR-003](../adr/003-batch-first-architecture.md) (why streaming waited until now)
