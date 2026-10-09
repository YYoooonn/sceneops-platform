# Kafka replay window and retention

Point-in-time verification record (documentation category C). The current contract is in
[Streaming transport](../architecture/streaming-transport.md) §26.1; the decision is
[ADR-014](../adr/014-kafka-replay-window.md).

| | |
| --- | --- |
| Date | 2026-10-10 (host time; broker log 2026-10-09 15:26–16:00 UTC) |
| Branch / base HEAD | `dev` / `f7ebd0c` (working tree with the change uncommitted) |
| Environment | macOS 15 host (Darwin 24.3.0), Docker Compose; `apache/kafka:3.9.2` single-node KRaft, replication factor 1; `capture` image with `confluent-kafka` 2.16.0 / librdkafka 2.16.0; local disk |
| Question | What replay window must Kafka provide so a delayed or failed Capture can be retried safely, and how should retention be configured? |

All experiments used throwaway topics (deleted afterwards) except the configuration audit.
Timings are from one host, one broker, one run each unless a count is given. The harness
scripts were scratch files, not committed; what is reproducible from the repository is the
regression tests named at the end.

## Starting configuration

Nothing was set by the repository. `sceneops.robot.telemetry.v1` had no topic-level
overrides and inherited broker defaults:

```text
retention.ms 604800000 (7 d)   retention.bytes -1   cleanup.policy delete
segment.ms   604800000 (7 d)   segment.bytes 1073741824 (1 GiB)
log.retention.check.interval.ms 300000 (5 min)   file.delete.delay.ms 60000
message.timestamp.type CreateTime   offsets.retention.minutes 10080
auto.create.topics.enable true (compose)   1 partition, RF 1 (broker defaults)
```

The topic held 168 records (77 KB; log start 0). The broker also listed 62 leftover test
topics (`*-test.<hash>.v1`) from earlier suites.

## When records are actually deleted

Three topics created together at 15:28:35 UTC, polled once a second for the low watermark:

| Topic | Configuration | Eligible at | Log start moved at | After eligibility |
| --- | --- | --- | --- | --- |
| r3 size | `retention.bytes` 3 MiB, `segment.bytes` 1 MiB; 10,240 × 1 kB records | at once | 15:29:04 → 6,442 of 10,240 gone | 29 s |
| r1 time, rolled | `retention.ms` 60 s, `segment.ms` 10 s; 100 records, then 1 more after 12 s | 15:29:47 | 15:34:04 → all 101 gone | 4 min 17 s |
| r2 time, one segment | `retention.ms` 60 s, defaults otherwise; 100 records | 15:29:35 | 15:34:04 → all 100 gone | 4 min 29 s |

The broker log shows the retention task at 15:29:03 and 15:34:03: a fixed 5-minute cadence,
so expiry lands on the next tick, between 0 and 5 minutes after eligibility. Observed
consequences:

- When every segment is eligible the broker rolls the active segment and deletes all of
  them (r2, r1): a quiet topic does expire. It is eligibility of the segment's *newest*
  record that counts, so on a busy topic a record lives until the end of its segment's
  span: the roll interval (7 days by default) or 1 GiB, whichever comes first.
- Size retention left 3,798 records (≈ 3.9 MB, above the 3 MiB cap by less than a segment):
  it deletes whole segments while the remainder still exceeds the cap.
- The log start moves at the tick and consumers see it at once; the files are removed 60 s
  later (15:35:03).

## Data rates

Reference workload: the locked `nuscenes-mini-v1` fixtures replayed through the real
replay → ROS 2 → bridge → Kafka → Capture path on a throwaway single-partition topic
(no publication, no PostgreSQL or MinIO state). Rates are computed from the records'
CreateTime; sizes are key + headers + value, and log sizes from `kafka-log-dirs`.

| Fixture | Replay | Records | Source span | Log size | Steady rate | Peak 1 s |
| --- | --- | --- | --- | --- | --- | --- |
| scene-0061 | 2× (corpus default) | 8,899 | 19.5 s | 471,491,353 B | ~49 MB/s, ~930 rec/s | 50.3 MB/s, 949 rec/s |
| scene-0103 | 2× | 9,208 | 20.4 s | 470,898,392 B | ~48 MB/s, ~945 rec/s | 49.9 MB/s, 952 rec/s |
| scene-0061 | 1× (real time) | 8,899 | 19.5 s | 471,544,606 B | ~24.5 MB/s, ~460 rec/s | 25.5 MB/s, 477 rec/s |

