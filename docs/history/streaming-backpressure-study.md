# Capture backlog, replay headroom and producer pressure

Point-in-time verification record (documentation category C). The current contracts are
[Streaming transport](../architecture/streaming-transport.md) §14 and §26.1 and
[ADR-014](../adr/014-kafka-replay-window.md); nothing here changes them.

| | |
| --- | --- |
| Date | 2026-10-10 (host time, UTC+9; experiments 2026-10-09 16:40–17:55 UTC) |
| Branch / HEAD | `dev` / `dcb7137`, clean working tree; the only additions are `tools/benchmarks/streaming/benchmark_capture_backlog.{py,sh}` |
| Environment | macOS 15 host (Darwin 24.3.0); Docker Desktop VM with 8 vCPU, 7.65 GiB RAM and a 62.7 GB disk (89 % used, 7.08 GB free at the start); `apache/kafka:3.9.2` single-node KRaft, replication factor 1, `LogAppendTime`; `capture` image with `confluent-kafka` 2.16.0. The platform's other containers (API, workers, PostgreSQL, MinIO, an idle inference server) kept running, so numbers carry background noise |
| Question | What happens when RobotRuns arrive faster than Capture can durably record them, and which signals and policies control replay-window risk? |

All Capture work used the real `python -m sceneops_capture` CLI, pinned to the acknowledged
`RUN_START` position, against the real broker. Sensor runs replay the locked `scene-0061`
recording (8,897 telemetry records, 471 MB on the topic, 19.5 s) without ROS 2; the
real ROS 2 replay → bridge path was measured separately (§7). Experiments used throwaway
`bp.*` topics on the local broker, except the broker-stall tests, which used a second
512 MB-heap broker. The harness is `tools/benchmarks/streaming/benchmark_capture_backlog.sh`; the
quantities below are one host, one broker and a few repetitions each. Nothing here is a
supported limit.

## 1. Queues and rates in the path

```text
source ──► bridge ──────► Kafka partition log ─────► waiting Capture ──► Capture ──► MCAP + receipt
        DDS history    librdkafka queue   the log IS the queue    (no queue of its own)    (durable)
        keep-all       1 GiB / 100k msgs   bounded by retention
        (RAM)          / 300 s (RAM)       (time, size, disk)
```

| Stage boundary | Where data waits | What bounds it | When downstream is slower |
| --- | --- | --- | --- |
| source → bridge | DDS reader history, keep-all, in the bridge's RAM | nothing configured | the bridge's callback never blocks (§7), so the source is not slowed |
| bridge → Kafka | librdkafka queue in the bridge's RAM | `queue.buffering.max.kbytes` 1 GiB, `.messages` 100,000, `message.timeout.ms` 300 s | grows until a bound is hit, then records fail (§7) |
| Kafka → waiting Capture | the partition log on the broker's disk | `retention.ms`, `retention.bytes` (per partition), free disk | nothing waits in a queue: an uncaptured run is a position in the log; it is lost when retention removes that position (§5) |
| Capture → MCAP | the Capture process (about 147 MB RSS) and its local disk | one attempt per run, started by the launcher | no queue: a run not yet started is a waiting run |

Capture owns no consumer-group lag; "lag" has to be a property of runs, not of a group. The set
of uncaptured runs exists only in the launcher's memory (the `run_started` position); no durable
record of it exists.

## 2. Capacity of one Capture and of one producer

Single-partition topic, runs produced one after another (no interleaving), then drained.

| Workload (per run) | Producer, one process | One Capture | Capture CPU | Capture peak RSS |
| --- | --- | --- | --- | --- |
| ref20: 471.1 MB, 8,897 records | 259 MB/s, 1.9 s CPU (4 ms/MB) | 7.7–8.0 s: **59–61 MB/s** | 3.7–4.0 s (0.5 core; 8.2 ms/MB) | 147 MB |
| ref10: 244.3 MB, 4,631 records | 340 MB/s | 4.32 s: 56.5 MB/s | 2.0 s | 147 MB |
| ref5: 120.9 MB, 2,332 records | 158–300 MB/s | 2.1 s: 57–59 MB/s | 1.2 s | 141–151 MB |
| small ×200,000: 95.8 MB (64 B payload) | **9,988 rec/s** (94 µs CPU/record: CPU-bound) | 4.25 s: **47,000 rec/s**, 22.8 MB/s (19.5 µs/record: CPU-bound) | 3.9 s | 133 MB |
| small ×7,400 (3.5 MB, the non-image profile of a 20 s run) | 6,767 rec/s | 0.41–0.58 s per run | 0.41 s | 73 MB |

