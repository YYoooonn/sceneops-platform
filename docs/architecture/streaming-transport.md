# Streaming Transport

> Describes how SceneOps moves robot telemetry through Kafka, and how the
> ROS2 streaming bridge feeds real ROS2 topics into that transport. Like
> [external-integration-runtime.md](./external-integration-runtime.md),
> this is a "what's actually built" document, not aspirational -- every
> claim below is checked against the code and against real, live runs
> (`make streaming-up && make smoke-streaming`, `make e2e-ros2-streaming`).
>
> Three parts: Part 1 covers the Kafka transport itself (envelope contract,
> wire format, delivery/ordering/partitioning semantics, configuration).
> Part 2 covers the ROS2 streaming bridge built on top of it. Part 3
> covers durable MCAP capture -- a run-scoped Kafka consumer
> (`ros2/capture/`) that writes what the bridge published back out to a
> validated, rosbag2-compatible MCAP file. No part changes another --
> each is a producer/consumer of the contract(s) established before it,
> not a modification of them.

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
current non-goals list, which also covers the ROS2 bridge and Part 3's
capture consumer). `make smoke-streaming` leaves zero canonical
(Postgres/MinIO) state -- verified by construction: nothing in
`sceneops-streaming` imports `sceneops-db` or an `ArtifactStore`. An MCAP
writer exists as Part 3's separate run-scoped Kafka consumer
(`ros2/capture/`), built on top of this transport -- it is not part of
`sceneops-streaming`/`sceneops-core`, and does not change anything
described in Part 1.

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
build context.
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

---

# Part 3: Durable MCAP Capture

## 18. Goal and scope

A run-scoped Kafka consumer that writes what the ROS2 streaming bridge
(Part 2) published back out to a validated, rosbag2/MCAP-compatible
file, using the exact writer `ros2 bag record` itself uses
(`rosbag2_py.SequentialWriter`) rather than a hand-rolled encoder:

```text
real Kafka (Part 1) -> ros2/capture (run-scoped consumer)
  -> validated, finalized local MCAP file
```

One invocation captures exactly one `(robot_id, robot_run_id)`. It
creates no canonical `RobotRun`, `Scene`, `Episode`, or
`ArtifactRecord`, and writes no Postgres/MinIO state -- verified by
construction (`ros2/capture/` imports neither `sceneops-db` nor
`ArtifactStore`) and by direct observation (`make e2e-streaming-capture`
leaves canonical table row counts unchanged). Registering a captured
file as a canonical `RobotRun` is the next, separate boundary (§27).

## 19. Package layout and placement

`ros2/capture/` -- flat scripts (no `__init__.py`), matching
`ros2/nodes/`'s own convention (absolute imports, e.g. `from
schema_registry import SUPPORTED_CHANNELS`, not relative ones -- these
modules are loaded via `sys.path.insert`, not as installed packages).
Runs inside the existing `ros2` Docker image/profile (`compose/ros2.yaml`
mounts `./ros2/capture:/workspace/capture:ro`) -- not a new service or
profile, and not inside `can_replay_node.py`, `streaming_bridge_node.py`,
or `apps/worker`: it needs `rosbag2_py` (apt-installed only in the `ros2`
image) for standard-format MCAP writing, and `mcap`/`mcap-ros2-support`
(`ros2/Dockerfile`) for the mandatory pre-finalize read-back validation
(§25) -- neither dependency leaks into `sceneops-core`, `apps/api`, or
any general domain package.

```text
ros2/capture/
  schema_registry.py    static v1 supported channel/type set
  mcap_writer.py         McapCaptureWriter (rosbag2_py.SequentialWriter)
  validation.py           pre-finalize MCAP read-back validation
  finalize.py             temp/final bag directory lifecycle
  capture_consumer.py     RunFilter, SequenceTracker, CaptureResult, run_capture()
  cli.py                  CLI entry point (make e2e-streaming-capture)
  tests/                  pytest, runs only inside the ros2 container