- The application bytes of scene-0061 are identical at 2× and 1× (471,158,195 B); the log is
  0.07% larger (batch framing). The log is 1.33× the finalized MCAP (345.7–355.6 MB).
  Each active segment also has two sparse 10 MiB index files.
- Run average (bytes ÷ source span): 24.1 MB/s. Peak-to-steady within a replay: ~1.04; real
  bursts of a live sensor were not measured.
- Bytes by channel, scene-0061: LiDAR 265.5 MB (382 records, 695 kB each); six cameras
  30–37 MB each (~140–165 kB per record); all non-image channels 5.1 MB total
  (`/tf` 511 B, `/vehicle/imu` 733 B, `/vehicle/odom` 1,136 B, `camera_info` ~805 B per
  record), i.e. ~0.26 MB/s and ~370 records/s at real time.
- Small synthetic telemetry (20,002 odometry-sized envelopes with a 20-byte payload through
  the producer's settings): 8,709,719 B, **435 B per record**, so ~415 B of framing (nine
  headers, key, batch), produced at 33,000 records/s.

### Storage for candidate windows (extrapolated)

Computed from the measured rates, assuming the rate holds for the whole window with one
robot streaming continuously, one replica, no compression, no safety factor. Not measured
beyond a 20 s run.

| Window | Sensor workload, 24.1 MB/s | Non-image channels, 0.26 MB/s |
| --- | --- | --- |
| 1 h | 87 GB | 0.9 GB |
| 6 h | 521 GB | 5.6 GB |
| 24 h | 2.1 TB | 22 GB |
| 3 d | 6.3 TB | 67 GB |
| 7 d | 14.6 TB | 157 GB |

By run rather than by time: 0.47 GB per reference run, so a 10-fixture baseline holds
4.7 GB, and the 20 GiB cap holds 41–45 runs (segments go whole).

## Capture timings

| Step | Measured |
| --- | --- |
| Capture container start to a position error (offset beyond the log end) | 0.82 s |
| Live capture: `RUN_END` produced → bag and receipt finalized | 1.46 s (scene-0103, one run) |
| Retry of a stored 471 MB run from its pinned `RUN_START` | 8.69 s and 8.67 s wall, including the start-up above: ~60 MB/s |
| `RUN_START` → `RUN_END` of a replayed run | 23.5 s (includes waiting for the bridge to be ready; not a mission length) |

Detection and retry delay are not platform properties: nothing supervises Capture
([limitations](../architecture/limitations.md)).

## Retention failure experiments

Real broker, a pinned or scanning Capture against records removed with `delete_records`
(which moves the log start exactly as retention does; the natural case is below).

| | Setup | Result |
| --- | --- | --- |
| A | `RUN_START` retained, pinned | finalized, 1.1 s |
| B | start offset below the low watermark | `StartPositionError: ... below the low watermark 1: no longer retained` in 0.1 s; no `.partial` |
| C1 | `RUN_START` trimmed, scan | `RunLifecycleError: telemetry in phase armed: it precedes RUN_START`, 1.1 s; not finalized |
| C1 | `RUN_START` trimmed, pinned at the new low watermark | same |
| C2 | `RUN_START` + 5 telemetry trimmed, scan and pinned | same |
| D1 | whole run trimmed, later records of the partition retained, pinned | below the low watermark, 0.1 s |
| D1 | same, scan | `RunStartNotObservedError` after the 10 s idle guard |
| D3 | run never published, pinned at the high watermark | `not in the log (high watermark 27)`, 0.0 s |
| D3 | same, scan | `RunStartNotObservedError` after the idle guard: same as D1 |
| D2 | partition emptied (low = high), pinned | below the low watermark, 0.0 s |
| E | finalized, then every partition emptied | MCAP and receipt byte-identical, `validate_mcap_file` passes (20 messages) |
| E | re-capture after expiry, pinned / scan | pinned: below the low watermark; scan: `RunStartNotObservedError`; finalized bag unchanged |

Only a start position tells an expired run from one that was never published. A failed
scan of an already finalized run leaves a `.partial` directory beside the final bag; the
capture scan classifies a run with a final bag as finalized.

Publisher and registration cannot read Kafka: the `publisher` and `worker` images contain
neither `confluent_kafka` nor `sceneops_streaming` (`ModuleNotFoundError`), and in the
source only the streaming package, the Capture consumer, writer and lifecycle, the Capture
CLI, the bridge and tools import them.

