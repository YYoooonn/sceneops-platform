# Streaming Producer Ordering and Duplicate Semantics Study

> **History — point-in-time record, not current architecture.** The producer contract
> that resulted from this study is described in
> [Streaming transport](../architecture/streaming-transport.md) §4 and §14.

**Date:** 2026-10-09
**HEAD:** `25fb8a1` (branch `dev`); the producer change made from this study was uncommitted when measured.
**Environment:** macOS (Darwin 24.3.0, arm64), Docker Desktop, `apache/kafka:3.9.2` KRaft single broker, 1 partition per test topic unless stated, `confluent-kafka` 2.15.1 (librdkafka 2.15.1), producer and broker on one host.
**Question:** which producer configuration gives one RobotRun's `RUN_START`, telemetry and `RUN_END` predictable ordering and duplicate behaviour under retries and broker failures.

## 1. Configuration before the study

SceneOps set `acks=all` only. Everything else was a librdkafka default
(librdkafka `CONFIGURATION.md`):

| Setting | Value | Origin |
| --- | --- | --- |
| `acks` | `-1` (all) | SceneOps explicit (also the default) |
| `enable.idempotence` | `false` | default |
| `message.send.max.retries` | 2147483647 | default |
| `max.in.flight.requests.per.connection` | 1000000 | default |
| `retry.backoff.ms` / `.max.ms` | 100 / 1000 | default |
| `linger.ms` | 5 | default |
| `message.timeout.ms` | 300000 | default |
| `request.timeout.ms` | 30000 | default |
| `topic.metadata.refresh.interval.ms` | 300000 | default |
| `partitioner` | `consistent_random` (CRC32 of key, `% partition_count`) | default |
| `queue.buffering.max.messages` | 100000 (`produce()` raises `BufferError` when full) | default |

`KafkaTelemetryProducer.publish` returns after `produce()`; delivery errors surface only
through `flush()`, which keeps the last error. Kafka itself guarantees per-partition
ordering of what the broker appended; with `enable.idempotence=false` and retries enabled,
librdkafka documents that retries may reorder.

## 2. Reordering investigation

Nothing was reordered in normal operation or when publishing concurrently:

| Test | Result |
| --- | --- |
| 200 000 serial records, current config | 0 gaps, 0 duplicates, 0 descents, callbacks in order |
| `KafkaTelemetryProducer.publish` serial / `asyncio.gather` / 8-wide, 100 000 records | 0 descents in all four runs (3 concurrent runs) |

The earlier anomaly was **not reproduced as broker reordering**. Under fault injection
the current configuration produces two different effects:

1. **Duplicate replay that looks like reordering.** After a connection loss, every in-flight
   batch is re-sent. The broker already appended some of them, so the log contains
   `…, 5800, 4201, …`: a backwards step in offset order with every sequence number present.
   Delivery callbacks reported no error and fired in order.
2. **True reordering.** When one produce request is lost while later in-flight requests
   succeed, the retried batch is appended after them (first occurrences out of order:
   `19999` before `8001`).

An application-side cause was not found: the bridge awaits each `publish`, and concurrent
`to_thread` publishing did not reorder at 100 000 records.

## 3. Fault experiments

Faults for §3.1 were injected with a Kafka-frame-aware TCP proxy in front of a second study
broker, with 20 ms added response latency to keep several requests in flight. These are
synthetic single-request faults, not observed network behaviour. 20 000 records, one fault
at 40 % of the run (counts are extra records / backwards steps in offset order):

### 3.1 Single faults, one pipelined connection

| Fault | A: current | B: idempotent | C: `max.in.flight=1` | D: idempotent + `max.in.flight=5` |
| --- | --- | --- | --- | --- |
| connection cut after forwarding a request | +8 453 dup, 2 steps | 0 / 0 | 0 / 0 | 0 / 0 |
| connection cut before forwarding | +17 043 dup, 2 steps | 0 / 0 | 0 / 0 | 0 / 0 |
| one response dropped | +18 386 dup, 1 step | 0 / 0 | +200 dup, 1 step | 0 / 0 |
| one request dropped | +17 504 dup, **1 true reorder** | 0 / 0 | 0 / 0 | 0 / 0 |

No configuration reported a delivery error in any row. 8 consecutive cuts: A recovered in
31 s with +7 127 dup; B and D recovered in 109 s and 134 s with 0 dup / 0 reorder (reconnect
backoff after repeated cuts; the idempotent runs were slower to resume).

### 3.2 Broker-level faults (60 000 records at about 5 000/s, fault at 40 %)

| Fault | A: current | B: idempotent |
| --- | --- | --- |
| `docker restart` of the broker | 0 lost / 0 dup / 0 reorder | same |
| `docker pause` 10 s | same | same |
| network blackout 10 s (proxy) | same | same |

### 3.3 Delivery timeout (`message.timeout.ms=5000`, 12 s blackout, 100 000 records at about 2 000/s)

Every configuration (A, B, B with `enable.gapless.guarantee=true`) failed a contiguous range
with `_MSG_TIMED_OUT` (29 000–52 800 records, longer than the outage because of reconnect
backoff and per-record timeouts) and then delivered later records: a **hole in the sequence**
with later data present. 0 duplicates, 0 acknowledged records missing from the log. The
gapless option did not turn this into a fatal error. Idempotence does not prevent holes.

With 1 000 tail telemetry records timed out and a stand-in `RUN_END` delivered after
reconnecting (A and B): the log held telemetry `0..999` contiguous plus the `RUN_END`; the
1 000 delivery errors were visible only to the producer. `RUN_END` carries no record count,
so the log alone does not reveal the loss.

### 3.4 Process faults

| Fault | Result (3 trials each, A and B) |
| --- | --- |
| `kill -9` of the producer mid-stream | log is always a contiguous prefix; 0 acknowledged records missing; 0 holes, 0 duplicates; 0–1 000 un-acknowledged records present |
| `close()` with the broker unreachable | `publish` ×100 returned in 0.01 s without error; `close()` raised `TimeoutError` after 10.1 s; records still queued die with the process |

## 4. Partition metadata freshness

Topic created with 2 partitions, one key (CRC32 maps to partition 1 of 2 and partition 3 of 4),
10 records/s, partitions raised to 4 at t = 30 s, idempotent producer:

| Producer | Moved partition 1 → 3 |
| --- | --- |
| default refresh interval, two 400 s runs (idempotent and not) | at t = 300.1 s ("periodic topic and broker list refresh"), seq 2885; about 270 s of old mapping |
| default refresh interval, three concurrent 100 s runs | no move within 100 s |
| `topic.metadata.refresh.interval.ms=5000` | 1.2 s after expansion |
| default + explicit `list_topics(topic)` at +10 s | immediately after the call |
| default, one earlier 380 s run | 11.5 s after expansion, cause not captured (no debug logging in that run) |

An active `robot_run_id` does move partitions mid-run once the producer sees the new count;
stale metadata only delays it, it does not prevent it, and an explicit refresh makes it
immediate.

## 5. Limitations

- One broker, one host, replication factor 1; no leader election or ISR changes. Setting `min.insync.replicas` above the replication factor cannot inject `NOT_ENOUGH_REPLICAS` on one broker (Kafka caps it).
- §3.1 faults are synthetic and run with 20 ms added latency; duplicate counts scale with the number of in-flight records and are not predictions for other latencies.
- One fault per run and one run per cell in §3.1–§3.3; the 8-cut burst, the delivery-timeout hole range and the early metadata refresh vary between runs.
- The harness was not added to the repository.