```

## 20. Frozen time mapping

Audited directly against `apps/worker/sceneops_worker/datasets/ingestion/
rosbag_raw_log.py`'s `RosbagAdapter` -- the actual downstream reader, not
assumed. `_read_bag()` derives every timestamp (`RobotState.timestamp_us`,
`Mission.started_at`/`ended_at`, frame timestamps, raw-log
`time_range`) from `message.log_time` alone; `publish_time` and the
CDR-decoded `header.stamp` are never read for timing anywhere in that
file (`header.stamp` is only read for value fields, e.g.
position/orientation).

This contradicts the naive assignment (`publish_time = source`,
`log_time = ingest`) -- so the mapping is the deliberate inverse:

```text
MCAP log_time      = TelemetryEnvelope.source_timestamp_ns
MCAP publish_time   = TelemetryEnvelope.ingest_timestamp_ns
```

putting the envelope's real source-observation/event time in the one
field `RosbagAdapter` actually reads. Verified both ways: a live
write-then-readback probe (`rosbag2_py.SequentialWriter.write(topic,
payload, log_time, publish_time)`, 4-arg form) against the real writer
confirmed this exact argument-to-field mapping; `ros2/capture/tests/
test_mcap_writer.py` asserts it as a permanent regression test. Mission
segmentation is unaffected either way -- `_missions_from_bag` only
compares mission messages against each other (`min`/`max` of `log_time`
among `/mission/status` records), never against `RobotState` timestamps,
so this mapping choice cannot silently break mission boundaries.

One documented consequence: `/mission/status`'s `source_timestamp_ns` is
a synthetic replay-boundary time, not a CAN observation (§11.1) -- an
MCAP built from a streamed session will show `/mission/status`'s
`log_time` far from the CAN-derived channels' `log_time` values (2018
CAN data vs. present-day replay time). This is expected, a property of
the data's actual semantics per §11.1, not a bug introduced by capture.

## 21. Commit-boundary ordering (durability guarantee)

Frozen, never reversed:

```text
consume -> write to the temp/partial MCAP -> close the writer (fsync)
  -> validate by reading the file back -> atomically finalize
  (rename + fsync parent dir) -> commit Kafka offsets
```

If anything before the Kafka commit fails, `run_capture()`
(`capture_consumer.py`) raises without committing -- the partial bag is
left in place for the next attempt to discard and rebuild from Kafka
(§25), never appended to. `KafkaTelemetryConsumer` gained
`enable_auto_commit`/`commit()` (Part 1's `consumer.py`) specifically for
this: `enable.auto.commit=False` is REQUIRED for capture (durability must
never depend on librdkafka's periodic background commit), and `commit()`
synchronously commits only up through the most recently polled record,
called exactly once, after finalize succeeds.

A dedicated regression test enforces this ordering, not just the
behavior: `test_run_capture_commits_only_after_finalize`
(`ros2/capture/tests/test_capture_consumer.py`) spies on both
`finalize_bag` and the consumer's `commit()` and asserts
`call_order == ["finalize", "commit"]` -- it fails if `run_capture` is
ever edited to call `commit()` before (or without) `finalize_bag()`.

## 22. Supported channels (static v1 registry)

`schema_registry.SUPPORTED_CHANNELS` is the only source of channel/type
validation -- no dynamic ROS2 topic/type discovery. An envelope naming a
channel or `message_type` outside this set raises
`UnsupportedChannelError` and aborts the capture attempt (§21's ordering
means nothing gets finalized or committed):

```text
/vehicle/odom       nav_msgs/msg/Odometry
/vehicle/imu        sensor_msgs/msg/Imu
/vehicle/status     sensor_msgs/msg/BatteryState
/vehicle/control    std_msgs/msg/String
/mission/status     std_msgs/msg/String
```

The same five channels Part 2's bridge publishes (§11) -- `mcap_writer.py`
never needs an imported ROS2 message class to write a message:
`rosbag2_py` resolves the schema from the type string alone against the
installed ROS2 interface definitions, and the payload is opaque CDR
bytes (§23 confirms these pass through byte-for-byte).

## 23. Partition invariant and run filtering

One `robot_run_id` must map to exactly one Kafka partition (Part 1's own
partitioning contract, §6, gives this for free under a stable partition
count) -- `_RunFilter` (`capture_consumer.py`) enforces it explicitly
rather than assuming it: the first accepted message's partition is
recorded, and any later message for the same `robot_run_id` on a
different partition raises `PartitionInvariantError` immediately, aborting
the capture (never silently merging two partitions' data into one file).
`_RunFilter` also discards every message that doesn't match the target
`(robot_id, robot_run_id)` -- one capture invocation only ever writes one
run's messages, regardless of what else is interleaved on the topic.

Raw CDR payload bytes are never touched: `Kafka record value ==
TelemetryEnvelope.payload == the written MCAP Message.data`, verified
both by a targeted unit test
(`test_raw_cdr_payload_bytes_are_preserved_exactly`, all 256 byte values
exercised) and by the real end-to-end run (§26).

## 24. Sequence integrity and duplicate policy

`_SequenceTracker` (`capture_consumer.py`) expects `sequence_number` 0
through N-1 in the order Kafka delivers them, with a bounded v1 policy
(no unbounded dedup table -- only the single last-accepted
`(sequence, payload)` pair is ever remembered):

```text
first message's sequence must be 0                -> else fail
in-order (sequence == last + 1)                    -> accept, write
exact immediate redelivery (same sequence AND
  same payload as the last accepted message)       -> skip (not an error)