- Service time fits `t = 0.2 s + bytes / 62 MB/s` over ref5/ref10/ref20 (in-process; a container
  adds about 0.8 s). A sensor Capture uses half a core, so it is limited by its single-threaded
  fetch/decode/write/validate loop and the fsync and read-back, not by the host.
- Small-record runs are bound by records (CPU) and by the fixed cost per run, not by bytes.
- Kafka disk growth equals the produced bytes (framing is about 415 B per record); the broker used
  0.25–0.33 core at 60 MB/s.
- Real bridge (ROS 2 replay → bridge → Kafka): at real time 24 MB/s costs the bridge about 1.35
  cores (`docker stats`), RSS 117 → 123 MB; an unpaced replay of the same run took 2.84 s
  (8,897 messages, ≈ 166 MB/s, 3,130 msg/s), 0 failed, bridge RSS peak 318 MB.

## 3. Concurrent Captures

Stored runs, drained together (`K = C` runs, sequential production). Aggregate bytes ÷ drain wall.

| C | ref10, P=1 | speed-up | ref10, P=8 | ref5, P=16 | VM CPU (ref10) | notes |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 56.5 MB/s | 1.0× | | | 10 % | |
| 2 | 109.7 | 1.94× | | | 15 % | |
| 4 | 211.5 | 3.74× | | 155 | 33 % | |
| 8 | 309 / 386 / 366 | 5.5–6.8× | 397 / 397 / 378 | 275 | 60–75 % (+3–7 % iowait) | per-capture CPU 2.6–3.6 s vs 2.0 s alone |
| 12 | | | | 340 | 76 % | |
| 16 | | | | 349 | 66 % | per Capture 25 MB/s, wall 4.9 s |

