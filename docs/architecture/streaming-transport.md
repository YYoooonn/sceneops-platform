# Streaming Transport

> Describes how SceneOps moves robot telemetry and sensor data through
> Kafka, and how the ROS2 streaming bridge feeds real ROS2 topics into that
> transport. Like
> [external-integration-runtime.md](./external-integration-runtime.md),
> this is a "what's actually built" document, not aspirational -- every
> claim below is checked against the code and against real, live runs
> (`make streaming-up && make test-infrastructure SUITE=kafka`,
> `make e2e-streaming-equivalence`).
>
> Four parts: Part 1 covers the Kafka transport itself (envelope contract,
> wire format, delivery/ordering/partitioning semantics, configuration).
> Part 2 covers the ROS2 streaming bridge built on top of it. Part 3
> covers durable MCAP capture -- a run-scoped Kafka consumer
> (`ros2/capture/`) that writes what the bridge published back out to a
> validated L1 raw recording (ADR-007 §29.5). Part 4 covers the run
> lifecycle events that bound a captured run. No part changes another -- each is a
> producer/consumer of the contract(s) established before it, not a
> modification of them.

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
[ADR-003](../adr/003-batch-first-architecture.md). Batch acquisition (the
external acquisition tool's MCAP sink -> Recording Publisher ->
`REGISTER_ROBOT_RUN`, see
[robot-run-and-mcap.md](../workflows/robot-run-and-mcap.md)) is independent
of this transport and converges with it on the same L1 recording contract
(ADR-007 §29.12).

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
├── source_timestamp_ns     int >= 0, required -- the source message's own timestamp, verbatim;
│                          0 = the message carries a zero (unstamped) header, see §11
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

The ROS2 streaming bridge connects real ROS2 topics -- telemetry, sensor
and transform channels -- to the Kafka transport described in Part 1,
without changing that transport's contract:

```text
robot, or a replay of a locked reference MCAP
(tools/dataset-acquisition, `reference replay`)
  -> real ROS2 DDS
  -> ros2/nodes/streaming_bridge_node.py
  -> TelemetryEnvelope
  -> KafkaTelemetryProducer
  -> real Kafka
  -> ros2/capture -> L1 MCAP -> Recording Publisher -> REGISTER_ROBOT_RUN
```

The bridge is source-agnostic: it knows ROS2 topics, types and where a
message carries its source timestamp, never a dataset format. The replay
sink of the external acquisition tool is one publisher of those topics
(§10.1); a robot's own stack is another. `make streaming-bootstrap` drives
the whole path through containers and FastAPI into the Reference Contract's
Streaming Acquisition RobotRuns.

### 10.1 Replay as the external-tool boundary

`tools/dataset-acquisition` turns a source into tool-local acquisition
events (topic, message type, CDR payload serialized once, source time) and
feeds two sinks: the batch MCAP sink and the ROS2 replay sink (image
`sceneops-platform/dataset-replay:local`: ROS2 Jazzy plus the tool, no
SceneOps package). A dataset adapter feeds the batch sink; the replay sink
has one runtime source, a finalized MCAP (`reference replay`), so a raw
dataset is converted to a recording first and never replayed directly. The
replay sink publishes each event's payload as raw CDR bytes at the pace the
source timeline sets (`--rate` multiplies it; `0` is unpaced), after every
topic has a matched subscriber, with reliable keep-all delivery,
`/tf_static` latched, and a final wait for every sample to be acknowledged.
It rewrites no timestamp: source observation times stay inside the payload,
so replay pacing and transport latency cannot enter them. The bridge and
replay containers meet only over DDS on the compose network.

The MCAP source derives channels, schemas, payload bytes and source time
from the recording itself; the source time of a locked batch MCAP is its
MCAP `log_time`, and messages with equal `log_time` keep their file order.
`reference replay` resolves the recording through the reference corpus lock
and verifies it before the first message is published (see
`docs/development/reference-corpus.md`). It reads no source dataset: the
`dataset-replay` service mounts only the corpus and the reference cache, and the
streaming acceptance probes that no raw-dataset path exists in the container.

## 11. Channel registry and source-timestamp contract

Which topics the bridge subscribes to, which type each carries, and where
its source timestamp lives come from one declarative registry,
`sceneops_core.streaming.channels` (`ChannelSpec`, `ChannelRegistry`). The
bridge and capture load the same registry, so they cannot disagree. It
holds transport facts only: no Scene, Episode, modality, sensor or dataset
format. There is no dynamic topic discovery.

Built-in default channels:

| Topic | ROS2 type | Source timestamp rule |
| --- | --- | --- |
| `/vehicle/odom` | `nav_msgs/msg/Odometry` | `header` |
| `/vehicle/imu` | `sensor_msgs/msg/Imu` | `header` |
| `/vehicle/status` | `sensor_msgs/msg/BatteryState` | `header` |
| `/vehicle/control` | `std_msgs/msg/String` (JSON) | `json_field` |
| `/mission/status` | `std_msgs/msg/String` (JSON) | `json_field` |
| `/tf` | `tf2_msgs/msg/TFMessage` | `transform_header` |
| `/tf_static` | `tf2_msgs/msg/TFMessage` (latched) | `transform_header` |

Sensor channels (cameras, `CameraInfo`, lidar, ...) are deployment
configuration: a channel-set JSON file given to both the bridge and capture
with `--channels-file` (repeatable) adds them to the defaults. The shipped
`ros2/channels/surround-camera-lidar.json` declares six `CompressedImage` +
`CameraInfo` pairs and a `PointCloud2` lidar. A channel entry is
`{topic, message_type, timestamp, latched?, queue_depth?}`; a topic defined
twice with different settings, a relative topic name, the reserved control
channel and a non-`<pkg>/msg/<Name>` type are rejected.