conflicting redelivery (same sequence, different
  payload)                                          -> fail
gap (sequence > expected next)                      -> fail
late/out-of-order (sequence < expected next, not
  the immediate-redelivery case above)               -> fail
```

"Fail" here means `SequenceIntegrityError`, which aborts the capture
attempt the same way `PartitionInvariantError`/`UnsupportedChannelError`
do -- per §21's ordering, nothing gets finalized or committed, so a
restart safely rebuilds from the last committed offset rather than
silently accepting corrupted sequencing.

## 25. Temp/final file lifecycle

`rosbag2_py.SequentialWriter` writes into a *directory* (`metadata.yaml`
plus one or more `.mcap` files), not a single file -- the unit that must
move atomically from "being written" to "durably captured" is that whole
directory. Layout, per `robot_run_id`, under one capture `output_root`:

```text
<output_root>/.partial/<robot_run_id>/   -- write target (in progress)
<output_root>/<robot_run_id>/            -- finalized (atomically renamed)
```

`prepare_partial_bag_dir()` (`finalize.py`) never appends to or resumes a
stale `.partial` directory from a previous crashed/interrupted attempt --
it discards it (`shutil.rmtree`) and lets the writer recreate it from
scratch, because the source of truth for what belongs in a capture is
Kafka, replayed from the last *committed* offset (always before anything
a stale partial could contain under §21's ordering), never whatever bytes
happen to already be on disk. `rosbag2_py.SequentialWriter.open()`
itself refuses to open into a directory that already exists (even
empty), verified directly -- so `prepare_partial_bag_dir()` guarantees
the path does *not* exist and its parent does, rather than creating it
itself.

`finalize_bag()` performs the atomic transition: `os.replace()` (atomic
within one filesystem, guaranteed here since both paths share
`output_root`) followed by an `fsync` of `output_root`'s directory entry.
It never overwrites an existing final bag -- a second finalize attempt
for the same `robot_run_id` raises `FinalBagExistsError`, leaving both
the original final bag and the new attempt's `.partial` directory
untouched, rather than silently discarding either.

Before finalizing, `validate_mcap_file()` (`validation.py`) reads the
just-closed MCAP back with the same `mcap` reader package
`RosbagAdapter` uses (never trusts the writer's own in-memory counters),
and raises `McapValidationError` -- refusing to finalize -- on a
corrupt/unreadable file, a written-vs-read-back message count mismatch,
or zero messages.

## 26. Capture lifecycle, configuration, and reliability scope

**Lifecycle is externally controlled.** `run_capture()` has no built-in
notion of "done" and never inspects `/mission/status` payload content to
decide when to stop -- a caller supplies `stop_condition(message_count)`,
polled before every Kafka poll. `cli.py` offers two mutually exclusive
policies: `--max-messages N` (deterministic, used by `make
e2e-streaming-capture`) and `--idle-timeout-seconds S` (stop after `S`
seconds with no new matching message).

**Configuration -- frozen in code, not environment variables:**

```text
CAPTURE_CONSUMER_GROUP_ID = "sceneops-mcap-capture"
  -- a CAPTURE CONSUMER-GROUP BASE, not the literal Kafka group.id any
     capture attempt actually uses (see below) -- independent from Part
     1's general SCENEOPS_STREAMING_KAFKA_CONSUMER_GROUP_ID default;
     capture never shares committed-offset state with any other
     consumer, generic or otherwise.