- Near-linear to C = 4; the aggregate plateaus at **≈ 350–400 MB/s (6–7 Captures' worth)** by C ≈ 10–12.
  The limit on this VM is the sum of CPU (60–76 % busy) and disk (≈ 300 MB/s of MCAP writes plus
  read-back validation); the broker stayed below 1.0 core (0.98 at C = 8, P = 1) and the virtual
  network carried 430–660 MB/s.
- Memory is 147 MB per Capture (1.2 GB at C = 8); 16 Captures drove the VM's available memory to 2.4 GB.
- Partitions do not limit concurrency: each Capture is pinned to one partition, and any number can read the same one. P = 1 against P = 8 differ by about 10 % at C = 8, within the run-to-run spread (309–397).
- **Partitions matter through interleaving.** A Capture reads every record of its partition between
  its `RUN_START` and `RUN_END`, other runs' records included. Four runs produced concurrently:

| Partitions | Runs per partition | Bytes read ÷ bytes captured | Capture CPU per run |
| --- | --- | --- | --- |
| 1 | 4 | 5.34× | 1.68 s |
| 4 | 2, 2 | 2.24× | 1.32 s |
| 16 | 1 each | 1.38× | 1.21 s |

  A lone Capture reads 1.35–1.85× its run (prefetch overshoot beyond `RUN_END`, about 100–160 MB).
  A foreign record costs about 16–27 % of an own one in CPU (1.3 ms/MB against 8.2 ms/MB).

## 4. Load regimes

Runs of `ref5` (the first 5 s of the reference run: 120.9 MB, 2,332 records) streamed at real time
(4.9 s), start intervals jittered ±30 %, 24 runs (20 for A), one partition, a launcher that starts a
Capture on `RUN_START` when a slot is free (oldest first). Time is from the first run.

| Regime | C | Offered | ÷ isolated C×57 | Peak waiting runs | Peak oldest-uncaptured age | Peak backlog | Backlog growth while arriving | Wait for a slot, mean / max |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A stable | 1 | 24 MB/s | 0.42 | 1 | 5.9 s | 145 MB | 0.0 MB/s | 0.8 / 3.0 s |
| A′ | 1 | 35 | 0.61 | 1 | 7.0 s | 193 MB | 0.3 | 1.8 / 3.8 s |
| B near saturation | 1 | 55 | 0.96 | 7 | 22.8 s | 821 MB | **+14.2** | 10.4 / 19.7 s |
| C overload | 1 | 81 | 1.41 | 14 | 52.0 s | 1,692 MB | **+46.7** | 25.1 / 48.5 s |
| C, 2 Captures | 2 | 81 | 0.71 | 5 | 12.2 s | 603 MB | +15.9 | 5.1 / 8.9 s |
| C, 3 Captures | 3 | 81 | 0.47 | 1 | 5.3 s | 264 MB | +3.1 | 0.7 / 1.6 s |
| C, 8 partitions | 1 | 81 | 1.41 | 10 | 24.9 s | 1,086 MB | +26.8 | 12.2 / 22.9 s |

- A stable system holds the oldest uncaptured age at about one run's duration (the run cannot end sooner);
  a Capture attached at `RUN_START` holds its slot for the run's duration plus 0.3 s (5.0–5.3 s here).
- Offered load against the **isolated** capacity is a poor predictor: B (0.96) is an overload and 0.61 is
  stable. Capture rates fall under interleaving: while arrivals overlap on one partition (≈ 3.3 live
  runs) a stored run takes 3.1–3.7 s (33 MB/s) against 2.1 s alone (57 MB/s). Measured effective rate
  of one slot: `offered − growth` = 41 MB/s (B), 34 MB/s (C), 54 MB/s with eight partitions.
- Once a backlog exists a Capture reads stored runs, which is faster than following a live run, so a
  backlog drains faster than a live-attached system could track a run (after arrivals stopped, C drained
  its 1.6 GB in about 45 s). During an overload the oldest uncaptured age grows at `1 − Cμ_eff/λ`
  seconds per second: 0.55–0.58 measured, 0.58 predicted for C.
- Adding Captures is effective and immediate (peak age 52 s → 12 s → 5 s for C = 1 → 2 → 3).

**Small runs (byte-poor, run-rate-bound).** 1,850 records (0.9 MB), 5 s at 370 rec/s, a run every 0.33 s
(3 runs/s, 2.6 MB/s), 200 runs:

| C | Peak waiting runs | Peak age | Peak backlog | Capture time per run |
| --- | --- | --- | --- | --- |
| 1 | **105** | **65.0 s** | 89 MB | 0.7 s |
| 2 | 41 | 15.1 s | 30 MB | 0.8 s |
| 3 | 15 | 5.9 s | 7 MB | 1.1 s |
| 4 | 13 | 5.9 s | 7 MB | 1.4 s |

Bytes behind are negligible while 105 runs and 65 s are waiting. The throughput of tiny runs plateaus at
about 2.7–2.9 runs/s whatever C ≥ 3 (the load generators share the VM, and per-run start-up cost rises
with concurrency: 0.7 → 1.4 s).

## 5. Replay headroom and expiry under overload

**Broker check cadence.** A record already past `retention.ms` was removed 226.9 s after it was produced,
fixing the phase of the broker's 5-minute retention task (epoch 1791566139.9, i.e. 2026-10-09 17:15:39.9 UTC, plus
multiples of 300 s). The three expiries below fell at exactly the predicted instants (38.0 s, 62.0 s, 38.0 s
into the runs). Deletion only happens at those instants.

**Definition.** For the oldest uncaptured run X on a partition:

```text
age(X)             = now − time RUN_START was appended
bytes_since(X)     = bytes appended to the partition since X's RUN_START
headroom_time      = retention.ms/1000 − age(X)
headroom_bytes     = retention.bytes − segment.bytes − bytes_since(X)
headroom_size_s    = headroom_bytes / recent ingest of the partition (bytes/s)
replay_headroom_s  = min(headroom_time, headroom_size_s, headroom_disk_s)
                     headroom_disk_s = (free broker disk − margin) / ingest
```

`headroom_bytes` is independent of the ingest rate, so it is the quantity to alert on; the seconds form
needs a rate and should use a high recent value. Expiry follows when either term reaches zero *and* a
retention check runs; nothing earlier than zero is lost, and up to 300 s of slack may follow that nobody can rely on.

| Experiment (C = 1, 24 runs at 81 MB/s, ref5) | Topic | Headroom reached 0 | Retention check | Result |
| --- | --- | --- | --- | --- |
| size | `retention.bytes` 800 MiB, `segment.bytes` 32 MiB | t = 15 s (918 MB behind > 806 MB) | t = 38.0 s: low watermark 0 → 37,941 | runs 010–017 `ReplayExpired` (010 mid-read; 7 at once in 0.3–0.4 s); 018–023 and the 10 already finalized unaffected |
| time | `retention.ms` 30 s, `segment.ms` 10 s | t = 49 s (age 30 s) | t = 62.0 s: low watermark → 43,051 | runs 015–019 expired (ages 33–40 s); 020–023 kept (ages 28–31 s) |
| size + `retention.bytes` raised to 4 GiB at t = 21 s | as size | t = 15 s | t = 38.0 s: **nothing deleted** | all 24 runs captured |

