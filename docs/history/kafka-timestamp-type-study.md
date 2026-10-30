# Kafka timestamp type study: `CreateTime` vs `LogAppendTime`

Point-in-time record. The current contract is
[Streaming transport](../architecture/streaming-transport.md) §26.1; the decision is
[ADR-014](../adr/014-kafka-replay-window.md). Follows the "Producer clock" section of the
[Kafka replay window study](./streaming-retention-study.md), whose `LogAppendTime` check did not
separate the two settings.

## Question

Should retention age a record by the producer's clock (`CreateTime`) or by the broker's append
time (`LogAppendTime`)?

## Environment

```text
date         2026-10-10 (broker log times KST, UTC+9)
git HEAD     3c381a4 on dev, with the retention work of ADR-014 uncommitted
broker       apache/kafka:3.9.2, single-node KRaft, 1 partition and RF 1 per throwaway topic,
             log.retention.check.interval.ms 300000 (default), broker and clients on one host
client       confluent_kafka 2.15.1 (librdkafka 2.15.1), idempotent producer
before       log.message.timestamp.type CreateTime (inherited default); live topic
             sceneops.robot.telemetry.v1: no topic overrides, retention.ms 24 h
```

A producer clock offset is produced by setting the record timestamp explicitly
(`produce(timestamp=wall_ms ± 24 h)`). On the wire this is identical to a producer whose clock
is that far off; no real skewed host was used.

## 1. Live topic under `CreateTime`

196 records, all `TIMESTAMP_CREATE_TIME`. The Kafka timestamp was the producer's wall clock at
publish (2026-10-07 to 2026-10-09). The envelope headers carried unrelated values
(`source_timestamp_ns` 1.7e18 = 2023-11-14, `ingest_timestamp_ns` +0.5 s from it; the fixtures
use fixed clocks), so the three timestamps are independent:

| Timestamp | Owner | Used by |
|---|---|---|
| Kafka record timestamp | producer (`CreateTime`) or broker (`LogAppendTime`) | retention, timestamp lookup; no SceneOps code |
| `source_timestamp_ns` | the bridge's verbatim copy of the source's stamp | not written separately (the payload carries it) |
| `ingest_timestamp_ns` | the bridge, at the transport boundary | MCAP `publish_time` |

MCAP `log_time` is Capture's own receive time and is not derived from any Kafka or envelope
timestamp (streaming-transport.md §20).

## 2. Throwaway topics, 20 records each, 50 ms apart

Producer clock correct, 24 h behind, 24 h ahead; one topic per cell.

Stored Kafka timestamp (record timestamp minus the producer's wall clock when it produced):

| `message.timestamp.type` | producer clock | timestamp type read back | Kafka timestamp − producer wall clock |
|---|---|---|---|
| CreateTime | correct | CREATE_TIME | 0 ms |
| CreateTime | −24 h | CREATE_TIME | −86,400,000 ms |
| CreateTime | +24 h | CREATE_TIME | +86,400,000 ms |
| LogAppendTime | correct | LOG_APPEND_TIME | −1 to +507 ms |
| LogAppendTime | −24 h | LOG_APPEND_TIME | 0 to +514 ms |
| LogAppendTime | +24 h | LOG_APPEND_TIME | −1 to +506 ms |

The up-to-~0.5 s excess is the first record of each producer (connection set-up), not clock
skew. In all six topics every record's `source_timestamp_ns` and `ingest_timestamp_ns` header
read back exactly as sent.

Retention (`retention.ms` 30 min; controls 60 s). All records were produced at about 01:17 KST
and the watermarks were polled every 20 s for 7.5 min. The first retention check fell at about
01:21 (3 min 51 s to 4 min 11 s after production; 240 s is the polling granularity):

| `message.timestamp.type` | producer clock | `retention.ms` | After the first check |
|---|---|---|---|
| CreateTime | correct | 30 min | retained (0/20) |
| CreateTime | −24 h | 30 min | **emptied** (low = high = 20) |
| CreateTime | +24 h | 30 min | retained |
| LogAppendTime | correct | 30 min | retained |
| LogAppendTime | −24 h | 30 min | retained |
| LogAppendTime | +24 h | 30 min | retained |
| CreateTime | correct | 60 s (control) | emptied |
| LogAppendTime | correct | 60 s (control) | emptied |

A first attempt at the same experiment saw the `CreateTime` / −24 h topic already empty when
read back 13 s after it was created, because a check fell in between; the design above separates
the read-back from the retention window.

Timestamp lookup (`offsets_for_times`). One partition: offset 0 produced with the broker's
default stamp, then 20 records produced with the clock offset, a 2 s pause before the 11th
record (offset 11) so that its wall-clock start `T` is known:

| `message.timestamp.type` | producer clock | lookup(`T`) (truth 11) | lookup(`T`+1 h) | lookup(`T`−1 h) |
|---|---|---|---|---|
| CreateTime | correct | 11 | none | 0 |
| CreateTime | −24 h | **none** | none | 0 |
| CreateTime | +24 h | **1** | 1 | 0 |
| LogAppendTime | correct | 11 | none | 0 |
| LogAppendTime | −24 h | 11 | none | 0 |
| LogAppendTime | +24 h | 11 | none | 0 |

"None" is the answer returned when no record has a timestamp at or after the query.

## 3. After the change

The broker was recreated with `KAFKA_LOG_MESSAGE_TIMESTAMP_TYPE=LogAppendTime`. The existing
live topic reports `message.timestamp.type=LogAppendTime` (inherited from the broker; no
topic override) with its 196 earlier records untouched.

Regression test `test_topic_timestamp_integration.py` (a topic created with no per-topic
config; records produced with the clock exact, −24 h and +24 h): 4 passed. With the broker's
cluster-wide default temporarily set back to `CreateTime` through a dynamic broker config
(removed afterwards), the same 4 tests failed.

Other suites on the changed broker: `make streaming-test` (bridge 55, capture suite 183 including
the 4 new tests, bridge + Capture 6; all passed) and `make smoke-streaming` (39 passed, 0
failed).

## Repository audit

No code reads the Kafka record timestamp: no `Message.timestamp()`, `offsets_for_times`,
`kafka-get-offsets --time` or timestamp-based fallback exists in `packages/`, `apps/`,
`integrations/`, `tools/` or `tests/`. Capture, pinned start, the bridge, the launcher and the
baselines work from positions (offset), headers and payload. The only references to the Kafka
timestamp type are retention documentation. The envelope's own clocks are headers set by
the producer and are independent of either setting.

## Limitations of this record

- One host, one broker, one partition per topic, 20 records per topic; the broker and the
  clients share a clock, so broker clock error was not measured.
- Producer skew is emulated with an explicit record timestamp, not a skewed host.
- The ahead-of-broker case was observed through one retention check only, not until expiry.
- Retention was measured at one check; the 3 min 51 s to 4 min 11 s range is polling
  granularity, not a property of the broker.
- The lookup table uses one partition and one pause; it shows the dependence on the stamp,
  not lookup cost or time-index behavior at scale.
- Records appended before the change keep their `CreateTime` stamp; no mixed-stamp topic was
  measured beyond the single leading record in the lookup experiment.