auto.offset.reset = "earliest"   -- correctness-first: a capture that
  starts after some of a run's messages were already published must
  still see all of them, not just whatever arrives from "now".
enable.auto.commit = False       -- required; see §21.
```

**Run-scoped consumer groups (`ros2/capture/group_id.py`).** Every
capture attempt derives its own Kafka `group.id` from the base above and
its target `robot_run_id` (`derive_capture_group_id`) -- it never passes
the bare base to `KafkaTelemetryConsumer` directly. Two independent
RobotRuns therefore never share committed-offset state, even when
interleaved on the same partition: one run's poll loop reading past
another run's messages (to reach its own target count) can no longer
silently advance the other run's committed position, because there is
no longer one shared position to advance. The same `robot_run_id`
always derives the same group (a pure function of its inputs, never
Python's randomized `hash()`), so retries land on the same group and
offset lifecycle every time; different `robot_run_id`s derive different
groups with cryptographic-hash collision resistance (a SHA-256 digest
suffix, not the human-readable slug prefix alone, which is cosmetic
only). Centralized in one module so capture code and any tooling that
needs to know a run's own group (tests, ops/benchmark scripts querying
Kafka consumer-group lag) derive it identically, never ad hoc.

**Tradeoff, not fully solved:** this provides correct, isolated replay
per RobotRun -- it does not provide efficient large-scale multi-run
capture. Each run-scoped group is, the first time it's used, a brand
new Kafka consumer group with no committed offset, so `auto.offset.reset
= earliest` means it may scan the topic's entire historical record
before reaching its own messages. On a topic that has accumulated a
large volume of unrelated history (this repeatedly happens in local
dev, where the same topic persists across many test/benchmark runs),
that rescan cost can dominate a capture's wall-clock time -- measured
directly: a 3,000-message capture that took ~3.4s against a
lightly-used topic took ~44s once the topic had accumulated roughly
150,000 prior messages from earlier benchmark runs. This is an accepted
tradeoff for v1 correctness, not a regression to chase -- see
[Streaming reliability & scale baseline](./streaming-reliability-scale-baseline.md)'s
Phase 6.6.1 addendum. Adding partitions or otherwise redesigning topic
layout to bound this cost is explicitly out of scope here.

None of these are `SCENEOPS_STREAMING_KAFKA_*` settings and none are
configurable via environment variable -- deliberately, matching Part 1's
own "explicit code-level default until a demonstrated override need
exists" policy (§9.1). No `MCAP_LOG_TIME_MODE`, `MCAP_PUBLISH_TIME_MODE`,
`CAPTURE_DEDUP_MODE`, or `CAPTURE_OFFSET_RESET` variable exists.

**`CaptureResult`** (`capture_consumer.py`), returned once Kafka offsets
are committed:

```text
robot_id, robot_run_id, path, message_count, partition,
first_offset, last_offset, first_sequence, last_sequence, sha256
```

Not a `RobotRun` -- it describes a local file and its Kafka provenance
only; nothing here is a canonical record (§18).

**Reliability scope (deferred, not this work):** process crash/restart
across capture invocations, multi-instance coordination, and
exactly-once capture guarantees beyond one invocation's own
commit-after-finalize ordering are out of scope -- a reliability boundary
for later work, matching Part 1's own DLQ/retry deferral (§7). A crashed
capture's `.partial` directory is always safely discardable on the next
attempt (§25); that is the extent of the crash-safety this work provides.

## 27. Make surface and verification

One target: `make e2e-streaming-capture` (`SCENE`/`RATE` overridable,
same defaults as `e2e-ros2-streaming`). No `capture-up`/`-down`/CLI-only
alias exists -- internal orchestration
(`scripts/e2e/e2e_streaming_capture.sh`) stays an implementation detail.

Five stages:

1. `ros2/capture/tests/` -- pure unit tests (schema registry, writer,
   finalize, validation, `RunFilter`/`SequenceTracker`/duplicate-policy
   logic, and the durability-ordering test, §21), no Kafka, no live ROS
   graph. 35 tests.
2. Real `can_replay_node.py` + `streaming_bridge_node.py` -> Kafka (same
   pattern as `e2e-ros2-streaming`, §16) -- the bridge's own reported
   published count becomes this capture's `--max-messages` value.
3. `cli.py` captures that run from the real broker into a finalized MCAP.
4. A second, independent CAN replay recorded directly via
   `ros2 bag record` (the existing oracle path,
   `ros2-can-replay-record`'s own pattern) to a scratch path under
   `data/tmp_streaming_capture/` -- never `data/raw/rosbag/<scene>`, the
   canonical baseline dataset location.
5. `scripts/e2e/mcap_capture_verify.py` (host, `uv run` -- `RosbagAdapter`
   needs no ROS2/rclpy install) opens the captured MCAP through the same
   `RosbagAdapter` apps/worker uses for real ingestion (mandatory
   compatibility check, `extract_episode_source()` only -- no DB writes,
   no `RobotRun`/`Episode` creation) and compares it against the
   direct-recorded bag for **semantic** equivalence, not byte-identity:

```text
captured message_count == bridge's own published count
captured first_sequence == 0
captured and direct-recorded bags expose the same topic/schema set
RosbagAdapter opens the captured MCAP without error
captured bag: robot_states non-empty, missions non-empty
robot_state count within tolerance of the direct-recorded bag's own
  count (a few-message delta is expected -- two INDEPENDENT replay
  invocations, each subject to its own ROS2 DDS discovery-lag at
  startup, and RosbagAdapter dedupes RobotState by microsecond
  timestamp; exact equality across two separately-timed replays is not
  the right oracle)