**Timestamp rules.** `header` reads `msg.header.stamp`; `transform_header`
reads the first transform's header stamp (`TFMessage`); `json_field` reads
the integer `source_timestamp_ns` of a JSON `std_msgs/String`. The rule only
*locates* a timestamp the message already carries, verbatim as
`stamp.sec * 1e9 + stamp.nanosec`. No rule substitutes bridge receive time,
replay time or any wall clock, and a message whose timestamp is missing,
or malformed fails loudly: it is counted and logged, the bridge exits
non-zero, and the sequence gap it leaves fails the capture.

**Zero stamps.** A message may carry a zero timestamp (an unstamped
`Header`): static data such as `/tf_static` has no observation time, and many
publishers leave it 0. The envelope accepts `source_timestamp_ns = 0` as the
source's own value, and a channel opts in with `allow_zero_stamp` (default
false; true for the built-in `/tf_static`). On such a channel the zero stays
0 in the payload and in the envelope and is never replaced by the bridge's
ingest time or any other clock. On every other channel a zero stamp is a
missing observation time and fails loudly (counted, logged, sequence gap).
A static transform's stamp is not read by canonicalization: a recording with
an unstamped `/tf_static` builds the same calibrations as a stamped one.

**Events on the source timeline.** `/mission/status` events carry
`source_timestamp_ns` values on the source's own timeline, set by whoever
publishes them (the replay sink replays the acquisition tool's events at the
unit's first and last source times). A synthetic event never carries replay
wall-clock or replay-pacing time.

`/vehicle/status` and `/vehicle/control` come from the same source record
when a source has one; `/vehicle/control` remains observed feedback and is
never renamed or reinterpreted by the bridge.

## 11.1 What the recording preserves per channel

| Fact | Where it lives in the L1 recording |
| --- | --- |
| Source observation time | inside the payload, unrewritten |
| Transport time | MCAP `publish_time` = the envelope's `ingest_timestamp_ns`: when the bridge accepted the message. Not an observation time and not a robot-side publication time (§20) |
| Receive time | MCAP `log_time` = capture's wall clock when it took the record from Kafka (§20) |
| Transport sequence | MCAP `sequence` = envelope `sequence_number` + 1 (§20) |

## 12. Bridge responsibility and boundaries

`ros2/nodes/streaming_bridge_node.py`'s `StreamingBridgeNode` has exactly
one responsibility: `ROS2 message -> TelemetryEnvelope -> TelemetryProducer`.
Verified by construction -- the file imports only `rclpy`/ROS2 message
packages, `sceneops_core.streaming`, and `sceneops_streaming`. It never
imports `sceneops-db`, `sceneops-storage`, Celery, Airflow, or anything
API/worker-side.

**Process boundary:** the bridge is a standalone ROS2 node, run inside the
`ros2` Docker image/service (`compose/ros2.yaml`, profile `ros2`) -- not a
separate image. This avoids
duplicating the full ROS2 environment: the `ros2/` image already has
`rclpy`, the message packages, and the MCAP plugin; it additionally
pip-installs `packages/sceneops-core`/`packages/sceneops-streaming`
(`ros2/Dockerfile`) so the bridge node can import them. It is not inside
`apps/api` or `apps/worker`.

## 13. Topic subscriptions, CDR payloads, sequence numbers, ingest timestamp

**Topic subscriptions** are derived from the channel registry (§11):
every `create_subscription` call the node makes comes from one
`ChannelSpec`; nothing is scattered across ad hoc callbacks. QoS is
reliable, volatile -- transient-local for a latched channel, so static data
published before the bridge started is still received. History is
**keep-all** unless a channel sets `queue_depth`: a source can emit bursts
(a sensor frame's messages share one instant, and nuScenes' `/tf`, IMU and
odometry arrive in bursts of hundreds) far larger than a fixed depth, and
DDS drops the oldest sample of a full keep-last history without any
signal. A depth is therefore an explicit, silent-loss memory bound, never a
default.

**CDR payloads.** Subscriptions are *raw*: the node receives serialized CDR
and forwards those bytes. `encoding = EnvelopeEncoding.ROS2_CDR`
("ros2-cdr") is set uniformly; `message_type` is the canonical ROS2
interface type string and `channel` the literal topic name including its
leading slash -- never a SceneOps alias or a remapped name. The bytes are
deserialized only to read the source timestamp.

**DDS alignment padding.** DDS pads a small serialized sample to a 4-byte
multiple, so a raw take can end in 1-3 zero bytes the publisher never
wrote (observed: 119 B published, 120 B received; 73 B -> 76 B). Large,
fragmented samples are not padded. Forwarding the padding would make
identical messages acquired by batch and by stream differ in bytes, so the
bridge trims it with proof (`exact_cdr`): the received bytes may exceed the
length of the decoded message's canonical re-serialization by 1-3 bytes, and
only if those are all zero are they dropped. Only the re-serialization's
*length* is used, never its bytes (alignment padding inside a CDR message is
uninitialized memory in `serialize_message` output), and the forwarded bytes
are always the received ones. Anything else is forwarded unchanged. The
recording's payload bytes are therefore exactly the publisher's.

**Sequence numbers.** One `int` counter (`StreamingBridgeNode._sequence`)
per `(robot_id, robot_run_id)` bridge process instance, incremented once
per message across all channels, not per-topic -- it represents
bridge-observed arrival order, independent of any per-topic identity.
Source duplicates are never deduplicated: two identical messages are two
occurrences with two sequence numbers. No lock is needed: the node runs its
default single-threaded executor (`rclpy.spin_once` in a loop), so
subscription callbacks never execute concurrently. The sequence is never
sorted by `source_timestamp_ns`, and cross-channel arrival order is
acquisition evidence only, never canonical temporal identity. Capture
carries the sequence into the recording (§20). A source's own per-topic
counters cannot cross a ROS2 topic; only this transport sequence exists on
the streaming path.

**Ingest timestamp.** Left entirely to `TelemetryEnvelope`'s own
`default_factory=time.time_ns` (§4's ownership rule) -- the bridge does not
capture or override it. It becomes the recording's `publish_time` (§20).

**Robot/RobotRun identity.** `--robot-id`/`--robot-run-id` are required
CLI arguments -- the bridge never derives or defaults them. They are
transport metadata only. The streaming bootstrap passes the Reference Contract's
RobotRun identity (`run-stream-ref-<corpus>-<fixture>`).

## 14. Backpressure, shutdown, and failure semantics

**Backpressure boundary:** `_AsyncProducerBridge` runs one real
`KafkaTelemetryProducer` on a dedicated background asyncio event loop for
the node's whole lifetime. A ROS2 callback's `publish()` call blocks the
calling (ROS2 callback) thread for at most `--publish-timeout-seconds`
(default 5.0s) waiting for the coroutine to complete on that loop. There is
no queue of the bridge's own besides the DDS subscription history (keep-all,
§13) and librdkafka's internal client queue, bounded by its own default
configuration. A stalled/unreachable broker surfaces as a timeout or
exception in the callback (caught, logged, counted -- never silently
dropped); a sustained input rate above what the bridge can forward shows up
as growing DDS-side memory, not as silent loss.

*Measured (scope: one nuScenes v1.0-mini scene, scene-0061, 8,897 messages,
about 356 MB, 20 channels incl. six cameras and a 20 Hz lidar; Apple-silicon
Docker Desktop, replay, bridge, capture and Kafka on one host, stock
broker and client settings):* replayed at 1x, 2x, 8x and unpaced, every
message reached Kafka and the recording (replay = bridge = capture counts,
per channel); unpaced replay completed in about 5.6 s (roughly 1,600
messages/s). A bounded history of depth 100 lost messages on the bursty
channels at 1x and 2x (`/tf` 691 of 2,963 bridged), which is why keep-all
is the default. This is a measurement of this workload, not a supported
throughput limit.

**Kafka message size.** The producer and broker run with stock limits (no
override in compose or settings; librdkafka `message.max.bytes` ~1 MB).
Largest payloads in this stack's nuScenes data: lidar `PointCloud2`
696,320 B (3,935 sweeps+samples), camera JPEG 298,656 B (largest of
2,342 front frames); the largest message of the scene-0061 replay was
695,849 B. All fit, so no limit was changed.
`ros2/capture/tests/test_sensor_payload_kafka_integration.py` round-trips
camera- and lidar-sized payloads through a real broker and a capture, and
asserts that a 1.5 MB payload fails loudly at publish (`Message size too
large`) -- the bridge turns that into a counted, logged failure. A sensor
whose messages exceed ~1 MB needs the producer `message.max.bytes`, topic
`max.message.bytes` and broker `message.max.bytes` raised together; that
is not configured today.

**Graceful shutdown:** `main()` installs `SIGTERM`/`SIGINT` handlers that
set a `threading.Event`; the spin loop (`rclpy.spin_once(node,
timeout_sec=0.5)`) checks it every 0.5s. For a finite source,
`--exit-after-idle-seconds N` ends the loop once at least one message was
bridged and none arrived for `N` seconds. On shutdown: stop spinning (no
new callbacks) -> publish `RUN_END` (always; capture finalizes a run only on
its `RUN_END`) -> flush the producer, bounded
by a timeout -> close it -> print one `bridge_summary` JSON line (published
and failed counts, per channel) -> `node.destroy_node()` ->
`rclpy.shutdown()`. If any message failed during the run, the process exits
non-zero.

**Failure semantics:** `_handle_message` wraps decode/timestamp/publish in
one `try`/`except Exception`. On failure: increment the failed count, log a
structured error (topic, exception type and message, running
published/failed counts) via `self.get_logger().error(...)`, and continue
processing subsequent messages -- never crash the node, never silently
drop. Every received message takes its sequence number before anything can
fail, so a message the bridge drops leaves a sequence gap, which capture
rejects: a bridge failure makes the run's recording fail rather than silently
come out incomplete. "Unsupported
message type" cannot occur at runtime by construction: the node only ever
subscribes to registry topics. No DLQ, no persistent retry storage --
deferred to a reliability boundary, matching the transport's own
invalid-message-handling approach (§7).

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
scripts can run inside the container with real `rclpy`. No new service and
no new Compose profile exist for the bridge -- it runs as another
invocation of the existing `ros2` service/profile.

**`make local-up` and `make streaming-up` are unaffected** -- neither
starts ROS2 or Kafka implicitly.

**Configuration:** the bridge takes `--robot-id`/`--robot-run-id`/
`--channels-file`/`--publish-timeout-seconds`/
`--exit-after-idle-seconds` as CLI arguments and otherwise reuses the Kafka
transport's `SCENEOPS_STREAMING_KAFKA_*` settings verbatim via
`StreamingSettings()` -- no bridge-specific Kafka setting aliases exist.
No per-topic environment variable exists; the channel set is the registry
(§11). `ROS_DOMAIN_ID` is not set -- the publisher (a robot, or the replay
container) and the bridge discover each other over the default ROS2 DDS
domain on the shared `sceneops-network` as separate containers. The `ros2`
service also mounts `./ros2/channels` (channel-set files) and the
`acquisition-recordings` volume (capture output, read by the
recording-publisher container).

## 16. Make surface and verification

```text
make test-infrastructure SUITE=kafka   `make ros2-test` (bridge + capture unit and
                                  real-Kafka integration tests, inside the ros2 image),
                                  then `make smoke-streaming` (needs streaming-up)
make e2e-streaming-equivalence    transport-preservation equivalence of one fixture's
                                  two golden RobotRuns, read-only (SCENE overridable;
                                  default smoke-1); needs neither Kafka nor ROS 2
```

`ros2/nodes/tests/test_streaming_bridge_node.py` uses a `FakeProducerBridge`
in place of `_AsyncProducerBridge`, with every payload produced by real
`rclpy.serialization.serialize_message`, never JSON-mocked: field mapping per
timestamp rule (header, transform header, JSON field), raw-byte forwarding,
DDS-padding trimming and its non-matches, duplicates, sequence behavior,
failure counting, QoS, and lifecycle events.

`scripts/e2e/e2e_streaming_equivalence.sh` proves the transport preserved the
acquisition without running it. The Reference Contract holds the same locked MCAP
(`docs/development/reference-corpus.md`) twice, as two registered RobotRuns of one
fixture (`docs/development/reference-contract.md`), so equality of the results is
a statement about the transport, not about two conversions of a dataset:

```text
Recording Import       locked MCAP -> RobotRun -> Scene / Episode. The RobotRun pins
                       the lock's recording sha256.
Streaming Acquisition  locked MCAP -> `reference replay` (no raw dataset mounted)
                       -> ROS 2 -> bridge -> Kafka -> capture (until RUN_END; receipt)
                       -> publish-pending -> reconcile --apply (REGISTER_ROBOT_RUN)
                       -> RobotRun -> Scene / Episode, built with the baseline's
                       build configuration files (config/baselines/). Its RobotRun
                       pins the captured recording.
verify                 both recordings read from the ArtifactStore (the registered
                       recording of each RobotRun, checked against its registered
                       checksum and manifest) and compared: the locked message and
                       per-channel counts on both; acquisition equivalence (§29.12):
                       channels, message types and encodings, per-channel payload
                       sequences, every Header.stamp and the mission event times,
                       `/tf_static`; Scene and Episode semantic equivalence (I-35);
                       negative controls; every RobotRun, Dataset, Scene and
                       Episode record unchanged afterwards
```

The journey replays, captures, publishes, registers and builds nothing, uses no
Kafka, ROS 2, bridge, replay container, capture volume or reference cache, and
writes no durable state (`REFERENCE_READ_ONLY`, `docs/development/test-matrix.md`).
The host needs Docker Compose, curl, jq, python3 (standard library) and the API port.

Container bytes, capture `log_time`, schema-definition text and cross-channel
write order are not compared; Scene / Episode builds that read
`mcap_log_time` or `mcap_publish_time` would legitimately differ and are not part
of the comparison (the baseline configurations use source-semantic clocks only).

The negative controls are minimal, real perturbations of the loaded data, applied
to in-memory copies: a message dropped from a channel, one `Header.stamp` or one
Scene / Episode observation time shifted by 1 ns, one payload checksum changed. The
verifier must report each as a difference.

**Kafka offsets of a streamed run.** A run's records on its partition are
`RUN_START`, `message_count` telemetry records and `RUN_END`: the bridge publishes
the two lifecycle control envelopes (channel `/session/control`) with the run's own
key, before the first and after the last telemetry record. Capture validates them
in their own sequence space and never writes them to the MCAP, but they are consumed
records, so the capture receipt's `kafka.first_offset .. kafka.last_offset` spans
`message_count + 2` offsets when the run owns that offset range (other runs hashed
to the same partition interleave their offsets without changing the run's own
records). `ros2/capture/tests/test_lifecycle_integration.py` proves this on a real
broker: the real bridge node publishes through its real producer, the topic holds
exactly one `RUN_START`, the run's telemetry records and one `RUN_END` in that order,
and capture finalizes on the explicit `RUN_END` with a receipt whose offset range
runs from `RUN_START` to `RUN_END`.

`make streaming-bootstrap` applies the same streaming path (the shared
`scripts/streaming/streaming_lib.sh`) to every fixture of a corpus scope and keeps the
results as the persistent streaming baseline (`docs/development/canonical-baseline.md`).
It checks per fixture that replay, bridge, capture and the receipt carry exactly the
locked recording's messages per channel; payload-level equivalence stays the
equivalence journey's.

`make smoke-streaming` (the Kafka transport's own smoke test) passes
independently -- it proves the Kafka transport itself and is never replaced
by the ROS2-specific verification.

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
(Part 2) published back out to one validated L1 raw recording (ADR-007
§29.5): an MCAP in the ROS 2 profile (`cdr` messages, `ros2msg` schemas),
one channel per topic, payloads exactly as published:

```text
real Kafka (Part 1) -> ros2/capture (run-scoped consumer)
  -> validated, finalized local MCAP file
  -> Recording Publisher -> REGISTER_ROBOT_RUN (explicit steps)
```

One invocation captures exactly one `(robot_id, robot_run_id)`. It
creates no canonical `RobotRun`, `Scene`, `Episode`, or `ArtifactRecord`,
and writes no Postgres/MinIO state -- verified by construction
(`ros2/capture/` imports neither `sceneops-db` nor `ArtifactStore`).
Registering a captured file as a canonical `RobotRun` is the next, separate
boundary (§27, §30).

## 19. Package layout and placement

`ros2/capture/` -- flat scripts (no `__init__.py`), matching `ros2/nodes/`'s
own convention (absolute imports, loaded via `sys.path.insert`, not as
installed packages). Runs inside the existing `ros2` Docker image/profile
(`compose/ros2.yaml` mounts `./ros2/capture:/workspace/capture:ro`) -- not a
new service or profile, and not inside `streaming_bridge_node.py` or
`apps/worker`. It needs the ROS 2 interface definitions installed in that
image (for schema text) and `mcap`/`mcap-ros2-support` (`ros2/Dockerfile`)
for the writer, the mandatory pre-finalize read-back validation (§25) and
the tests -- none of this leaks into `sceneops-core`, `apps/api`, or any
general domain package. The channel registry it validates against lives in
`sceneops-core` (§11).

```text
ros2/capture/
  message_definition.py  ros2msg schema text from the installed ROS 2 interfaces
  mcap_writer.py         McapCaptureWriter (official mcap writer, ROS 2 profile)
  validation.py          pre-finalize MCAP read-back validation; retry-convergence comparison
  finalize.py            temp/final bag directory lifecycle
  capture_consumer.py    RunFilter, SequenceTracker, CaptureResult, run_capture()
  cli.py                 CLI entry point (run_capture, finalizes on RUN_END)
  tests/                 pytest, runs only inside the ros2 container (make ros2-test)
```

## 20. Time and ordering in the recording

Every timing fact keeps its own meaning (ADR-007 §29.5 R4, R6); no field
stands in for another:

```text
MCAP log_time      capture RECEIVE time: the wall-clock instant capture took the
                   record from Kafka (Unix-epoch ns). Never a source timestamp.
                   Clamped so it never decreases in write order.
MCAP publish_time  TelemetryEnvelope.ingest_timestamp_ns: the TRANSPORT ingest
                   time, when the bridge accepted the message. Neither an
                   observation time nor a robot-side publication time.
source time        inside the payload (Header.stamp / transform stamp / JSON
                   field), untouched -- a zero stamp stays zero.
                   TelemetryEnvelope.source_timestamp_ns is the bridge's
                   verbatim copy of it and is not written separately.
MCAP sequence      TelemetryEnvelope.sequence_number + 1
```

**Why `publish_time` is the transport ingest time.** A raw ROS 2
subscription exposes no publisher timestamp, and a DDS source timestamp, where
one exists, is the publisher's own wall clock (for a replay, replay wall-clock)
and is not carried by the envelope. The only upstream-of-capture time SceneOps's
transport observes is the bridge's acceptance time, and ADR-007 §29.5 R4 requires
a transport-level timing fact to survive into the recording. `publish_time` is
its only per-message slot, so it carries that time under its true name. The three
facts stay distinct: source observation time (payload), transport ingest time
(`publish_time`), receive time (`log_time`). A build configured from
`mcap_publish_time` depends on the acquisition, like one from `mcap_log_time`,
and is outside the source-time equivalence guarantee.

`log_time` is the recorder's clock, so a recording's `started_at` /
`ended_at` (derived from it) are wall-clock receive times, and the
recording's `capture.source_clock` stays `mcap_log_time`. A downstream
build configured from source timestamps (header stamps, payload fields)
never reads `log_time`; one configured from `mcap_log_time` depends on
the acquisition and is outside the source-time equivalence guarantee (§29.12).

`sequence`: MCAP reserves 0 for "no sequence" and the bridge's counter
starts at 0, so the stored value is one higher. It is increasing within
every channel (with gaps where other channels' messages sit between).
Messages are written in the order consumed from the run's Kafka partition;
that cross-channel arrival order is acquisition evidence, never canonical
temporal identity. Transport redelivery (same sequence, same payload) is
dropped; source-level duplicates are separate occurrences and are kept.

**Why the official `mcap` writer, not `rosbag2_py`.** rosbag2's MCAP
storage plugin always writes `sequence = 0` and its Python writer takes only
a receive and a send timestamp. Preserving the sequence required writing the
MCAP directly. Schema text comes from the `.msg` files of the installed ROS 2
distribution (`message_definition.py`): the type's text followed by every
dependency, each introduced by a separator line and `MSG: <package>/<Name>`
-- the same format rosbag2 writes. Tests decode payloads serialized by
`rclpy` with those schemas through an independent MCAP ROS 2 decoder.

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

## 22. Supported channels

The channel registry (§11) is the only source of channel/type validation
-- no dynamic ROS2 topic/type discovery. Capture must be given the same
`--channels-file`s as the bridge. An envelope naming a channel or
`message_type` outside the registry raises `UnsupportedChannelError` and
aborts the capture attempt (§21's ordering means nothing gets finalized or
committed). The writer needs no imported ROS2 message class: the payload is
opaque CDR bytes and the schema text is read from the interface
definitions by type name (§20).

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

The capture writer writes into a *directory* holding one `.mcap` file --
the unit that moves atomically from "being written" to "durably captured"
is that whole directory. Layout, per `robot_run_id`, under one capture `output_root`:

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
happen to already be on disk. The writer refuses to open into a directory
that already exists, so `prepare_partial_bag_dir()` guarantees the path
does *not* exist and its parent does, rather than creating it itself.

`finalize_bag()` performs the atomic transition: `os.replace()` (atomic
within one filesystem, guaranteed here since both paths share
`output_root`) followed by an `fsync` of `output_root`'s directory entry.
It never overwrites an existing final bag -- a second finalize attempt
for the same `robot_run_id` raises `FinalBagExistsError`, leaving both
the original final bag and the new attempt's `.partial` directory
untouched, rather than silently discarding either.

Before finalizing, `validate_mcap_file()` (`validation.py`) reads the
just-closed MCAP back with the `mcap` reader package (never trusts the
writer's own in-memory counters),
and raises `McapValidationError` -- refusing to finalize -- on a
corrupt/unreadable file, a written-vs-read-back message count mismatch,
or zero messages.

## 26. Capture lifecycle, configuration, and failure behavior

**Lifecycle.** `run_capture()` never inspects payload content to decide
when to stop. A run is finalized only by its explicit `RUN_END` control
event (`stop_on_run_end`, always set by `cli.py`), consumed after it passed
its own sequence validation. The bridge publishes `RUN_END` after its last
telemetry record on the same partition, so everything it forwarded precedes
it. The caller's `stop_condition(message_count)`, polled before every Kafka
poll, is then only an abort guard: `cli.py --idle-timeout-seconds S` aborts
when nothing arrives for `S` seconds before `RUN_END`. An aborted capture
raises `RunEndNotObservedError`, exits non-zero and finalizes and commits
nothing, because a recording that merely stopped arriving (a bridge killed
without `RUN_END`) cannot be told from a complete one once it is a registered
RobotRun. Without `stop_on_run_end` (tests and benchmarks of count-bounded
synthetic runs) the `stop_condition` alone ends and finalizes the capture; the
receipt then records its reason (`max_messages`, `idle_timeout`, `manual`),
and every capture finalized through `cli.py` records `explicit_run_end`.

Channels arrive asynchronously: a channel is registered in the MCAP the
first time a message for it is written, whenever that is, and a run is not
required to deliver every registry channel. Control events (RUN_START /
RUN_END) never enter the recording (§29).

**Restart and failure behavior (current, explicit).**

```text
Capture process dies before finalize   the .partial directory is discarded and the capture
                                       rebuilds from Kafka (offsets were never committed).
Capture dies after finalize, before    a re-run consumes the same records, writes a new
commit                                 partial and finds the final file. It converges only
                                       if the recorded messages match (topic, schema, publish
                                       time, sequence, payload; log_time excluded, since each
                                       attempt stamps its own receive time): the existing file
                                       stays the recording of record, the offsets are
                                       committed. Different content under the same run id
                                       fails with FinalBagExistsError and commits nothing.
Bridge killed without RUN_END          no RUN_END arrives; the idle-timeout guard aborts the
                                       capture (RunEndNotObservedError): nothing is finalized
                                       or committed, the .partial directory stays, and a re-run
                                       discards it and rebuilds from Kafka.
Bridge message failure                 counted and logged; the sequence gap fails the capture.
Sequence gap, conflicting duplicate,   the capture fails; nothing is finalized or committed.
partition spread, unsupported channel
Kafka retention                        capture can only rebuild what the topic still holds.
In-flight state across restart         capture holds no state beyond Kafka and the .partial
                                       directory.
```

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
interleaved on the same partition. The same `robot_run_id` always derives
the same group (a pure function of its inputs), so retries land on the same
group and offset lifecycle every time; different `robot_run_id`s derive
different groups (a SHA-256 digest suffix, not the human-readable slug
prefix alone, which is cosmetic only).

**Tradeoff, not fully solved:** this provides correct, isolated replay per
RobotRun -- it does not provide efficient large-scale multi-run capture.
Each run-scoped group is, the first time it's used, a brand new Kafka
consumer group with no committed offset, so `auto.offset.reset = earliest`
means it may scan the topic's entire historical record before reaching its
own messages (measured: ~3.4s against a lightly-used topic vs ~44s once it
held ~150,000 prior messages; see
[Streaming reliability & scale baseline](./streaming-reliability-scale-baseline.md)).
Redesigning topic layout to bound this cost is out of scope here.

None of these are `SCENEOPS_STREAMING_KAFKA_*` settings and none are
configurable via environment variable -- deliberately, matching Part 1's
own "explicit code-level default until a demonstrated override need
exists" policy (§9.1).

**`CaptureResult`** (`capture_consumer.py`), returned once Kafka offsets
are committed:

```text
robot_id, robot_run_id, path, message_count, partition,
first_offset, last_offset, first_sequence, last_sequence, sha256,
per_channel_counts
```

Not a `RobotRun` -- it describes a local file and its Kafka provenance
only; nothing here is a canonical record (§18). The CLI prints it and one
`capture_summary` JSON line for orchestration.

**Out of scope:** multi-instance coordination, durable session recovery and
exactly-once capture beyond one invocation's commit-after-finalize ordering
-- a reliability boundary for later work, matching Part 1's own DLQ/retry
deferral (§7).

## 27. Make surface and verification

`make ros2-test` runs `ros2/capture/tests/` and `ros2/nodes/tests/` in the
ros2 image: writer (receive time, publish time, sequence, monotonic clamp,
payload bytes, duplicates, late channel arrival, unsupported channels),
message definitions decoded by an independent MCAP ROS 2 decoder,
finalize/validation, `RunFilter`/`SequenceTracker`/duplicate policy,
`run_capture` (RUN_END finalization and refusal to finalize without it, sensor
channels from a channel file, durability ordering, crash boundaries C and D
including conflicting retry), and real-Kafka integration (multi-run isolation, lifecycle,
camera- and lidar-sized payloads, oversize failure).

`make streaming-bootstrap` is the real vertical: the captured recording is
checked with the L1 conformance suite
(`python -m sceneops_integrations.recording check`), published from its capture
receipt, registered and built into Scenes and Episodes; `make
e2e-streaming-equivalence` (§16) compares the registered result with the locked
recording it was replayed from. Capture's output is checked by the same conformance suite as
the batch tool's.

# Part 4: Run lifecycle

## 28. Lifecycle control envelopes

`RUN_START`/`RUN_END` (`sceneops_core.streaming.control`) are an
additive signal layered onto the existing `TelemetryEnvelope`/topic/
wire contract -- not a new schema, not a new topic. A control event IS
a `TelemetryEnvelope`: same required fields, same
`robot_id`/`robot_run_id`, same Kafka key (`robot_run_id`, via
`wire.partition_key`, entirely unchanged), same topic. What makes it a
control event rather than telemetry is purely its `channel`/
`message_type` (`SESSION_CONTROL_CHANNEL = "/session/control"`,
reserved, never a real ROS2 topic/interface, and rejected by
`ChannelSpec`, so it can never enter the channel registry).

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
message also needs in its own space (§29 covers why this is safe).

Capture finalizes a run only on its `RUN_END` (§26), so a bridge always
publishes both events.

## 29. Control envelopes in capture

A control envelope shares its run's `robot_run_id`, so it passes the run
filter like telemetry. `run_capture()` recognizes it with
`is_control_envelope()` and routes it to its own, independent
`_SequenceTracker` ("control tracker"): validated for gap/duplicate/conflict
exactly as strictly as telemetry, in a sequence space that never collides
with telemetry's own (both legitimately start at `0`, §28), but never passed
to the MCAP writer. A control envelope therefore never appears in a finalized
MCAP and never raises `UnsupportedChannelError` (reserved for a genuinely
unsupported channel). Its offset still belongs to the receipt's Kafka offset
range.

## 30. What comes next

The full chain from live telemetry through to a readable learning
dataset is built: durable capture (Part 3) closes `Kafka -> MCAP`; the database-free Recording
Publisher plus `REGISTER_ROBOT_RUN` (`docs/workflows/robot-run-and-mcap.md`
§3.2) close `MCAP -> MCAP + RobotRunManifest -> ArtifactRecords + RobotRun`;
and the verified
recording resolver (`sceneops_worker.robots.resolver`,
`docs/workflows/robot-run-and-mcap.md` §3.1) closes
`RobotRun -> recording Scene / Episode building / robot-state ingestion`.

```text
ROS2 / live robot -> stream envelope -> Kafka -> durable capture
  (Part 3, one run) -> validated local MCAP -> Recording Publisher
  -> REGISTER_ROBOT_RUN
  -> resolve_recording(robot_run_id) -> existing Episode pipeline
  -> existing learning-data pipeline
```

Publication and registration are recoverable, not triggered by capture:
Capture writes a `capture_receipt.json` into the bag it finalizes, `python -m
sceneops_integrations.recording publish-pending` publishes every finalized
capture that has a receipt, and `reconcile --once --apply` registers every
published manifest that has no RobotRun (retrying and replacing a stalled
registration within a budget). Each is a stateless one-shot command that the
operator, a polling loop (`make recovery-up`) or a CronJob invokes; nothing in
the capture path calls them on finalize. Capture itself stays DB-free
and never writes RobotRun state. See
[Robot data ingestion](../workflows/robot-run-and-mcap.md) §3.2 and
[ADR-008](../adr/008-acquisition-lifecycle-reliability.md).

Canonical Scenes and Episodes are built from a registered RobotRun's
recording by `RECORDING_SCENE_BUILDING` / `RECORDING_EPISODE_BUILDING`
([Scene domain](./scene-domain.md) §6). Capture records camera, lidar,
`CameraInfo`, `/tf`, `/tf_static` and telemetry channels (§11), so a
streamed recording is as buildable as a batch one; `make
e2e-streaming-equivalence` shows a locked recording replayed through the
transport yields Scenes and Episodes equivalent to the Recording Import RobotRun's.

Reliability and scale characteristics of everything above -- crash
boundaries, duplicate/gap/out-of-order handling, multi-RobotRun
isolation, Kafka-outage behavior, backpressure, throughput/memory at
scale, practical payload limits -- are measured and frozen in
[Streaming reliability & scale baseline](./streaming-reliability-scale-baseline.md)
and [Multi-run streaming architecture study](./streaming-multirun-phase7-study.md),
point-in-time records, not living contracts.

## 31. Non-goals

Not built, not started, not partially wired -- listed so a future pass
doesn't mistake absence for a bug:

```text
Capture-triggered publication/registration of a finalized capture
  (publish-pending and reconcile --apply recover it when invoked; capture
  itself calls neither, §30)
Episode generation from streamed data
Any Postgres/ArtifactStore write from the streaming or capture path
Kafka Connect, Schema Registry, Avro
Spark/Flink stream processing
DLQ / automatic retry policy
Consumer lag metrics platform (Prometheus/Grafana)
Streaming UI
Mutation of canonical baseline sceneops-canonical/v0.0
Dynamic ROS2 topic discovery (the channel registry is static, §11)
Timestamp correction beyond what the bridge already reads from source data
Interpretation of /mission/status boundaries beyond proving transport
  preservation (Episode-building's use of mission boundaries is a
  downstream consumer's concern, not this transport's)
Capture crash/restart reliability beyond the single-invocation,
  single-partition guarantees Part 3 describes (§26) -- process
  supervision, multi-instance coordination, and exactly-once capture
  across restarts are a separate reliability boundary, deferred
Multi-partition-per-robot_run_id support (Part 3 fails loudly instead,
  §23) or dynamic capture topic/channel configuration (the channel set is a
  static registry, §11, §22)
Multi-run capture from one shared consumer (capture is run-scoped, §26)
Capture-triggered publish hand-off (a finalized capture is published by
  publish-pending, not by Capture; Capture imports no ArtifactStore code)
Kafka message-size configuration beyond the stock ~1 MB limit (§14)
```

## 32. Source-of-truth map

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

- Channel registry (`ChannelSpec`, `ChannelRegistry`, timestamp rules, channel-set files) + tests: `packages/sceneops-core/sceneops_core/streaming/channels.py`, `packages/sceneops-core/tests/test_streaming_channels.py`; shipped channel set: `ros2/channels/surround-camera-lidar.json`
- Bridge node: `ros2/nodes/streaming_bridge_node.py`
- Bridge unit tests (real rclpy, no Kafka, ros2 container only): `ros2/nodes/tests/test_streaming_bridge_node.py`
- Replay sink (external-tool boundary; no SceneOps dependency): `tools/dataset-acquisition/src/dataset_acquisition/ros2_replay.py`, `tools/dataset-acquisition/tests/test_ros2_replay.py`, image target `replay` in `tools/dataset-acquisition/Dockerfile`, service `dataset-replay` in `compose/acquisition.yaml`
- Replay source (a locked MCAP): `tools/dataset-acquisition/src/dataset_acquisition/mcap_source.py` (`reference replay` in `cli.py`), `tools/dataset-acquisition/tests/test_mcap_source.py`
- Container/runtime: `ros2/Dockerfile`, `compose/ros2.yaml`
- Make: `make ros2-test`, `make e2e-streaming-equivalence` (`makefiles/streaming.mk`)

**Durable MCAP capture:**

- Schema text from installed interfaces: `ros2/capture/message_definition.py`, `ros2/capture/tests/test_message_definition.py`
- MCAP writer: `ros2/capture/mcap_writer.py`, `ros2/capture/tests/test_mcap_writer.py`
- Pre-finalize validation: `ros2/capture/validation.py`, `ros2/capture/tests/test_validation.py`
- Temp/final lifecycle: `ros2/capture/finalize.py`, `ros2/capture/tests/test_finalize.py`
- Consumer orchestration (`RunFilter`/`SequenceTracker`/`CaptureResult`/`run_capture`, durability-ordering test): `ros2/capture/capture_consumer.py`, `ros2/capture/tests/test_capture_consumer.py`
- Run-scoped consumer-group derivation: `ros2/capture/group_id.py`, `ros2/capture/tests/test_group_id.py`
- Multi-RobotRun isolation (real Kafka): `ros2/capture/tests/test_multi_robot_run_integration.py`
- CLI entry point: `ros2/capture/cli.py`
- Container/runtime deps (`mcap`/`mcap-ros2-support`): `ros2/Dockerfile`, capture source mount: `compose/ros2.yaml`
- Realistic sensor payloads through real Kafka: `ros2/capture/tests/test_sensor_payload_kafka_integration.py`
- Lifecycle envelope of a run (real bridge, real Kafka, capture receipt offsets): `ros2/capture/tests/test_lifecycle_integration.py`
- E2E (read-only over the Reference Contract): `scripts/e2e/e2e_streaming_equivalence.sh`, `scripts/e2e/streaming_equivalence_verify.py` (decisions unit-tested in `scripts/e2e/tests`), `make e2e-streaming-equivalence`
- Recording equivalence and conformance: `packages/sceneops-integrations/sceneops_integrations/recording/{equivalence,conformance}.py`

**Run lifecycle:**

- Lifecycle control envelopes (`RunEventType`, `build_control_envelope`/`is_control_envelope`/`parse_run_event`): `packages/sceneops-core/sceneops_core/streaming/control.py`, `packages/sceneops-core/tests/test_streaming_control.py`
- `SESSION_CONTROL_CHANNEL` constant: `packages/sceneops-core/sceneops_core/constants/streaming.py`
- Bridge lifecycle-event publishing: `ros2/nodes/streaming_bridge_node.py`, `ros2/nodes/tests/test_streaming_bridge_node.py`
- Control-envelope handling and RUN_END finalization in capture (`control_tracker`, `RunEndNotObservedError`): `ros2/capture/capture_consumer.py`, `ros2/capture/tests/test_capture_consumer.py`
- Real-Kafka lifecycle integration (solo and interleaved runs): `ros2/capture/tests/test_lifecycle_integration.py`
- Point-in-time design/benchmark record: [Multi-run streaming architecture study](./streaming-multirun-phase7-study.md)

**Related ADRs:** [ADR-005](../adr/005-ros2-vs-kafka-boundary.md) (ROS2 vs. Kafka boundary), [ADR-003](../adr/003-batch-first-architecture.md) (why streaming waited until now)