- The boundary fell where the model puts it: the run 846 MB behind expired, the run 702 MB behind survived
  (threshold 806 MB); in the time experiment expiry fell between ages 31 and 33 s (30 s + part of a segment).
- The headroom crossed zero 23 s and 13 s before the loss. That gap is the position of the check, not a
  property of the system: it is uniform over 0–300 s.
- From `headroom_bytes` and the recent ingest, the predicted time to zero was +4.5 s at t = 10 s (actual +5.0 s)
  and +14.7 s at t = 5 s (actual +10 s: the ingest was still ramping up).
- Estimating `bytes_since` from offsets and partition size alone (no producer counters),
  `(high − offset(X)) × size / high`, was within −2 % on average (−19 % … +2 %) on single-partition runs.
- Raising `retention.bytes` online is effective if it precedes the check and costs disk.

**Why the metrics differ.** Values at the instant of first loss against the highest values of runs that lost nothing
(24 h / 20 GiB):

| Run | Uncaptured runs | Oldest age | Bytes since oldest | Offsets behind | Loss |
| --- | --- | --- | --- | --- | --- |
| size, at headroom 0 | 8 | 10.8 s | 894 MB | 17,820 | yes (at the next check) |
| time, at headroom 0 | 12 | 30.5 s | 1,621 MB | 30,795 | yes |
| C (C = 1), peak | 15 | 52.0 s | 1,887 MB | 36,006 | none |
| small C = 1, peak | 106 | 65.0 s | 97 MB | 202,108 | none |

The count, age, bytes and offset distance at a loss are lower than the peaks of a run that lost nothing: no
threshold on any of them transfers between configurations. Only headroom is zero at the losses and positive
(215 s at the lowest, from the 20 GiB cap at 81 MB/s) elsewhere.

**Effective window.** `W_eff = min(retention.ms, (retention.bytes − segment.bytes) / ingest)` per partition. The size
term governs whenever ingest exceeds `retention.bytes / retention.ms` = 0.25 MB/s for the local broker (20 GiB / 24 h):
one reference robot (24.1 MB/s) has a window of 14 min; an overload at 81 MB/s, 4.2 min.

## 6. Disk

At the start the Docker VM had 7.08 GB free while the topic cap is 20 GiB, so the cap does not protect this VM; the
broker-stall tests and a 3 GB experiment drove free space to 1.57 GB, which stopped one run at the harness guard
(1.6 GB). Deleted topic data returns after about 60 s (`file.delete.delay.ms`).

## 7. Producer pressure