mission count and mission_ids match exactly between captured and
  direct-recorded bags
```

Verified against real scene-0061 data: a real run captured all 2915
bridge-published messages with `first_sequence=0`, and every check above
passed, including the mandatory `RosbagAdapter` read-back. Zero
Postgres/MinIO writes -- confirmed both by construction (§18) and by
direct inspection of canonical table row counts before/after (unchanged).

# Part 4: Continuous Multi-Run Capture

## 28. Goal and scope

One long-lived Kafka consumer that routes each consumed record by
`TelemetryEnvelope.robot_run_id` to that run's own, independently
sequence-tracked, independently written MCAP -- replacing N independent
full-topic-history rescans (one per `RunScopedCapture` invocation,
Part 3's own path) with one continuous topic pass serving arbitrarily
many concurrent RobotRuns:

```text
real Kafka (Part 1) -> ContinuousCaptureRouter (one long-lived consumer)
  -> N independently tracked, independently finalized local MCAP files
```

`ContinuousCaptureRouter` (`ros2/capture/router.py`) composes Part 3's
own building blocks (`_RunFilter`, `_SequenceTracker`, `_sha256_file`,
`finalize.py`/`validation.py`/`mcap_writer.py`) directly -- it does not
reimplement or modify any of them. The frozen commit-boundary ordering
Part 3 encodes (write -> close/fsync -> validate -> atomically finalize
-> only then advance Kafka position, §21) is preserved per-session; see
§31 for how that ordering composes across many simultaneously-open
sessions sharing one partition's committed offset.

`RunScopedCapture` (`capture_consumer.run_capture()`, Part 3) is
untouched and remains the supported path for replay/backfill/debugging/
recovery -- the router is an additional, independent consumer of the
same frozen Kafka/envelope/MCAP contracts, not a replacement.

## 29. CaptureSession lifecycle

Each RobotRun the router observes gets its own `CaptureSession`,
tracked through an explicit `SessionState`:

```text
DISCOVERED -> RECORDING -> FINALIZING -> FINALIZED
                                       -> FAILED