### Retention advancing under an active Capture

Pinned Capture of the scene-0061 topic (8,899 records, ~53 kB average) from offset 0, its
MCAP writer slowed to 10 ms per record; the log start was moved to 7,000 four seconds in
(544 records written).

```text
Capture failed 13.5 s in after 1,735 records written:
EnvelopeDecodeError: Kafka consumer error: KafkaError{code=_AUTO_OFFSET_RESET ...
  "fetch failed due to requested offset not available on the broker: Broker: Offset out of
  range"} [topic:0@1736]      -- not finalized, .partial left
```

The client delivered the ~1,200 records beyond its write position that it had already
fetched, then failed on the first fetch below the new log start. The error is the consumer's
`auto.offset.reset=error` event, previously reported as a malformed record
(`EnvelopeDecodeError`); it is now `ReplayExpiredError`.

The same without `delete_records`: a raw assigned consumer (`auto.offset.reset=error`, 1 MiB
partition fetch) reading 10 records/s from a topic with `retention.bytes` 3 MiB /
`segment.bytes` 1 MiB. The broker's own retention check (15:39:03 by its 5-minute cadence)
moved the log start to 6,442, far past the consumer's position; the consumer used up its
prefetched data, read 1,978 records in all, and failed with the same `_AUTO_OFFSET_RESET`
error at 15:40:07, about a minute after the check.

## Time and size retention

- A time window is a per-record guarantee (a lower bound, plus the roll interval and 5
  minutes). A size window is `retention.bytes ÷ ingest rate` and moves with the rate.
- r3 above: a 7-day `retention.ms` was in force and the first 63% of the data was gone at
  the first check, 29 s after it was produced.
- Applied to the measured rate, a 20 GiB cap is 14 minutes of continuous sensor streaming
  (20,480 MiB ÷ 24.1 MB/s) however long `retention.ms` is. A cap at 24 h × 24.1 MB/s would be
  2.1 TB.
- Size deletion is segment-granular: with 1 GiB segments the effective cap lies up to one
  segment below the setting.

## Producer clock

Retention judges a record by its timestamp, which is `CreateTime` (the producer's clock; the
producer sets none explicitly). With a producer clock 24 h behind the broker's:

Two topics with `retention.ms` 10 min received 20 records each with producer timestamps 24 h
in the past (15:51:20 UTC). The `CreateTime` topic kept the producer's timestamps and was
emptied at the first retention check, 15:55:59, 4 min 39 s after the records were
written. The `LogAppendTime` topic (the broker stamps the append time) still held all 20
records when observation ended at 15:56:49. A first attempt with `retention.ms` 60 s did not
discriminate: both topics were past 60 s by the first check and were emptied together. A
producer clock ahead of the broker's lengthens the window by the same amount; neither case
was measured.

## What follows from these measurements

- Retention only ever removes a log prefix, so the states of a run are: intact, a prefix
  missing (never finalizes), or gone (`ReplayExpired`); and a finalized bag is independent of
  all three.
- A retry needs `RUN_START` readable when it begins. Per-record lifetime is at least
  `retention.ms`, so `retention.ms ≥ D_max + T_detect + T_retry + T_read`, with `T_retry`
  ≈ 1 s and `T_read` ≈ 8 s for a 0.47 GB run measured here.
- The only measured sources of an early end to that guarantee are a size cap and a
  producer clock behind the broker's.

## Limitations of this record

- One host, one broker, one partition (the sensor measurements) or three (the failure
  experiments); replication factor 1, so nothing here measures replication or multi-broker
  retention.
- Sensor data is a replay of two 20 s fixtures at 1× and 2×, not a live robot; no burst,
  jitter, longer run or multi-robot load was generated.
- The retention tick phase depends on the broker's start time; the 5-minute cadence is
  `log.retention.check.interval.ms`, not a measured property of every broker.
- `T_detect` and the operational recovery delay are policy, not measurements.
- Compression was not used or evaluated.

## Regression tests added

`packages/sceneops-streaming/tests/test_consumer.py` (classification of
`_AUTO_OFFSET_RESET` / `OFFSET_OUT_OF_RANGE` as `ReplayExpiredError`; only a position below
the low watermark is expiry) and, against real Kafka,
`packages/sceneops-recording/tests/capture/test_pinned_start_integration.py` (a run with an
expired prefix is never finalized, pinned or scanning; expired vs not started; a finalized
recording is unchanged by the expiry of its records and by failed re-runs).
