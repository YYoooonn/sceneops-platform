# Streaming Transport (Phase 6.1)

> Describes Phase 6.1 as it exists today: the streaming *transport
> foundation* only. Like [external-integration-runtime.md](./external-integration-runtime.md)
> (Phase 4), this is a "what's actually built" document, not aspirational
> -- every claim below was checked against the code and against a real,
> live run (`make streaming-up && make smoke-streaming`), not against
> planning documents.

## 1. Goal and scope

Phase 6.1 proves exactly one thing: binary robot telemetry can move
through Kafka as a typed envelope and come back out on the other side
byte-for-byte identical, with its metadata intact. Nothing else.

```text
binary robot telemetry
  -> typed streaming envelope (TelemetryEnvelope)
  -> Kafka producer
  -> Kafka broker
  -> Kafka consumer
  -> exact envelope/payload recovery
```

**Not built yet** (explicitly out of scope for this request -- see §9):
a ROS2 subscriber bridge, an MCAP writer, `RobotRun`/`Episode` lifecycle
integration, any Postgres/ArtifactStore write, a DLQ/retry policy, Schema
Registry/Avro, and any UI. `make smoke-streaming` leaves zero canonical
(Postgres/MinIO) state -- verified by construction: nothing in
`sceneops-streaming` imports `sceneops-db` or an `ArtifactStore`.

## 2. Existing streaming-related code (audit, before this request)

Before Phase 6.1, the only streaming-shaped things in the repository were
documentation of an *intended future boundary*, not working code:

- [ADR-005](../adr/005-ros2-vs-kafka-boundary.md) -- decided the
  robot-runtime-communication (ROS2) vs. data-platform-event-stream
  (Kafka) split, named example topics (`robot.telemetry.v1`), but
  Kafka's side was explicitly "미착수" (not started).
- [ADR-003](../adr/003-batch-first-architecture.md) -- decided Kafka is
  deferred until "실제 robot이 실시간으로 telemetry를 쏘기 시작하는 시점"
  (Phase 7 in that ADR's numbering -- this request's Phase 6).
- [reserved-and-limitations.md](./reserved-and-limitations.md) §7 listed
  "live robot control or real-time telemetry streaming" as a non-goal,
  pointing at ADR-005 for the intended future boundary.
- The real, working robot-data path (`ros2/`,
  `apps/worker/sceneops_worker/datasets/ingestion/rosbag_raw_log.py`,
  `RosbagAdapter`) is strictly **batch**: replay -> `ros2 bag record` ->
  MCAP -> decode -> ingest. See
  [robot-run-and-mcap.md](../workflows/robot-run-and-mcap.md). No
  ROS2 topic is ever published live to anything outside the ROS2 sandbox
  container.

No Kafka client, message envelope, producer, consumer, or topic existed
anywhere in the codebase. Phase 6.1 does not replace or refactor any of
the above -- it adds a new, independent transport primitive alongside it.
The batch MCAP path is untouched.

## 3. Package layout (frozen)

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

sceneops-streaming (packages/sceneops-streaming, NEW)
  The only package that imports confluent_kafka. StreamingSettings
  (bootstrap servers / topic / consumer group prefix / producer client
  id), wire.py (pure Kafka key/headers/value mapping -- no broker
  needed), KafkaTelemetryProducer, KafkaTelemetryConsumer,
  EnvelopeDecodeError. No dependents yet -- listed in root
  pyproject.toml's dev dependency-group only, so `make test` installs
  it without forcing the Kafka SDK onto apps/worker or apps/api's own
  dependency graph (request §15).
```

`apps/worker`, `apps/api`, and every domain package (`sceneops-analytics`,
Scene, Episode, learning-data) depend on neither package's Kafka-specific
half -- only a future ROS2 bridge (Phase 6.2+) would add
`sceneops-streaming` as a real dependency.

## 4. TelemetryEnvelope contract (frozen)

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
├── ingest_timestamp_ns     int > 0, defaults to construction time -- see §5
├── sequence_number         int >= 0, required -- diagnostic only, never identity
├── encoding                EnvelopeEncoding (ros2-cdr | json | raw), required
└── payload                 bytes -- binary-first, never base64
```