```

- `DISCOVERED` -- a session exists (explicit `RUN_START` seen, or
  implicitly created by that run's first telemetry record) but has not
  yet had any telemetry successfully written.
- `RECORDING` -- at least one telemetry record has been written.
- `FINALIZING` -- transient: finalize I/O (validate/atomic-rename) in
  progress.
- `FINALIZED` -- terminal, successful. `result` may still be `None` if
  the session finalized with zero telemetry ever written (e.g.
  `RUN_START` immediately followed by `RUN_END`) -- there is no file to
  describe in that case.
- `FAILED` -- terminal, unsuccessful (sequence/partition/writer
  invariant violation, or a finalize-time I/O failure). Data is
  discarded, never finalized, never silently repaired.

A session reaches `FINALIZED`/`FAILED` through one of three signals: an
explicit `RUN_END` control envelope (§30), a per-session idle-timeout
fallback (`session_idle_timeout_seconds`, `None` disables it) for when
a producer disappears without ever sending `RUN_END`, or caller-driven
shutdown. A per-run error (`PartitionInvariantError`,
`SequenceIntegrityError`, `UnsupportedChannelError`) moves only that
session to `FAILED` -- the router keeps serving every other session
unaffected.

`max_active_runs` (default `64`) bounds concurrent non-terminal
sessions; exceeding it raises `MaxActiveRunsExceededError` rather than
silently evicting one -- no eviction policy beyond idle-timeout exists.

`CaptureSession`/`SessionState` is execution/runtime state only --
in-process, never persisted, never a canonical domain record. It is
not `RobotRun`, and nothing here writes to PostgreSQL/ArtifactStore
(matching Part 3's own scope, §18).

## 30. Lifecycle control envelopes

`RUN_START`/`RUN_END` (`sceneops_core.streaming.control`) are an
additive signal layered onto the existing `TelemetryEnvelope`/topic/
wire contract -- not a new schema, not a new topic. A control event IS
a `TelemetryEnvelope`: same required fields, same
`robot_id`/`robot_run_id`, same Kafka key (`robot_run_id`, via
`wire.partition_key`, entirely unchanged), same topic. What makes it a
control event rather than telemetry is purely its `channel`/
`message_type` (`SESSION_CONTROL_CHANNEL = "/session/control"`,
reserved, never a real ROS2 topic/interface, and never present in
`ros2/capture/schema_registry.py`'s `SUPPORTED_CHANNELS`).

Reusing the existing topic/key, rather than a separate control topic,
is deliberate: partitioning by `robot_run_id` (frozen, §6) guarantees a
control event lands on the same partition as that run's own telemetry,
which is what "ordered consistently with that run's telemetry"
requires -- Kafka only guarantees ordering within one partition of one
topic, never across two. The cost is that every consumer of the topic
sees these records too; `is_control_envelope()` makes them trivially
filterable by any consumer that doesn't care about lifecycle.

A control envelope's `sequence_number` does not share that run's
telemetry sequence counter -- the bridge reads its own, independent
live counter without incrementing it, so `RUN_START` always lands at
sequence_number `0`, the same position the first real telemetry
message also needs in its own space (§32 covers why this is safe).

## 31. Offset-commit safety across concurrent sessions

One continuous consumer group can only commit one position per
partition, but the router serves many sessions at different points in
that partition's history at the same time. The router never commits a
partition's offset past the earliest position any still-active
(non-terminal) session on it still needs:

```text
safe_offset(partition) = min(session.first_offset for session in
  non-terminal sessions on that partition), or last_consumed_offset + 1
  when no session is currently active
```

`commit_safe()` applies this after every finalize and periodically
during polling. Consequence: if the router crashes, every
not-yet-finalized session's partial state is left on disk,
un-finalized, with Kafka offsets never committed past it -- a fresh
router (or a `RunScopedCapture`, re-consuming from the committed
position under its own run-scoped group) sees that data again rather
than silently losing it.

## 32. RunScopedCapture compatibility with control envelopes

`RunScopedCapture` (Part 3) is unmodified in its core loop and remains
fully supported; it did not originally filter by channel at all -- any
record its `_SequenceTracker` accepted was passed unconditionally to
`McapCaptureWriter.write_envelope()`. Because a control envelope now
legitimately shares that run's `robot_run_id` (§30's design), an
unfiltered control envelope would reach the writer and raise
`UnsupportedChannelError` for `/session/control` (correctly -- it is
not in `SUPPORTED_CHANNELS`) -- and `run_capture()` has no per-message
error isolation (that is the router's own, newer behavior), so the
exception would propagate straight out and abort the entire capture.

Fixed: control envelopes are recognized via `is_control_envelope()` and
routed to their own, independent `_SequenceTracker` ("control
tracker") -- validated for gap/duplicate/conflict exactly as strictly
as telemetry, in a sequence space that never collides with telemetry's
own (both legitimately start at `0`, in independent spaces, per §30) --
but never passed to the MCAP writer, so a control envelope never
appears in a finalized MCAP and never triggers
`UnsupportedChannelError`. A legacy stream with no control events at
all is completely unaffected: `is_control_envelope()` is never true
for it, so every record takes the exact path this module always used.

## 33. Make surface, verification, and current limitations

No CLI entry point or make/e2e target runs `ContinuousCaptureRouter`
against a live scenario the way `cli.py`/`make e2e-streaming-capture`
exercises `RunScopedCapture`. Verification is unit tests
(`ros2/capture/tests/test_router.py`,
`test_lifecycle_integration.py`) plus real-Kafka integration tests
(`test_router_integration.py`, `test_lifecycle_integration.py`'s
real-broker cases) -- these already run as part of
`make e2e-streaming-capture`'s stage 1 (the full `ros2/capture/tests/`
suite, §27), so router/lifecycle correctness is covered by the
existing make surface without a dedicated new target.

Current limitations, verified against code:

```text
No persistence of CaptureSession across process restart -- a crashed
  router loses all in-memory session state; recovery relies entirely
  on Kafka's committed-offset safety (§31), not on reconstructing
  session state.