**Library level** (`KafkaTelemetryProducer.publish`, the bridge's path), broker paused 65 s at real time
(25 MB/s, sensor payloads):

| Time since pause | Observation |
| --- | --- |
| 0–44 s | `publish` returns in ≤ 13 ms (mean ≤ 0.4 ms) whatever the broker does; queue and RSS grow with the data (540 → 1,637 MB) |
| 44.7 s | the 1 GiB bound is hit: **every** later publish raises `BufferError: Local: Queue full` |
| after the broker returned (+65 s) | still every publish fails until the end of the run (75 s): 43,280 of 68,339 offered records; `flush()` then returns success in 0.3 s |

`produce()` raises before the `poll(0)` that serves delivery reports, so reports are never served after the
queue fills and the accounting never frees: the queue stays full after the broker has recovered. (The queue length
stayed at 21,808 until the final `flush`.) Small records (370 rec/s, 335 s outage): `BufferError` at the 100,000-message
bound (280.6 s, 270 s after the pause), records queued 300 s failed with `_MSG_TIMED_OUT`, and `flush()` raised it.

**Real bridge** (ROS 2 replay of `scene-0061` at 1×, broker stalled with `docker pause`):

| Stall | Bridge | Replay | Outcome |
| --- | --- | --- | --- |
| 12 s mid-run | RSS 123 → 416 MB (≈ the 12 s of data) | ≈ 20 s | absorbed; exit 0, run complete |
| 60 s from t = 8 s | RSS plateau 423 MB; at +41 s: `telemetry flush failed: 5798 telemetry record(s) still undelivered after 10.0s`, RUN_END withheld, exit 1 | 19.6 s, max lag 12 ms | Capture refuses the run; the queued records die with the process |

The bridge applies **no backpressure** to the source: callbacks return at once, the source continues at its pace,
and the data accumulates in the librdkafka queue (not in DDS history) until a bound is hit. The 20 s reference run
(471 MB) cannot reach the 1 GiB bound; the persistent-`BufferError` state was shown at the producer level only,
through the same `_produce_sync` the bridge calls.

Follow-up: [streaming-producer-overload-study.md](./streaming-producer-overload-study.md) reproduces the persistent `BufferError`, explains it and measures the bridge through it.

## 8. Thresholds, capacity model and responses

Parameters measured here: `μ_own` ≈ 57–62 MB/s, `T0` ≈ 0.2 s (+ container start), `κ` ≈ 0.25–0.35 (cost of a foreign
byte relative to an own one: 0.25 from the interleaving test, 0.36 fitted to the regimes), ceiling `μ_ceil` ≈ 350–400 MB/s on this 8-vCPU VM, tiny-run ceiling ≈ 2.8 runs/s.

```text
μ_eff(partition) = μ_own / (1 + κ (c − 1))      c = runs interleaved on a partition ≈ 1 + (L − 1)/P,  L = λ_runs · D (live runs at once)
stable           λ_bytes < min(C · μ_eff, μ_ceil)   and   λ_runs · s_run < C     (s_run = T0 + B/μ_eff)
overload         backlog grows at λ − C·μ_eff;  oldest age grows at 1 − C·μ_eff/λ
loss after       (retention.bytes − segment.bytes − Q0) / (λ − C·μ_eff)     (size)
                 (retention.ms/1000 − age0) / (1 − C·μ_eff/λ)               (time)
```

Checks (L = 4.9 s / 1.5 s = 3.3): P = 1 gives 34–39 MB/s (measured 34); P = 8 gives 56–58 (measured 54).
Slots to hold a stored backlog: `C ≥ λ_runs · s_run`; a live-attached slot holds `D + 0.3 s`.

Levels, with `R = T_detect + T_retry + T_read` (T_read ≈ run bytes/μ_eff = 8–14 s here, T_retry ≈ 1 s,
T_detect set by whoever supervises) and `T_ahead = waiting runs × s_run / C`:

| Level | Condition | Meaning |
| --- | --- | --- |
| healthy | headroom ≥ 3R + T_ahead | a failed Capture can be noticed and retried with margin |
| warning | R + T_ahead ≤ headroom < 3R + T_ahead | the backlog is closing the window; act automatically |
| critical | 0 < headroom < R + T_ahead | a failed Capture can no longer be retried safely |
| replay-expired | a run's `RUN_START` is below the low watermark (`ReplayExpiredError`) | not recoverable from Kafka |

(With `T_detect` = 60 s, R ≈ 70–75 s; the local broker at 81 MB/s starts at headroom 215 s: warning from the first second.)

**Response order.** (1) Automatic, first: start more Captures when runs wait and headroom is below healthy; Captures are
independent and stateless, scale to ≈ 6–7× one Capture on this VM, and showed the largest effect (52 → 5 s). (2) Automatic,
emergency: raise the topic's `retention.bytes`/`.ms` before the check while disk allows. (3) Alert an operator at warning
if Captures are at the host ceiling. (4) Capacity planning: partitions at least the number of concurrent live runs,
`retention.bytes` ≥ ingest × (R + margin), disk above the cap of every partition, more Capture hosts. Slowing producers or
rejecting runs has no signal path today (the bridge cannot see Kafka state and the source cannot be slowed).

## 9. Minimal signals

| Signal | Source |
| --- | --- |
| `oldest_uncaptured_age_seconds` | launcher: now − `RUN_START` ack time of the oldest run without a finalized bag |
| `uncaptured_bytes_since_oldest` | partition size and watermarks (estimate above) |
| `stream_ingest_bytes_per_second`, `broker_free_disk_bytes`, `kafka_partition_bytes` | broker |
| `waiting_capture_count`, `active_capture_count` | launcher |
| `replay_headroom_seconds` (derived), `replay_expired_total` | derived / Capture exit |

## 10. Limitations

- One host, one broker, replication factor 1; repetitions are few (C = 8 four times, others once); background containers were running.
- Sensor runs are the locked recording published directly, not through ROS 2, except §2 and §7.
- Regimes last 36–90 s (24 runs of 5 s): they show trends, not steady state; the 24 h / 20 GiB production-local retention was not exhausted.
- `κ` and `μ_ceil` come from this VM's CPU and disk; only the structure of the model is portable.
- The harness ran from the scratch directory during the measurements; the committed tool is the same code reorganised.