A transport primitive, not a universal SceneOps DataUnit and not a domain
record -- domain fields belong inside `payload`, decoded by something that
understands `encoding`/`message_type`. Every field is required with no
silent default except `version`; a malformed/incomplete record fails
Pydantic validation instead of being coerced (§8).

## 5. Time, ordering, delivery semantics (frozen)

**Time.** `source_timestamp_ns` (the observation) and `ingest_timestamp_ns`
(when SceneOps accepted the message at the transport boundary) are
**semantically distinct clocks, tracked independently** -- they are two
different concepts, not two values required to differ numerically.
Arrival time never *replaces* source time (a decoder must never conflate
the two), but a caller supplying the same instant for both is legal and
round-trips exactly; `TelemetryEnvelope`'s schema has no equality/
inequality constraint between them (Phase 6.1 follow-up request §1/§2 --
confirmed no such validation ever existed in the model itself; three test/
smoke assertions that incorrectly required `!=` were corrected in the same
request, see `packages/sceneops-core/tests/test_streaming_envelope.py`'s
`test_equal_source_and_ingest_timestamps_are_valid`,
`packages/sceneops-streaming/tests/test_wire.py`'s
`test_encode_decode_round_trip_preserves_equal_source_and_ingest_timestamps`,
and `scripts/e2e/smoke_streaming.py`'s run-B fixture).

**Ownership (frozen).** `source_timestamp_ns` is always supplied by the
source adapter/bridge (Phase 6.2+: the ROS2 bridge, reading the source
message's own stamp -- not implemented yet). `ingest_timestamp_ns` is
assigned at construction of the `TelemetryEnvelope` itself
(`default_factory=time.time_ns`) unless the caller explicitly supplies its
own value -- constructing the envelope IS "accepting the message at the
streaming transport boundary" in this architecture, so there is exactly
**one** default-assignment point, never a second, competing one inside the
producer/consumer/wire layers. A caller with a more precise
boundary-acceptance time (a lower transport adapter under an equivalent
contract) may always override the default explicitly.

**Ordering.** Arrival order (Kafka offset order within a partition) and
source timestamp are kept as separate concepts -- the transport layer
never sorts or reorders messages. For v1, Kafka partitioning by
`robot_run_id` gives deterministic per-RobotRun ordering (§7); no attempt
is made at ordering across RobotRuns or globally.

**Delivery.** At-least-once. `KafkaTelemetryProducer.publish` buffers a
record and returns once accepted into librdkafka's client-side queue;
`flush` blocks until the broker acknowledges every buffered record (or
raises on the first delivery failure). No exactly-once claim is made
end-to-end -- Kafka delivery and any future canonical-side idempotency
(e.g. a dedup key when this eventually feeds `RobotRun`/MCAP) are separate
concerns, deliberately not conflated here.

## 6. Kafka wire contract (frozen)

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

## 7. Topic and partitioning contract (frozen)

One topic, one configuration source:
`sceneops_core.constants.streaming.DEFAULT_TELEMETRY_TOPIC` =
`sceneops.robot.telemetry.v1`, overridable via
`StreamingSettings.telemetry_topic`
(`SCENEOPS_STREAMING_KAFKA_TELEMETRY_TOPIC`). No topic-per-robot or
topic-per-channel; nothing in the codebase creates a second telemetry
topic, and there is no separate "topic prefix" concept anywhere in the
implementation -- a prior draft of this document mentioned one, but it
was never built and has been removed from the docs to match the actual
code (Phase 6.1 follow-up request §7 audit: confirmed zero occurrences of
`topic_prefix`/`TOPIC_PREFIX` in the repository).

Partitioning: **Kafka key = `robot_run_id`** (`wire.partition_key`).
Every message for one RobotRun routes to the same partition
deterministically -- per-RobotRun ordering, by design, not global
ordering. Verified against the real broker by `make smoke-streaming`
(§10): a 3-message run-A sequence always lands on exactly one partition,
in publish order.

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

## 8. Invalid-message handling (frozen)

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
-- that is a reliability-phase concern, explicitly deferred (§9). The
caller (today: `make smoke-streaming`; later: a Kafka consumer inside a
future ROS2 bridge) decides whether to stop, skip, or surface the
failure.

## 9. Explicit non-goals (Phase 6.1)

Not built, not started, not partially wired -- listed so a future pass
doesn't mistake absence for a bug:

```text
ROS2 subscriber bridge (ROS2 topic -> TelemetryEnvelope -> Kafka)
MCAP writer / RobotRun streaming lifecycle
Episode generation from streamed data
Any Postgres/ArtifactStore write from the streaming path
Kafka Connect, Schema Registry, Avro
Spark/Flink stream processing
DLQ / automatic retry policy
Consumer lag metrics platform (Prometheus/Grafana)
Streaming UI
Mutation of canonical baseline sceneops-canonical/v0.0
```

The intended future flow, once these are built:

```text
ROS2 / live robot -> stream envelope -> Kafka -> durable capture -> MCAP
  -> RobotRun -> existing Episode pipeline -> existing Phase 5
  learning-data pipeline
```

## 10. Local development

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
`PLAINTEXT_HOST` for host-side tooling via `${KAFKA_PORT:-9092}`) --
matching how `make smoke-streaming` itself runs on the host (`uv run
python`, like every other `scripts/e2e/*` script) and needs a
localhost-reachable broker, same convention as
`makefiles/e2e.mk`'s `E2E_BOOTSTRAP_ENV` localhost overrides for
Postgres/MinIO.

**Topic creation.** `KAFKA_AUTO_CREATE_TOPICS_ENABLE=true` is set on the
broker for local-dev convenience -- the one telemetry topic is created
transparently on first publish. This is a deliberate, documented choice,
not an oversight: it keeps the public Make surface minimal (no dedicated
`streaming-topic-create` target, matching this request's
minimal-public-surface constraint) while staying fully deterministic for
the smoke contract -- the topic name is the one fixed, configured value
(`SCENEOPS_STREAMING_KAFKA_TELEMETRY_TOPIC`, §11), so auto-creation always
converges on the same single topic regardless of which process publishes
to it first. A production deployment would instead provision the topic
explicitly (infra-as-code), out of scope here.

## 11. Configuration (frozen)

Two distinct configuration domains -- deliberately never mixed into one
list of environment variables (Phase 6.1 follow-up request §6):

### 11.1 SceneOps streaming-client configuration

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
  No aliases (`KAFKA_BOOTSTRAP_SERVERS`, `KAFKA_BROKERS`,
  `STREAMING_BOOTSTRAP_SERVERS`) exist; one concept, one variable.
  Defaults to the in-network address (`kafka:9092`) -- never hardcodes
  `localhost` (§11.3).
- `telemetry_topic` -- the one telemetry topic name (§7). No
  `topic_prefix`/`TOPIC_PREFIX` concept exists (confirmed by repository
  audit); YAGNI applies until a second real topic is needed.
- `producer_client_id` -- passed straight through as `confluent_kafka`'s
  `client.id` (`producer.py`). Useful for broker-side logs/diagnostics/
  client metrics only -- **not canonical identity**, never used for
  partitioning, deduplication, or any decision logic.
- `consumer_group_id` -- the one application-level default consumer
  group. `KafkaTelemetryConsumer(settings=..., group_id=None)` (the
  default) uses this verbatim -- a real long-lived consumer (a future
  ROS2 bridge) needs no override. A caller that needs per-instance
  isolation derives its own group id FROM this configured base rather
  than inventing an unrelated literal -- e.g.
  `f"{settings.consumer_group_id}-smoke-<uuid>"`
  (`scripts/e2e/smoke_streaming.py`, §11.4). This is the single
  configuration ownership model: the base lives in settings, any
  caller-side suffix is always derived from it, never independent.

**Deliberately NOT exposed as environment variables** (request §12):
`acks`, `retries`, `linger.ms`, `batch.size`, compression,
`auto.offset.reset`, `enable.auto.commit`, consumer session timeouts.
These stay as explicit code-level defaults in `producer.py` (`acks=all`)
and `consumer.py` (`auto.offset.reset` is a constructor parameter, not an
env var; `enable.auto.commit=True` is hardcoded) until a demonstrated
override need exists -- not pre-designed now. A later phase may freeze
durability-specific consumer settings; Phase 6.1 does not.

**Precedence** (pydantic-settings' own built-in behavior, not a custom
mechanism): explicit constructor kwarg > environment variable >
`.env.local`/`.env` file > the field default shown above. See
`packages/sceneops-streaming/tests/test_config.py` for a test proving
this ordering, including that the env var rename is real (the old,
un-prefixed `SCENEOPS_STREAMING_BOOTSTRAP_SERVERS` name is now silently
ignored, not a live alias).

### 11.2 Kafka broker / Compose configuration

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

These were never in `.env.example` to begin with (verified by the
Phase 6.1 follow-up audit) -- no cleanup was needed here, only
confirmation that the boundary already held. `KAFKA_PORT` (default
`9092`, in `.env.example`/`.env.local` under local-development
infrastructure, §11.3) is the one exception that IS user-facing, because
Compose publishes it to the host.

### 11.3 Internal vs. host listener addresses

```text
container-to-container (kafka:9092, PLAINTEXT)
  -- used by StreamingSettings.bootstrap_servers' default, and by any
     future SceneOps container (a ROS2 bridge) on the sceneops network

host-to-Kafka (localhost:${KAFKA_PORT}, PLAINTEXT_HOST)
  -- used ONLY by scripts/e2e/smoke_streaming.sh, which overrides
     SCENEOPS_STREAMING_KAFKA_BOOTSTRAP_SERVERS at invocation time
     because make smoke-streaming runs on the HOST (uv run python, like
     every other scripts/e2e/* script), not inside a container on the
     sceneops network
```

No SceneOps container-side setting hardcodes `localhost`, and
`StreamingSettings`' code default is the in-network address, never the
host one. The `PLAINTEXT_HOST` listener exists specifically because this
current, real need (the host-run smoke test) requires it -- not spun up
for hypothetical future use.

### 11.4 Smoke test's configuration path

`make smoke-streaming` never hardcodes a broker, topic, or consumer group
independently of `StreamingSettings` -- `scripts/e2e/smoke_streaming.py`
constructs one `StreamingSettings()` and reads `bootstrap_servers`/
`telemetry_topic`/`consumer_group_id` from it throughout;
`smoke_streaming.sh` only overrides the bootstrap-servers env var (§11.3)
before invoking the script. The one smoke-specific value is a per-
invocation consumer-group suffix, always derived from
`settings.consumer_group_id` (§11.1) -- never a bare literal like the
prior `f"smoke-{uuid}"` naming this cleanup removed. Repeated
`make smoke-streaming` runs stay reliable because each invocation gets
its own fresh group (no stale committed-offset state to collide with),
while still exercising the real configured base group id as its prefix.

## 12. Source-of-truth map

- Transport-neutral contract: `packages/sceneops-core/sceneops_core/streaming/`, `packages/sceneops-core/tests/test_streaming_envelope.py`
- Default topic / header-prefix constants: `packages/sceneops-core/sceneops_core/constants/streaming.py`
- Kafka wire mapping + client implementation: `packages/sceneops-streaming/sceneops_streaming/{wire,producer,consumer,config,errors}.py`
- Wire/decode unit tests (no broker required): `packages/sceneops-streaming/tests/test_wire.py`
- Config surface/precedence unit tests (no broker required): `packages/sceneops-streaming/tests/test_config.py`
- Compose service: `compose/streaming.yaml`
- Make targets: `makefiles/streaming.mk` (`streaming-up`/`streaming-down`/`smoke-streaming`)
- Smoke test: `scripts/e2e/smoke_streaming.py`, `scripts/e2e/smoke_streaming.sh`
- Related ADRs: [ADR-005](../adr/005-ros2-vs-kafka-boundary.md) (ROS2 vs. Kafka boundary), [ADR-003](../adr/003-batch-first-architecture.md) (why streaming waited until now)