No Kafka consumer-group rebalance recovery -- the router assumes one
  process holding one partition assignment for its whole lifetime.
No multi-worker / multi-partition router -- one ContinuousCaptureRouter
  instance serves one partition.
RunScopedCapture and a lifecycle-enabled bridge run can now coexist on
  the same robot_run_id (control envelopes are tolerated, §32), but
  the bridge's --emit-lifecycle-events still defaults to False at
  every layer, including the CLI -- not flipped on by default, since
  doing so changes the real Kafka wire output of every consumer of
  that topic, not just router-fed ones.
```

## 34. What comes next

The full chain from live telemetry through to a readable learning
dataset is built: durable capture (Part 3) or continuous multi-run
capture (Part 4) both close `Kafka -> MCAP`; the database-free Recording
Publisher plus `REGISTER_ROBOT_RUN` (`docs/workflows/robot-run-and-mcap.md`
§3.2) close `MCAP -> MCAP + RobotRunManifest -> ArtifactRecords + RobotRun`;
and the verified
recording resolver (`sceneops_worker.robots.resolver`,
`docs/workflows/robot-run-and-mcap.md` §3.1) closes
`RobotRun -> existing Episode pipeline / robot-state ingestion`, keeping
`RosbagAdapter` itself storage-agnostic throughout.

```text
ROS2 / live robot -> stream envelope -> Kafka -> durable capture
  (Part 3, one run) or continuous capture (Part 4, many concurrent
  runs) -> validated local MCAP -> Recording Publisher
  -> REGISTER_ROBOT_RUN
  -> resolve_recording(robot_run_id) -> existing Episode pipeline
  -> existing learning-data pipeline
```

Publication and registration are explicit steps today -- `python -m
sceneops_integrations.recording publish` against a finalized MCAP, then
`POST /robot-runs:register`; nothing in the capture or router path
triggers either automatically on finalize. Capture itself stays DB-free
and never writes RobotRun state.

Canonical Scenes are built from a registered RobotRun's recording by
`RECORDING_SCENE_BUILDING` ([Scene domain](./scene-domain.md) §6). Capture
does not record sensor, tf or CameraInfo channels yet, so today only
batch-acquired recordings carry the channels it needs.

Reliability and scale characteristics of everything above -- crash
boundaries, duplicate/gap/out-of-order handling, multi-RobotRun
isolation, Kafka-outage behavior, backpressure, throughput/memory at
scale, practical payload limits -- are measured and frozen in
[Streaming reliability & scale baseline](./streaming-reliability-scale-baseline.md)
and [Multi-run streaming architecture study](./streaming-multirun-phase7-study.md),
point-in-time records, not living contracts.

## 35. Non-goals

Not built, not started, not partially wired -- listed so a future pass
doesn't mistake absence for a bug:

```text
Automatic publication/registration of a finalized capture
  (the mechanism exists -- Recording Publisher + POST /robot-runs:register
  -- but nothing invokes it without an explicit operator/caller step, §34)
Episode generation from streamed data
Any Postgres/ArtifactStore write from the streaming or capture path
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
Capture crash/restart reliability beyond the single-invocation,
  single-partition guarantees Part 3 describes (§26) -- process
  supervision, multi-instance coordination, and exactly-once capture
  across restarts are a separate reliability boundary, deferred
Multi-partition-per-robot_run_id support (Part 3 fails loudly instead,
  §23) or dynamic capture topic/channel configuration (the supported
  channel set is a static registry, §22)
ContinuousCaptureRouter session persistence across restart, Kafka
  rebalance recovery, and multi-worker/multi-partition router
  instances (Part 4, §33)
Bridge lifecycle-event emission on by default (technically compatible
  since §32, deliberately not flipped -- §33)
```

## 36. Source-of-truth map

**Kafka transport:**

- Transport-neutral contract: `packages/sceneops-core/sceneops_core/streaming/`, `packages/sceneops-core/tests/test_streaming_envelope.py`
- Default topic / header-prefix constants: `packages/sceneops-core/sceneops_core/constants/streaming.py`
- Kafka wire mapping + client implementation: `packages/sceneops-streaming/sceneops_streaming/{wire,producer,consumer,config,errors}.py`
- Wire/decode unit tests (no broker required): `packages/sceneops-streaming/tests/test_wire.py`
- Config surface/precedence unit tests (no broker required): `packages/sceneops-streaming/tests/test_config.py`
- Manual-commit (`enable_auto_commit`/`commit()`) unit tests (no broker required): `packages/sceneops-streaming/tests/test_consumer.py`
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

**Durable MCAP capture:**

- Schema registry: `ros2/capture/schema_registry.py`, `ros2/capture/tests/test_schema_registry.py`
- MCAP writer: `ros2/capture/mcap_writer.py`, `ros2/capture/tests/test_mcap_writer.py`
- Pre-finalize validation: `ros2/capture/validation.py`, `ros2/capture/tests/test_validation.py`
- Temp/final lifecycle: `ros2/capture/finalize.py`, `ros2/capture/tests/test_finalize.py`
- Consumer orchestration (`RunFilter`/`SequenceTracker`/`CaptureResult`/`run_capture`, durability-ordering test): `ros2/capture/capture_consumer.py`, `ros2/capture/tests/test_capture_consumer.py`
- Run-scoped consumer-group derivation: `ros2/capture/group_id.py`, `ros2/capture/tests/test_group_id.py`
- Multi-RobotRun isolation (real Kafka): `ros2/capture/tests/test_multi_robot_run_integration.py`
- CLI entry point: `ros2/capture/cli.py`
- Container/runtime deps (`mcap`/`mcap-ros2-support`): `ros2/Dockerfile`, capture source mount: `compose/ros2.yaml`
- E2E: `scripts/e2e/e2e_streaming_capture.sh`, `scripts/e2e/mcap_capture_verify.py`, `make e2e-streaming-capture` (`makefiles/streaming.mk`)
- RosbagAdapter (the mandatory compatibility-check target): `apps/worker/sceneops_worker/datasets/ingestion/rosbag_raw_log.py`

**Continuous multi-run capture:**

- Router, `CaptureSession`/`SessionState`, offset-commit safety: `ros2/capture/router.py`
- Router unit tests: `ros2/capture/tests/test_router.py`
- Router real-Kafka integration tests: `ros2/capture/tests/test_router_integration.py`
- Lifecycle control envelopes (`RunEventType`, `build_control_envelope`/`is_control_envelope`/`parse_run_event`): `packages/sceneops-core/sceneops_core/streaming/control.py`, `packages/sceneops-core/tests/test_streaming_control.py`
- `SESSION_CONTROL_CHANNEL` constant: `packages/sceneops-core/sceneops_core/constants/streaming.py`
- Bridge lifecycle-event publishing (`--emit-lifecycle-events`): `ros2/nodes/streaming_bridge_node.py`, `ros2/nodes/tests/test_streaming_bridge_node.py`
- `RunScopedCapture` control-envelope compatibility (`control_tracker`): `ros2/capture/capture_consumer.py`, `ros2/capture/tests/test_capture_consumer.py`
- Real-Kafka lifecycle integration (solo and interleaved runs): `ros2/capture/tests/test_lifecycle_integration.py`
- Point-in-time design/benchmark record: [Multi-run streaming architecture study](./streaming-multirun-phase7-study.md)

**Related ADRs:** [ADR-005](../adr/005-ros2-vs-kafka-boundary.md) (ROS2 vs. Kafka boundary), [ADR-003](../adr/003-batch-first-architecture.md) (why streaming waited until now)
