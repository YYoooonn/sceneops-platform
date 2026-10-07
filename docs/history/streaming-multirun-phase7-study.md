# Phase 7.0 — Multi-Run Streaming Architecture Study

> **History — superseded study, not current architecture.** This study prototyped and
> benchmarked a Continuous Capture Router and a partition-aware alternative. Neither is
> part of the platform: the router and its benchmark were removed, and capture is one
> one-shot process per `robot_run_id` that finalizes only on `RUN_END`
> ([ADR-008](../adr/008-acquisition-lifecycle-reliability.md), amendment;
> [Streaming transport](../architecture/streaming-transport.md) §26). The tooling under
> `scripts/dev/phase7/` no longer exists. Its measurements of full-topic replay cost
> remain evidence for the limitation recorded in
> [Current limitations](../architecture/limitations.md) §2.

> Point-in-time record (category C — historical evidence, per this
> repo's documentation taxonomy) of the Phase 7.0 architecture/benchmark
> study: current run-scoped capture audited, benchmarked against
> growing topic history and a multi-run workload, and compared against
> two prototyped alternatives (partition-aware capture, a Continuous
> Capture Router). Not a living architecture contract — see
> [Streaming transport](../architecture/streaming-transport.md) and
> [Streaming reliability & scale baseline](./streaming-reliability-scale-baseline.md)
> for the frozen contracts this study builds on and does not change.
> Numbers here are frozen to the date/commit/environment below.

**Date:** 2026-09-30
**Commit (HEAD throughout this study, unchanged):** `531f7433d0b1ab350482e1f5a9f9829066337144` (branch `dev`)
**Environment:** macOS 15.3.2 (Darwin 24.3.0, arm64), Docker 29.4.1, 8 logical CPUs, single-node local Compose stack (`apache/kafka:3.9.2` KRaft mode, 1 broker; default telemetry topic `sceneops.robot.telemetry.v1` at its usual 1 partition; a second, isolated, study-only 4-partition topic created for §6 and dropped afterward). Same class of environment as the Phase 6.6/6.7 baselines.

No production code was changed. This is a study: new benchmark/prototype tooling under `scripts/dev/phase7/` only. No git commit was made, per instruction.

---

## 0. Note on the topic's starting state

Partway through this study's setup, the default topic's history from
earlier Phase 6 work (~11,666 messages, `TopicId=HPX5S...`) disappeared
— the topic was found recreated under a new `TopicId` (`A-N6X...`) with
the Kafka broker container itself never having restarted
(`RestartCount=0`, continuous uptime). This was not caused by this
study (no `streaming-down`/`local-reset`/topic-delete command was
issued here) — most likely a `make streaming-down`/`local-reset` run
elsewhere against the same shared local stack. It does not affect any
conclusion below: every checkpoint in §4 explicitly (re)builds the
topic history it measures against, so the study's own measurements are
self-contained and reproducible regardless of what came before.

---

## 1. Current capture architecture audit

Audited directly against the current code (all paths below, `HEAD`
`531f743`), cross-checked against `docs/architecture/streaming-transport.md`
(Part 3) and `docs/history/streaming-reliability-scale-baseline.md`
(§14), which this study treats as authoritative background, not
re-derived from scratch:

```text
ROS2 (or a synthetic producer, for this study)
  -> TelemetryEnvelope (packages/sceneops-core/sceneops_core/streaming/)
  -> KafkaTelemetryProducer (packages/sceneops-streaming/.../producer.py)
     -- key = robot_run_id (wire.partition_key), acks=all
  -> Kafka topic sceneops.robot.telemetry.v1 (1 partition, prod default)
  -> ros2/capture/capture_consumer.py: run_capture()
     -- KafkaTelemetryConsumer (subscribe()-based, packages/sceneops-streaming/.../consumer.py)
     -- group.id = derive_capture_group_id(base, robot_run_id)
        (ros2/capture/group_id.py) -- one fresh, run-scoped Kafka
        consumer group PER robot_run_id, SHA-256-suffixed, deterministic
     -- auto.offset.reset=earliest, enable.auto.commit=False
     -- _RunFilter: accepts only this (robot_id, robot_run_id), enforces
        single-partition invariant
     -- _SequenceTracker: enforces 0..N-1 completeness, bounded dup policy
     -- McapCaptureWriter (rosbag2_py.SequentialWriter) -> validate
        (read-back) -> finalize_bag() (atomic rename+fsync) -> commit()
  -> CaptureResult (local file + Kafka provenance only, not canonical)
  -> apps/worker/.../robots/registration.py: register_robot_run_capture()
     -- ArtifactStore upload/verify -> ArtifactRecord -> RobotRunRecord
        (status=COMPLETED, set only AFTER the whole file is captured
        and finalized -- no canonical row exists during capture itself)
```

Key facts this study's benchmarks and recommendations depend on:

- **Partitioning key = `robot_run_id`** (frozen, `streaming-transport.md`
  §6) — every message for one run lands on exactly one partition,
  deterministically, under a fixed partition count. This is what makes
  partition-aware capture (§5, prototype B) possible at all.
- **`run_capture()` uses `KafkaTelemetryConsumer`, which only
  `subscribe()`s** (`consumer.py:57`) — it never `assign()`s to a
  specific partition. On today's 1-partition topic this is moot (the
  sole partition is always assigned regardless), but on a
  multi-partition topic, a lone consumer-group member that
  `subscribe()`s is handed **every** partition, not just the one its
  target `robot_run_id` lives on (§6).
- **`KafkaTelemetryConsumer.poll()` wraps every single Kafka `poll()`
  call in `asyncio.to_thread(...)`** (`consumer.py:59-62`). This is a
  correctness-neutral implementation detail that turns out to dominate
  the measured rescan cost at scale (§4.1) — separate from, and larger
  than, the architectural "run-scoped group scans full history" cost
  this study was originally commissioned to quantify.
- **`RobotRunStatus` already has a `RECORDING` value**
  (`sceneops_core/robots/schemas/enums.py`) but nothing in the current
  pipeline ever creates a canonical `RobotRun` row in that state — the
  only `create_run()` call (`registration.py:235`) happens after
  capture is fully finalized, with `status=COMPLETED` directly. There
  is today no canonical signal of "a run is currently being captured,"
  only "a run finished being captured." Relevant to §10.
- **`CaptureResult`** (`capture_consumer.py`) already carries exactly
  the fields a future `CaptureSession` would need to persist/resume
  (`robot_id`, `robot_run_id`, `path`, `message_count`, `partition`,
  `first_offset`/`last_offset`, `first_sequence`/`last_sequence`,
  `sha256`) — it is currently only a return value, never a persisted or
  resumable runtime record. Relevant to §9.
- Existing scale/reliability tooling this study extends rather than
  replaces: `scripts/dev/benchmark_streaming_capture_scale.py` (Phase
  6.6, single-checkpoint produce/capture benchmark) and the Phase 6.6.1
  addendum's manual two-point rescan comparison (3.47s @ light history
  vs 43.9s @ ~150k prior messages). §4 below is a direct, larger-scale,
  automated continuation of that same addendum.

---

## 2. Benchmark/prototype tooling added (this study)

All new, all under `scripts/dev/phase7/` — none of it imports into or
is imported by production code (`ros2/capture/`, `sceneops-streaming`,
`apps/worker`); it only imports FROM those frozen modules, matching how
`scripts/dev/benchmark_streaming_capture_scale.py` already does:

```text
scripts/dev/phase7/producer.py          host-side (no rosbag2_py needed):
                                         noise / single-run / interleaved
                                         multi-run publishing, real Kafka
scripts/dev/phase7/capture_runner.py    ros2-container-side (needs
                                         rosbag2_py via mcap_writer.py):
                                           runscoped        = baseline A,
                                             unmodified run_capture()
                                           runscoped-batch  = A applied to
                                             many robot_run_ids in one
                                             process (avoids per-run
                                             container-startup overhead
                                             skewing §5's measurement)
                                           partitionaware   = prototype B,
                                             assign()-based, same
                                             writer/validate/finalize
                                             pipeline reused unmodified
                                           subscribed-sync  = isolation
                                             probe: subscribe()-based
                                             (architecturally = A) but
                                             polled synchronously (no
                                             asyncio.to_thread) -- added
                                             specifically to separate
                                             "group-protocol cost" from
                                             "async-wrapper cost" once
                                             §4.1's finding emerged
scripts/dev/phase7/router_prototype.py  host-side, Continuous Capture
                                         Router prototype (C): one
                                         subscribe()-based pass from
                                         earliest to the topic's
                                         watermark-at-start, routes by
                                         robot_run_id into in-memory
                                         counters only -- no MCAP writing,
                                         no canonical state, per the
                                         brief's explicit scope for C
scripts/dev/phase7/run_rescan_benchmark.sh  orchestrates §4's checkpoints
```

---

## 3. Benchmark workload / environment

- Payload: 64 bytes (matching Phase 6.6's own convention, for direct
  comparability of per-message overhead).
- Noise messages: spread round-robin across many distinct synthetic
  `robot_run_id`s (never the target run's own id), so they exercise
  realistic multi-run topic interleaving without affecting the target
  run's own sequence numbering or Kafka partition-invariant checks.
- Target run size: 3,000 messages (matching Phase 6.6's own 3,000-message
  checkpoint) for §4; 2,000 messages × 10 runs for §5.
- All producing done from the host (`uv run python`, `localhost:9092`,
  no `rclpy`/ROS2 needed for synthetic envelopes); all capturing
  (`runscoped`/`runscoped-batch`/`partitionaware`/`subscribed-sync`)
  done inside the real `ros2` container (needs `rosbag2_py`), exactly
  like every existing Phase 6 E2E/benchmark target.

---

## 4. Rescan benchmark: current baseline (A) vs topic history

`scripts/dev/phase7/run_rescan_benchmark.sh`, checkpoints 10k / 100k /
500k / 1M end-offset, each measuring a fresh 3,000-message target run
placed at that checkpoint. `runscoped` = production `run_capture()`,
completely unmodified. `partitionaware_p1` = prototype B run against
the *same* 1-partition topic (so it isolates non-partition-count
effects only — the true multi-partition case is §6).

| History before target | `runscoped` (A) duration | A throughput | `partitionaware` (B, P=1) duration | B throughput | Kafka lag after A |
|---:|---:|---:|---:|---:|---:|
| 10,000 | 4.56s | 658 msg/s | 0.70s | 4,285 msg/s | 0 |
| 100,000 | 15.19s | 198 msg/s | 1.48s | 2,033 msg/s | 0 |
| 500,000 | 56.75s | 53 msg/s | 5.67s | 529 msg/s | 0 |
| 1,000,000 | 102.51s | 29 msg/s | 10.54s | 285 msg/s | 0 |

(`msg/s` columns are `target_count / duration`, matching Phase 6.6's
own convention — not the raw scan rate. Raw scan rate, i.e.
`(history + target) / duration`, is what §4.1 uses instead, since that
is the number that is actually comparable across A/B/C.)

This reproduces and extends the Phase 6.6.1 addendum's finding (3.47s
@ light history → 43.9s @ ~150k) at controlled checkpoints up to 1M:
rescan cost grows ~linearly with topic history, exactly as the existing
"scans the entire topic under `auto.offset.reset=earliest`" mental
model predicts. Kafka lag reached exactly 0 after every capture —
correctness held throughout, matching Phase 6.6's own finding; this
study never observed a sequence/partition-invariant violation.

### 4.1 Finding: most of A's cost is `asyncio.to_thread`-per-`poll()`, not the group protocol

The size of the A vs B gap (a ~10x factor, not the ~1x a single-partition
topic should produce if partitioning were the only variable) prompted
one more isolation probe: `subscribed-sync` — identical to A
architecturally (real `subscribe()`, real run-scoped consumer group,
real group-join/rebalance protocol) but with `consumer.poll()` called
synchronously instead of through
`KafkaTelemetryConsumer.poll()`'s `asyncio.to_thread(...)` wrapper.
Measured once at ~1,009,000 history (same target size):

| Variant | Group protocol? | Async-wrapped poll? | Partitions scanned | Duration | Raw scan rate |
|---|---|---|---|---:|---:|
| `runscoped` (A, production, unmodified) | yes (fresh group/run) | **yes** | 1 (topic's only partition) | 102.51s | ~9,815 msg/s |
| `subscribed-sync` (isolation probe) | yes (fresh group/run) | **no** | 1 | 13.66s | ~73,900 msg/s |
| `partitionaware` (B) | no (`assign()`, no rebalance) | no | 1 | 10.54s | ~95,455 msg/s |

**Removing only the `asyncio.to_thread` wrapper (keeping everything
else about A identical — same `subscribe()`, same fresh run-scoped
group, same rebalance/join protocol) recovers ~87% of the gap to B**
(102.51s → 13.66s, a ~7.5x speedup, vs B's 10.54s). The remaining
~3.1s gap between `subscribed-sync` and B is the actual
group-join/rebalance-protocol cost — real, but a minority contributor
at this scale.

**Implication:** the headline "run-scoped capture rescans the whole
topic and that's slow" framing is correct directionally, but the
*current* absolute cost is inflated roughly 7-8x by an orthogonal
implementation detail in `KafkaTelemetryConsumer.poll()` — calling
`asyncio.to_thread` once per Kafka `poll()` call in a hot loop that
may execute once per *skipped* (non-matching) message, not just once
per accepted one. This is NOT a correctness issue and NOT specific to
run-scoped capture (a naively-built Continuous Capture Router reusing
the same wrapper for its own hot poll loop would inherit the identical
problem, at higher absolute message volume). It is, however, a much
cheaper fix than either prototype A/B/C's full production
implementation, and should be evaluated (and likely fixed) before or
alongside whichever Phase 7.1 direction is chosen — see §12/§13.

---

## 5. Multi-run workload: amplification

10 interleaved `RobotRun`s (`phase7-mr-0000`..`phase7-mr-0009`), 2,000
messages each (20,000 total), produced on top of the §4 benchmark's
accumulated ~1,012,000-message history (so this measures the realistic
case: many independent captures against an already-busy topic, not a
freshly-emptied one). All 10 runs landed on the topic's one partition
(expected — 1 partition today).

**Sequential run-scoped capture of all 10 runs** (`runscoped-batch`,
production `run_capture()` unmodified, one call per run, all inside a
single container invocation to remove container-startup noise from the
comparison):

| RobotRun | Messages | Duration |
|---|---:|---:|
| `phase7-mr-0000` | 2,000 | 126.51s |
| `phase7-mr-0001` | 2,000 | 120.04s |
| `phase7-mr-0002` | 2,000 | 118.17s |
| `phase7-mr-0003` | 2,000 | 110.22s |
| `phase7-mr-0004` | 2,000 | 109.71s |
| `phase7-mr-0005` | 2,000 | 112.95s |
| `phase7-mr-0006` | 2,000 | 106.65s |
| `phase7-mr-0007` | 2,000 | 105.87s |
| `phase7-mr-0008` | 2,000 | 115.80s |
| `phase7-mr-0009` | 2,000 | 118.99s |
| **Total (10 runs, sequential)** | **20,000** | **1,144.91s** (peak RSS 163MB) |

Durations cluster tightly (106-127s, ~10% spread, no strong ordering
effect by run index) — consistent with every run independently
rescanning very close to the same ~1,012,000-1,032,000-message history,
exactly as §4's single-target-run model predicts, since all 10 runs'
messages were interleaved into the same thin band at the tail of an
already-large topic.

**Continuous single-pass baseline** (`router_prototype.py`, one
`subscribe()`-based pass from earliest to the topic's watermark
captured at start, routing into per-run in-memory counters, no MCAP
writing — matching this study's brief for prototype C), run against
the topic in its exact final state (same data the 10 sequential
captures above just finished scanning individually):

| Topic messages | Distinct `robot_run_id`s found | Duration | Throughput |
|---:|---:|---:|---:|
| 1,032,000 | 234 | 11.695s | 88,241 msg/s |

All 10 target runs' exact 2,000-message counts were recovered correctly
in this single pass, alongside every other run already on the topic —
confirmed via `--dump-runs-file` against each producer call's own
reported count.

**Amplification factor:**

```text
wall-clock:  1,144.91s / 11.695s  ≈ 97.9x
```

The message-VOLUME amplification (how much Kafka data is repeatedly
scanned) is smaller than the wall-clock figure: each of the 10 runs
individually scans on the order of the full ~1,012,000-1,032,000-message
topic to reach its own interleaved messages, so total scanned volume ≈
10 × ~1,022,000 ≈ 10.22M message-scans, against 1,032,000 for the
single pass — a ≈9.9x volume amplification, i.e. **≈ R** (10 runs),
matching §8's theoretical model directly. The remaining gap between the
9.9x volume figure and the 97.9x wall-clock figure (≈9.9x on top of
the volume factor) is exactly §4.1's finding compounding in: the
run-scoped path's async-wrapped per-message rate is itself ~9x slower
than the synchronous single-pass path's. **The two effects multiply,
not add**: `R × (async-wrapper rate penalty) ≈ 10 × 9.9 ≈ 98`, matching
the measured 97.9x almost exactly.

---

## 6. Partition study

Isolated, study-only topic `sceneops.robot.telemetry.phase7study.4p.v1`
(4 partitions, `key=robot_run_id`, dropped after this study — the
default/production topic's partition count was never changed).

**Distribution across runs.** 300 distinct synthetic `robot_run_id`s,
200,000 noise messages (round-robin, ~667 msgs/run) plus one 3,000-msg
target run:

```text
runs per partition:      {0: 75, 1: 75, 2: 75, 3: 75}   -- exactly even
messages per partition:  {0: 50000, 1: 50000, 2: 50000, 3: 50000}
```

Confirms both required properties directly: (1) **same-run
partition-locality** — every `robot_run_id`'s messages landed on
exactly one partition (guaranteed by keyed partitioning under a fixed
partition count, not just assumed); (2) **even distribution across
runs** — Kafka's default key-hash partitioner spread 300 distinct run
ids across 4 partitions with zero measured skew at this sample size.

**Target run:** landed on partition 1 (offset 50,000 — i.e. behind
that partition's full 50,000-message share of the noise history).

| Variant | Partitions scanned | Group protocol? | Async-wrapped poll? | Duration |
|---|---|---|---|---:|
| `runscoped` (A, production, unmodified — `subscribe()`, sole group member gets **all 4** partitions) | 4 (200,000 msgs) | yes | yes | 32.11s |
| `subscribed-sync` (isolation probe — still all 4 partitions) | 4 (200,000 msgs) | yes | no | 5.81s |
| `partitionaware` (B — `assign()` directly to partition 1 only) | 1 (50,000 msgs) | no | no | 1.10s |

Decomposed: async-wrapper removal alone ≈ 26.3s of the 31.0s gap
(85%); partition-narrowing (4→1) on top of that ≈ a further 4.7s
(1/4 of the data, roughly matching the ~4x reduction 5.81s→1.10s
predicts, i.e. ≈1.45s expected vs 1.10s observed — consistent given
per-partition message counts weren't perfectly identical to the
single-partition §4 baseline).

**Does partition-aware capture meaningfully improve rescan cost?**
Yes, proportionally to `1/P` on top of whatever the async-wrapper fix
already buys — real, measured, and independent of the §4.1 finding
(both effects stack: 32.11s → 5.81s → 1.10s). It does **not** change
the lifecycle model: a partition-aware capture is still a bounded,
one-shot, run-scoped consumer that starts, scans, and exits — it does
not eliminate the fundamental "one capture per run, each paying its
own partition's history" cost the Continuous Router (§7) eliminates
entirely. It also introduces a new dependency the current design
doesn't have: something must tell a capture attempt *which* partition
its target `robot_run_id` is on before it starts (§9 — today, nothing
records this; a production partition-aware capture would need to learn
it from the producer's own delivery report at write time, or
recompute Kafka's partitioner function independently and keep both in
sync with the topic's actual partition count).

---

## 7. Continuous Capture Router prototype (C)

`router_prototype.py`, run once against the default topic at
~1,012,000 messages (§4's final checkpoint's own accumulated state),
and once against the isolated 4-partition topic at 200,000 messages
(§6):

| Topic | Messages | Distinct `robot_run_id`s | Duration | Throughput |
|---|---:|---:|---:|---:|
| `sceneops.robot.telemetry.v1` (1 partition) | 1,012,000 | 224 | 11.54s | 87,672 msg/s |
| `sceneops.robot.telemetry.phase7study.4p.v1` (4 partitions) | 200,000 | 300 | 4.81s | 41,599 msg/s |

One pass correctly recovered every distinct `robot_run_id` present on
the topic and its exact per-run message count in both cases (spot-
checked against each producer call's own reported counts — exact
match, e.g. every `phase7-p4-noise-*` run showed 666 or 667 as
expected from `200000 // 300`/`200000 % 300`).

This prototype does not write MCAP or touch canonical state (by
design, per the study brief) — it establishes the *consumption-side*
lower bound: one topic pass, regardless of how many independent runs
are interleaved on it, costs what one pass costs. §5's amplification
figure is this number against §5's sequential-run-scoped total.

---

## 8. Scan amplification, quantified

Using §5's actual measured values (10 runs, ~1,012,000-1,032,000-message
history):

```text
N (topic messages, single pass)        ≈ 1,032,000
R (independently run-scoped-captured RobotRuns)  = 10

run-scoped (A, current production, unmodified):
  ≈ R × N message-scans               ≈ 10.22M message-scans (measured
                                          proxy: 1,144.91s total wall time)
  ≈ R × (per-message rate penalty)    additional wall-clock cost from
                                          §4.1's async-wrapper finding
                                          (~9x slower per message than a
                                          synchronous single pass)

continuous (C, single pass, this study's router prototype):
  ≈ N message-scans                   1,032,000 (measured: 11.695s,
                                          88,241 msg/s)

measured amplification:
  message volume  ≈ 9.9x  (≈ R, as the model predicts)
  wall-clock       ≈ 97.9x (R × per-message-rate-penalty, compounding
                             multiplicatively per §5's own breakdown)
```

Using §4's per-checkpoint data as a general model (target run size
small relative to history, single partition): for `R` independently
run-scoped-captured `RobotRun`s against a topic that has already
accumulated `N` unrelated messages by the time each capture starts,
total run-scoped work is approximately `R × N` message-scans (each
capture independently re-scans very close to the same `N`), vs `N`
message-scans for one continuous pass that captures all `R` runs in
the same traversal — an `Nx` reduction in scanned volume that grows
directly with `R`, not with `N`. The wall-clock amplification is
larger still, because (§4.1) the current run-scoped path's raw
per-message rate (~9,800 msg/s, async-wrapped) is itself ~9x slower
than a synchronous single pass (~87,000 msg/s) — the two effects
compound multiplicatively, not additively.

---

## 9. `CaptureSession` recommendation

**Yes — but as execution/runtime state, explicitly separate from the
canonical `RobotRun` domain record, not a new canonical entity.**

Reasoning:

- `CaptureResult` (`ros2/capture/capture_consumer.py`) already carries
  every field a `CaptureSession` needs (`robot_id`, `robot_run_id`,
  local `path`, `message_count`, `partition`, `first_offset`/
  `last_offset`, `first_sequence`/`last_sequence`, `sha256`) — it is
  currently produced once, at the very end of a single-shot capture,
  and immediately discarded (never persisted, never resumable). A
  Continuous Router needs the SAME information kept *live*, per
  actively-open run, across an unbounded number of concurrent runs in
  one long-lived process — a materially different lifetime and
  cardinality than today's "one `CaptureResult`, one function call,
  one return."
- The canonical `RobotRun` (`RobotRunRecord`, `sceneops-core`) is
  deliberately created only once a capture is already finalized
  (`register_robot_run_capture()`, current architecture, §1) — it has
  no field for "which Kafka partition," "how many messages captured so
  far," or "which local writer instance owns this run's file," because
  none of that is meaningful once a run is done. Overloading it with
  in-progress execution state would either (a) require a schema change
  to a frozen canonical contract mid-capture, violating §11's frozen-
  contracts list, or (b) force every in-progress-capture read/write to
  go through Postgres, adding a DB round-trip to a per-message hot
  path that today has none.
- `RobotRunStatus.RECORDING` already exists in the enum (§1) but is
  unused by any code path — this is a preexisting, if latent,
  acknowledgment that "in progress" is a legitimate RobotRun-adjacent
  state; a `CaptureSession` would be the runtime-side counterpart that
  actually drives that status transition, without living inside the
  same row/table.

Proposed shape (not implemented here, a recommendation for Phase 7.2):
an in-process (or Redis-backed, if the router needs to survive its own
restart independent of Postgres) map keyed by `robot_run_id`, holding
exactly `CaptureResult`'s fields plus writer ownership and open/closed
state — owned entirely by the Continuous Capture Router process,
reconciled into canonical `RobotRun`/`ArtifactRecord` state only at
finalization, through the existing, unchanged
`register_robot_run_capture()` boundary.

---

## 10. Run lifecycle recommendation

**No universal signal exists today** — confirmed by audit (§1):
`/mission/status` is an application-level, scene-specific channel (its
own `source_timestamp_ns` is explicitly documented as a *synthetic
replay-boundary* time, not a lifecycle primitive — `streaming-
transport.md` §11.1), and nothing else in the current transport
carries a `robot_run_id`-scoped start/end signal at all. `run_capture()`
today is told when to stop by an external, out-of-band
`stop_condition` callable (`--max-messages` or `--idle-timeout-seconds`
in `cli.py`) supplied by whatever orchestrates the capture invocation
— there is no in-band Kafka signal today, by design (§1, `capture_
consumer.py`'s own docstring: "Lifecycle is externally controlled").

Candidates evaluated:

| Mechanism | Verdict |
|---|---|
| `/mission/status` as universal signal | **Rejected** — explicitly scene/application-specific and already documented as non-authoritative for timing (§1); would also couple a transport-level lifecycle concern to one particular channel's payload semantics, which §29 of `streaming-transport.md` already lists as a non-goal ("Interpretation of `/mission/status` boundaries beyond proving transport preservation ... is a downstream consumer's concern"). |
| Explicit run/session control API (a `POST /robot-runs/{id}/start`-style signal, separate from telemetry) | **Plausible, most correct** — matches how `RobotRunStatus.RECORDING`→`COMPLETED` should actually be driven; makes "run started"/"run ended" an explicit, auditable canonical-adjacent event rather than inferred. Cost: a new API surface + whatever produces it (robot-side agent, bridge, or an operator tool) must be built and kept in sync with reality. |
| Transport control event (a reserved Kafka message/topic carrying start/end, keyed the same as telemetry) | **Plausible, lowest new-infrastructure cost** — reuses the existing Kafka transport instead of adding a new API; a router already consuming the topic sees it inline, in order, with no extra round-trip. Cost: extends the frozen `TelemetryEnvelope`/topic contract (§11 lists this transport as frozen) — would need to be an explicitly NEW, additive control-channel convention, not a mutation of the existing envelope, to avoid violating that freeze. |
| Producer/bridge lifecycle signal (the ROS2 bridge process's own start/`SIGTERM`-triggered graceful shutdown, §14 of `streaming-transport.md`, observed indirectly via Kafka) | **Incomplete alone** — the bridge's own process lifetime is a reasonable proxy for "this run's live telemetry has stopped arriving," but a router serving many concurrent runs from many independent producer processes has no single process boundary to watch; would need per-`robot_run_id` bridge-exit visibility, which doesn't exist today either. |
| Timeout/inactivity policy (no explicit signal; a run is "ended" once idle past a threshold) | **Necessary as a fallback regardless of which primary mechanism is chosen** — `cli.py` already supports exactly this (`--idle-timeout-seconds`) for the single-shot case; a router needs the same policy per open `CaptureSession` so a crashed/never-terminated producer doesn't hold a writer open forever. Not sufficient alone — it cannot distinguish "run legitimately paused" from "run over," and picking a bad threshold either finalizes too eagerly (splitting one run into two files) or never finalizes a genuinely-done run. |

**Recommendation for Phase 7.1/7.2:** an explicit, additive **transport
control event** (new, separate message convention on the existing
telemetry topic or a small dedicated control topic — decided in 7.2,
not here), as the primary signal, with the existing **idle-timeout
policy already proven in `cli.py`** kept as the mandatory fallback for
every `CaptureSession` regardless. Do not build the explicit
run/session control API as the *primary* mechanism first — it adds a
second system (HTTP+DB) into the hot start/stop path for something the
transport layer can carry inline at effectively no extra cost, and
nothing about Kafka delivery ordering makes an HTTP call more timely
than an in-band message. An API-driven control surface remains a
reasonable *operator-facing* layer on top (e.g. to force-finalize a
stuck session), just not the thing the router itself blocks on.

---

## 11. Preserved Phase 6 contracts

Confirmed unmodified throughout this study — every prototype
(`capture_runner.py`, `producer.py`, `router_prototype.py`) only
*imports* these, never edits them:

```text
TelemetryEnvelope                     unchanged
robot_run_id Kafka key                unchanged (relied upon directly
                                       by both prototypes B and C)
ROS2 CDR payload                      unchanged (not exercised by this
                                       study's synthetic envelopes, which
                                       reuse the same encoding field but
                                       carry benchmark filler bytes --
                                       no CDR decode was attempted or
                                       needed for counting/routing)
timestamp semantics                   unchanged
MCAP time mapping                     unchanged (prototype B reuses
                                       McapCaptureWriter unmodified)
durable finalization                  unchanged (prototype B reuses
                                       finalize.py unmodified)
ArtifactStore registration            untouched (no registration call
                                       made anywhere in this study)
RobotRun canonical model               untouched (no RobotRun row
                                       created anywhere in this study)
Episode/Learning pipeline              untouched, not exercised
```

`ros2/capture/*.py`, `packages/sceneops-streaming/*`,
`packages/sceneops-core/sceneops_core/streaming/*`: zero diffs.

---

## 12. Architecture decision (A vs B vs C)

| Criterion | A — current (run-scoped) | B — partition-aware run-scoped | C — Continuous Capture Router |
|---|---|---|---|
| Correctness | Proven (Phase 6.6/6.7) | Proven here at small scale (§6) — same duplicate/gap/sequence logic reused unmodified | Not proven at production depth here (prototype only counts) — inherits the same reusable `_SequenceTracker`/`_RunFilter`/writer logic if built properly, but the "one process serving N concurrent open captures" concern is new and untested |
| Historical-rescan cost | Full topic history, every capture (§4) | `1/P` of topic history, every capture (§6) — real but partial fix | Effectively eliminated for any run whose window falls after the router started (one pass serves all runs, §7/§8) |
| Kafka offset semantics | One committed offset per run-scoped group; simple, already correct | Same per-partition; simple, already correct | Needs its own offset-management story per partition (a single long-lived group across all active + future runs) — not designed here, flagged for 7.1 |
| Multi-run concurrency | Correct but linearly expensive (§5/§8) — N independent full scans | Same concurrency model as A, cheaper per-scan | Native — one consumer already serves arbitrarily many concurrent runs in one pass (§7) |
| Restart/recovery complexity | Simple — a crashed capture's `.partial` is safely discardable and Kafka is the source of truth (§21 of `streaming-transport.md`, unchanged) | Same simplicity as A | New — needs `CaptureSession` state (§9) to know which runs were open and where each had gotten to; not solved here |
| Partition scalability | Works, but gets no benefit from more partitions (subscribes to all of them regardless, §6) | Directly benefits from more partitions (`1/P` scaling) | Also benefits from more partitions (parallel workers, one per partition or a shared group) — but that's a further, separate scaling axis (7.3), not exercised in this study |
| MCAP writer lifecycle | One writer, one process, one run, short-lived — trivial | Same as A | One process must own potentially many concurrent open writers — new lifecycle management, not built here |
| Resource usage | Peak RSS ~150-157MB at 1M-history captures observed here (§4/§6, consistent with Phase 6.6's own finding that RSS is dominated by scale, not by group strategy) | Comparable RSS to A at equal message volume (§6: 93-155MB observed) | Not measured at comparable MCAP-writing load here (prototype does no MCAP writing) — flagged as an open question for 7.1's real implementation, since N concurrent open `rosbag2_py` writers is a different resource profile than N sequential ones |
| Operational simplicity | Simplest today — no new infra, no new lifecycle concept | Small addition (needs partition lookup, §6) | Largest change — a new long-lived service, a new `CaptureSession` concept (§9), a new run-lifecycle signal (§10) |
| Compatibility with Phase 6 contracts | Full (it IS the Phase 6 contract) | Full (§11 — reuses every frozen module unmodified) | Full in this prototype (§11) — the real 7.1 implementation would additionally need to preserve them under concurrent multi-writer conditions, not yet proven |

**Decision:**

```text
Production:
  Continuous Capture Router (C) -- for live, ongoing multi-run ingestion

Recovery / replay / backfill:
  existing RunScopedCapture (A), unmodified
  -- optionally upgraded with partition-aware assign() (B) once the
     topic actually has >1 partition, since B is a strict improvement
     over A with no architectural downside for this exact use case
```

This matches the brief's suggested conclusion shape, and the
measurements here support it: C is the only one of the three that
removes the `R×` amplification (§8) rather than merely shrinking its
constant (`1/P`, B) or leaving it unaddressed (A). B is not a
competitor to C — it is a strict improvement to A's own mechanics
(§6's numbers show no downside), and A itself remains exactly the
right tool for replay/backfill/debugging/recovery: a bounded, one-shot,
externally-controlled scan of a known window is precisely what those
use cases need, and precisely what C's continuous, unbounded, always-
running model is not shaped for.

**One qualification the brief's "prefer a conclusion of this form ...
only if measurements support it" clause is directly relevant to:** a
meaningful fraction of what makes A look architecturally worse than it
is turns out to be the `asyncio.to_thread`-per-`poll()` cost (§4.1),
not the run-scoped-group design itself. That finding does not change
the recommendation (C still wins on the amplification axis no
constant-factor fix to A touches — §8's `R×` scaling is structural, not
a fixed cost), but it does mean the *urgency* is lower than the raw
102s-at-1M-messages numbers alone would suggest, and it surfaces a
near-free win (§13, 7.0.1) worth taking either way.

---

## 13. Proposed Phase 7 roadmap

```text
7.0.1  Poll-loop implementation fix -- DONE (§18)
       Stop wrapping every single ros2/capture Kafka poll() call in
       asyncio.to_thread -- §4.1 measured this as ~85-90% of A's
       apparent rescan cost at 1M-message history, entirely
       independent of the run-scoped-vs-continuous or single-vs-multi-
       partition questions. Cheap, low-risk (no protocol/contract
       change), and benefits A, B, AND any future C implementation
       equally, since C will need its own hot poll loop regardless of
       which of A/B/C's consumption strategy it's built on.
       Implemented same-day (§18): batched confluent_kafka.consume()
       + bounded internal buffer, poll_batch_size=64 (benchmarked),
       zero external API change. Measured 2.8x-7.0x speedup at
       100k/500k/1M history against the real production run_capture()
       path; remaining cost now correctly attributed to consumer-group
       join/rebalance protocol, not async-dispatch overhead.

7.1    Continuous Capture Router
       Build the real router: single long-lived consumer (or a small,
       fixed pool -- see 7.3), routes by robot_run_id to per-run
       McapCaptureWriter instances, reuses _RunFilter/_SequenceTracker/
       validate/finalize unmodified (this study's prototype already
       proves the routing/consumption side works; 7.1 adds the real
       per-run MCAP writer lifecycle prototype C deliberately skipped).

7.2    Capture Session Lifecycle
       CaptureSession as execution/runtime state (§9) -- open/closed
       state, writer ownership, per-run progress -- reconciled into
       canonical RobotRun/ArtifactRecord only at finalization through
       the existing, unchanged register_robot_run_capture() boundary.
       Also where the run start/end signal (§10 -- recommended:
       additive transport control event, idle-timeout as mandatory
       fallback) actually gets designed and wired in.

7.3    Multi-Partition / Multi-Worker Scaling
       Grow the production topic beyond 1 partition (this study's §6
       used an isolated, disposable topic specifically so as not to
       touch the production default yet); decide the partition-to-
       worker assignment model for the Router once there's more than
       one of it.

7.4    Restart & Recovery
       What a Router restart does to in-flight CaptureSessions (§9) --
       this study did not attempt to answer this, it only established
       that CaptureSession needs to exist.

7.5    High-Bandwidth Sensor Strategy
       Unchanged from the original Phase 7 outline -- the ~999KB
       message-size ceiling (Phase 6.6 §7) is untouched by anything in
       this study.

7.6    Multi-Run Scale Benchmark
       Re-run this study's §5/§8 methodology (now with 7.0.1's fix and
       7.1's real Router in place) at the 50-100 concurrent RobotRun
       scale this study's brief suggested but which was not reached
       here (§14 -- deferred, not because it's infeasible, but to keep
       this study's own runtime practical; nothing here suggests the
       50-100 scale would change the qualitative A/B/C conclusion).

7.7    Clean-Room Acceptance
       Unchanged in spirit from Phase 6.7 -- full chain, fresh reset,
       real infra, real source data, once 7.1-7.4 land.
```

This keeps the original outline's shape but inserts **7.0.1** ahead of
everything else, since it is unusually cheap, structurally independent
of every later decision, and directly explains a large fraction of
this study's own headline numbers.

---

## 14. Known limits / deferred from this study

```text
Only reached 10 concurrent RobotRuns for §5/§8 (not the 50-100 the
  brief suggested "if practical") -- kept to 10 to keep this study's
  own wall-clock time practical against a shared local dev stack;
  nothing measured here suggests the qualitative conclusion would
  change at 50-100, only that the absolute amplification factor would
  be larger
Partition study (§6) used a disposable, isolated 4-partition topic,
  dropped after use -- the production default topic's partition count
  is untouched, so no real multi-partition production behavior
  (rebalancing under a growing/shrinking partition count, repartition-
  remaps-future-keys per streaming-transport.md §6) was exercised
Prototype C did no MCAP writing -- the real per-run writer-lifecycle
  resource profile under N concurrent open writers is unmeasured
  (flagged explicitly in §12's comparison table and as 7.1's job)
This entire study is single-broker, single-machine, local Docker
  Compose -- same caveat every prior streaming baseline in this repo
  already carries forward
The topic-history reset noted in §0 means this study's own checkpoints
  are the only history that matters to its conclusions, but it does
  mean no continuity claim is made against the exact prior Phase 6.6.1
  numbers beyond "same order of magnitude, same trend"
```

---

## 15. Files changed

```text
New (this study only, no production code touched):
  docs/history/streaming-multirun-phase7-study.md   (this file)
  scripts/dev/phase7/producer.py
  scripts/dev/phase7/capture_runner.py
  scripts/dev/phase7/router_prototype.py
  scripts/dev/phase7/run_rescan_benchmark.sh

Modified: none
Deleted: none
```

---

## 16. Regression results

| Target | Result |
|---|---|
| `make lint` | PASS |
| `make test` | PASS — 1,419 passed, 14 skipped, 0 failed (9.15s) — identical counts to Phase 6.7's own baseline |
| `make test-integration` | PASS — 65 passed, 0 failed (5.13s) — identical count to Phase 6.7's own baseline |
| `make canonical-verify` | PASS — `sceneops-canonical/v0.0` unchanged (scene_count=10, episode_count=10), cross-dataset identity isolation still holding |

No streaming/capture E2E targets (`make smoke-streaming`,
`make e2e-ros2-streaming`, `make e2e-streaming-capture`,
`make e2e-robot-run-registration`, `make e2e-robot-run-learning`) were
re-run — production code is untouched (§15: zero diffs to any existing
file, only new, additive `scripts/dev/phase7/*` tooling and this
document), so nothing in this study is capable of having regressed
those paths, and `test`/`test-integration` already re-run every unit
and integration test that DOES cover `ros2/capture/`,
`sceneops-streaming`, and `sceneops-core/streaming` unchanged. Per the
task brief's own instruction ("If production code remains untouched,
do not rerun every expensive Phase 6 E2E unnecessarily"), this is
sufficient.

---

## 17. Blockers before Phase 7.1

```text
None structural. 7.0.1 (poll-loop fix) and 7.1 (Router) can both start
immediately -- neither depends on an unresolved question from this
study. The two open design questions that DO need resolving before 7.2
specifically (not 7.1): the exact shape of the run start/end control
event's wire format (§10 recommends the mechanism, not the schema),
and whether CaptureSession state (§9) lives in-process or in Redis --
both are 7.2-scoped decisions, not blockers to starting 7.1's router
consumption/routing/writer-reuse work.
```

---

## 18. Phase 7.0.1 addendum — poll-loop fix (implemented)

**Date:** 2026-09-30 (same day, direct follow-up). **Commit at start:**
same `531f743` (§0's HEAD was never advanced by §1-17; this addendum's
own diff is the first production-code change in this document's
history). No git commit was made here either, per instruction.

### 18.1 Exact pre-fix bottleneck

`KafkaTelemetryConsumer.poll()` (`packages/sceneops-streaming/
sceneops_streaming/consumer.py`) called
`await asyncio.to_thread(self._consumer.poll, timeout_seconds)` --
one broker-facing blocking call, and one `asyncio.to_thread` executor
dispatch, PER MESSAGE, including every message a run-scoped capture's
`_RunFilter` immediately discards as not belonging to its target
`robot_run_id`. At ~1M-message topic history, §4.1 isolated this as
~85-90% of the run-scoped capture path's apparent rescan cost --
confirmed here end-to-end against the real production `run_capture()`
path (§18.4), not just the isolation probe.

### 18.2 Chosen batching/buffering design

`poll()`'s external contract is byte-for-byte unchanged (still
`async def poll(self, timeout_seconds: float = 1.0) -> ConsumedTelemetryEnvelope | None`,
same class, same call sites, zero caller changes anywhere in the
repo). Internally:

```text
poll(timeout_seconds):
  if internal buffer non-empty:
    pop and decode/return the next buffered record -- synchronous,
    no broker call, no thread dispatch, timeout_seconds unused
  else:
    ONE asyncio.to_thread(self._consumer.consume,
                           num_messages=poll_batch_size,
                           timeout=timeout_seconds)
    -- one bounded blocking C call for up to poll_batch_size records
    if the batch is empty (timeout elapsed, nothing available):
      return None                          -- same as before
    else:
      buffer the batch, pop and decode/return the first record
```

`confluent_kafka.Consumer.consume(num_messages, timeout)` (audited
directly: `help(Consumer.consume)`, confirmed present in the installed
`confluent-kafka==2.15.1`) was confirmed suitable before use: it
returns a plain `list[Message]` (possibly empty on timeout), each
individually checkable via `.error()` exactly like a single-record
`poll()` result, blocks for at most `timeout` regardless of
`num_messages` (never multiplies the wait), and participates in the
same background rebalance/heartbeat callback handling `poll()` always
did ("Callbacks may be executed as a side effect of calling this
method" -- same note in both methods' docstrings). It works identically
under `subscribe()` (baseline A/production) and `assign()` (prototype
B) -- confirmed by construction, not just documentation, since §18.4
re-ran both through the real `ros2/capture` pipeline.

Decode/error-checking happens lazily, one message per `poll()` call,
in strict buffer order -- not eagerly for the whole batch at fetch
time. This preserves two things exactly: (1) `_last_message` (what
`commit()` acknowledges) always reflects the single most recently
RETURNED-to-caller record, whether served fresh or from the buffer,
so `commit()`'s semantics are untouched; (2) a decode/Kafka error for
message K of a batch is raised on the K-th `poll()` call that reaches
it, never earlier and never for a message the caller hasn't asked for
yet -- identical to the pre-fix one-call-one-message behavior, just
sourced from a local buffer instead of a fresh broker round trip.

### 18.3 Selected batch size and evidence

`poll_batch_size` is a constructor keyword (`KafkaTelemetryConsumer.
__init__`), defaulting to a module constant `DEFAULT_POLL_BATCH_SIZE`
-- **not** an environment variable (no demonstrated per-deployment
override need, matching this package's existing policy for `acks`/
`retries`/`linger.ms`/etc.).

`scripts/dev/phase7/poll_batch_size_benchmark.py` (new, host-side,
exercises the real `KafkaTelemetryConsumer` class directly against
real Kafka, no MCAP writing needed to isolate consumer throughput)
swept candidates against the already-populated ~1,032,000-message
default topic, repeated for stability:

| `poll_batch_size` | Run 1 | Run 2 | Run 3 |
|---:|---:|---:|---:|
| 8 | 56,496 msg/s | -- | -- |
| 16 | 69,013 msg/s | -- | -- |
| 32 | 75,213 / 74,883 / 74,266 / 74,817 msg/s | | |
| 64 | 78,177 / 79,062 / 78,155 msg/s | | |
| 128 | 69,186 / 68,837 msg/s | | |
| 256 | 68,572 msg/s | -- | -- |
| 512 | 70,905 / 71,254 msg/s | | |

Throughput rises sharply from 8→32 (amortizing the per-fetch dispatch
cost that motivated this fix at all), peaks reproducibly at **64**
(~78-79k msg/s across 3 repeated runs, each ~5-6% ahead of every other
candidate tested), then plateaus/mildly degrades from 128-512 (more
buffered state per fetch, no further throughput benefit at this
message/payload size). **`DEFAULT_POLL_BATCH_SIZE = 64`** — the
empirically fastest candidate, and on the small/bounded side (keeps
the worst-case buffered-but-uncommitted replay window modest, §18.5).

### 18.4 Real-Kafka before/after benchmark

Same methodology as §4 (`scripts/dev/phase7/run_rescan_benchmark.sh`,
now parametrized with a `PHASE7_BENCH_TOPIC` override so this
comparison could run against a fresh, isolated, disposable topic --
`sceneops.robot.telemetry.phase701study.v1`, 1 partition, dropped
after use — rather than resetting the shared default topic). The
`ros2` Docker image was rebuilt first (`docker compose build ros2`) --
required, since `packages/sceneops-streaming` is `COPY`+`pip install`-ed
into that image at build time, not live-mounted (the same staleness
class of issue Phase 6.7 §11 already flagged once for this exact
image). Real production `run_capture()` path, completely unmodified,
now running on top of the fixed `KafkaTelemetryConsumer`:

| History before target | Before (§4, async-wrapped) | After (batched, this fix) | Speedup | Kafka lag after |
|---:|---:|---:|---:|---:|
| 100,000 | 15.19s | 5.44s | 2.8x | 0 |
| 500,000 | 56.75s | 9.82s | 5.8x | 0 |
| 1,000,000 | 102.51s | 14.56s | 7.0x | 0 |

Correctness held throughout: Kafka lag reached exactly 0 after every
capture (no message lost or left uncommitted), and the after-fix 1M
number (14.56s) lands almost exactly where §4.1's `subscribed-sync`
isolation probe predicted (13.66s) -- strong independent confirmation
that this fix closes the gap §4.1 attributed to it, not some other
variable. The remaining ~14.6s at 1M history (vs. prototype B's 10.73s
on the same checkpoint, re-measured here too) is the genuine,
un-removed consumer-group-join/rebalance-protocol cost §4.1 already
identified as the minority remaining contributor -- exactly the
"remaining history-dependent slowdown... attributed to run-scoped
rescanning rather than the async wrapper" the acceptance criterion
asks for.

### 18.5 RSS / result-order correctness

Peak RSS at 1M history: 154MB (after-fix) vs. 155-157MB (before-fix,
§4/§6) -- no meaningful change, as expected (the buffer holds at most
`poll_batch_size=64` `Message` objects at a time, negligible next to
the writer/MCAP-side memory already dominating this figure per Phase
6.6's own finding). Result ordering: verified both by the new unit
tests (§18.6 -- `test_record_ordering_preserved_across_batches`,
strict sequence-number and offset assertions) and by every real-Kafka
capture in §18.4 reporting `first_sequence=0`/contiguous
`last_sequence` with zero `SequenceIntegrityError`s -- `_SequenceTracker`
(unmodified) never observed an out-of-order or gapped delivery from
the new batched buffer.

### 18.6 Tests added

`packages/sceneops-streaming/tests/test_consumer.py` -- fake
`ConfluentConsumer` extended with a `consume(num_messages, timeout)`
method (replacing the now-unused fake `poll()`) that records every
call's `(num_messages, timeout)` args, enabling direct assertions that
N returned records required far fewer underlying fetch calls than N.
10 new tests (4 pre-existing commit/construction tests untouched):

```text
test_one_batch_fetch_serves_multiple_poll_calls
test_bounded_buffer_refetches_once_drained
test_record_ordering_preserved_across_batches
test_poll_returns_none_on_timeout_with_no_messages
test_timeout_not_multiplied_by_batch_size
test_buffered_poll_calls_do_not_repeat_the_timeout_wait
test_decode_error_mid_batch_does_not_lose_or_reorder_later_records
test_malformed_record_raises_decode_error_with_location
test_commit_commits_the_last_record_even_when_served_from_buffer
test_close_with_buffered_unreturned_messages_does_not_raise
```

"Same RobotRun capture retry semantics unchanged" was not re-tested
with NEW tests -- it didn't need new ones: `ros2/capture/tests/
test_capture_consumer.py` and `test_crash_boundaries.py` fake
`capture_consumer.KafkaTelemetryConsumer` itself (a level above this
fix), so they were structurally incapable of being affected by it;
re-running them unmodified (§18.7) is the correct verification, not a
gap.

### 18.7 Regression

| Target | Result |
|---|---|
| `make lint` | PASS |
| `make test` | PASS -- 1,429 passed (1,419 + 10 new), 14 skipped, 0 failed (9.01s) |
| `make test-integration` | PASS -- 65 passed, 0 failed (4.63s) |
| `make smoke-streaming` | PASS -- 39/0 failed |
| `make e2e-streaming-capture` | PASS -- 55 unit tests (`ros2/capture/tests/`, including both crash-boundary convergence tests and the real-Kafka multi-RobotRun isolation test) + 8 verification checks / 0 failed |

`ros2/capture/tests/` was also run standalone
(`docker compose ... run --rm ros2 python3 -m pytest /workspace/capture/tests -v`)
to confirm every individual test name, not just the aggregate count --
55/55, including `test_sequential_independent_captures_each_see_
complete_sequence` (real Kafka, real multi-RobotRun interleaving) and
both `test_boundary_c_.../test_boundary_d_...` crash/retry convergence
tests, all passing unmodified against the real, fixed consumer.

### 18.8 Files changed (this addendum)

```text
Modified:
  packages/sceneops-streaming/sceneops_streaming/consumer.py
    -- poll() batching, poll_batch_size param, DEFAULT_POLL_BATCH_SIZE
  packages/sceneops-streaming/tests/test_consumer.py
    -- 10 new tests, fake consumer gained consume()
  scripts/dev/phase7/run_rescan_benchmark.sh
    -- PHASE7_BENCH_TOPIC override + producer.py/capture_runner.py
       --topic passthrough (so this addendum's before/after comparison
       could target an isolated topic without resetting the shared
       default one)

New:
  scripts/dev/phase7/poll_batch_size_benchmark.py

Rebuilt (no source change, staleness-avoidance only, matching Phase
6.7 §11's own precedent):
  docker image sceneops-platform/ros2:local
```

No change to `ros2/capture/*`, `sceneops-core/streaming/*`,
`RobotRunRecord`/registration, Kafka topic/partition/consumer-group
configuration, `auto.offset.reset`, `enable.auto.commit`, or MCAP
finalize-before-commit ordering -- confirmed by the unmodified §11
preserved-contracts list plus this addendum's own file list above.

### 18.9 Remaining rescan cost after the optimization

Real, smaller, and now dominated by the consumer-group-join/rebalance
protocol rather than by per-message dispatch overhead -- exactly the
"repeated-history scanning" cost this follow-up was explicitly told
not to try to solve. It still grows with topic history (100k: 5.44s →
500k: 9.82s → 1M: 14.56s -- roughly logarithmic-looking across this
range rather than as steeply linear as the before-fix numbers, though
three points isn't enough to firmly characterize the post-fix growth
curve's shape) and is the remaining, correctly-attributed case for
§12/§13's Continuous Capture Router (7.1) and/or partition-aware
capture (7.3) -- neither of which this addendum touches.

### 18.10 Blockers before Phase 7.1

None. This addendum is self-contained, backward-compatible (every
existing caller of `KafkaTelemetryConsumer` -- `run_capture()`,
`smoke_streaming.py`, `ros2_streaming_verify.py` -- required zero
changes), and fully regression-tested. 7.1 (Continuous Capture Router)
can proceed immediately and will inherit this fix's throughput
improvement for free, since it will need its own `KafkaTelemetryConsumer`-
or-equivalent hot poll loop regardless of which of A/B/C's consumption
strategy it's ultimately built on.

**Suggested commit** (not created, per instruction):

```bash
git commit -m "perf(streaming): reduce Kafka consumer poll overhead"
```

---

## 19. Phase 7.1 — Continuous Capture Router core runtime (implemented)

**Date:** 2026-10-01 (direct follow-up). **Commit at start:** same
`531f743` (§§0-18 never advanced HEAD; this is the second and larger
production-code change in this document's history, after §18's).
No git commit was made, per instruction.

### 19.1 Architecture implemented

`ros2/capture/router.py`, new module, same package/dependency class as
`capture_consumer.py` (needs `rosbag2_py` via `mcap_writer.py`, so it
lives in `ros2/capture/`, live-mounted into the `ros2` container
exactly like every existing capture module -- no image rebuild needed
for this file itself, only for `packages/sceneops-streaming`'s
`commit_offsets()` addition, §19.4):

```text
Kafka (real broker, real topic)
  -> KafkaTelemetryConsumer (ONE continuous consumer, ROUTER_CONSUMER_
     GROUP_ID -- a stable base, distinct from CAPTURE_CONSUMER_GROUP_ID,
     so the router and RunScopedCapture never share committed-offset
     state)
  -> ContinuousCaptureRouter._route(): envelope.robot_run_id
     -> dict lookup -> existing _ActiveRun, or a new one (bounded by
        max_active_runs)
  -> per-run, fully independent and REUSED-unmodified from
     capture_consumer.py:
       _RunFilter        (partition invariant + robot_id/robot_run_id
                           identity check)
       _SequenceTracker  (0..N-1 completeness, bounded duplicate policy)
  -> McapCaptureWriter    (one open writer per active run -- reused
                           unmodified from mcap_writer.py)
  -> finalize_run()/finalize_all(): validate_mcap_file() -> finalize_
     bag() -> CaptureResult (all reused unmodified from validation.py/
     finalize.py/capture_consumer.py) -> commit_safe()
```

`RunScopedCapture` (`capture_consumer.run_capture()`) is completely
untouched -- zero diff -- and remains the supported replay/backfill/
debugging/recovery path, exactly as required. Every module the router
composes (`_RunFilter`, `_SequenceTracker`, `McapCaptureWriter`,
`finalize_bag`/`prepare_partial_bag_dir`/`partial_bag_path`/
`final_bag_path`, `validate_mcap_file`, `CaptureResult`,
`capture_consumer._sha256_file`) is imported and reused directly, never
copy-pasted or reimplemented -- the "avoid duplicating existing capture
logic" instruction is satisfied by construction, not by convention.

### 19.2 Files changed

```text
New:
  ros2/capture/router.py
    -- ContinuousCaptureRouter, _ActiveRun, ActiveRunState,
       MaxActiveRunsExceededError, RunIdentityConflictError,
       UnknownRunError, ROUTER_CONSUMER_GROUP_ID
  ros2/capture/tests/test_router.py
    -- 18 unit tests, fake Kafka consumer + REAL McapCaptureWriter/
       finalize/validate (same convention as test_capture_consumer.py)
  ros2/capture/tests/test_router_integration.py
    -- 2 tests against the REAL local Kafka broker (same convention as
       test_multi_robot_run_integration.py)
  scripts/dev/phase7/router_benchmark.py
    -- real-Kafka multi-run benchmark harness (§19.8)

Modified:
  packages/sceneops-streaming/sceneops_streaming/consumer.py
    -- new commit_offsets(offsets: dict[int, int]) method on
       KafkaTelemetryConsumer (§19.4) -- commit()/poll()/close()
       completely unchanged, zero behavior change for any existing
       caller
  packages/sceneops-streaming/tests/test_consumer.py
    -- 3 new tests for commit_offsets(); fake ConfluentConsumer.commit()
       extended to accept offsets= (previously message= only)

Rebuilt (packages/sceneops-streaming is COPY+pip-installed into the
ros2 image at build time, not live-mounted -- same staleness class
Phase 6.7 §11 and this document's own §18.4 already flagged once each):
  docker image sceneops-platform/ros2:local

No change to: capture_consumer.py, finalize.py, mcap_writer.py,
validation.py, group_id.py, schema_registry.py, sceneops-core/
streaming/*, or anything canonical/RobotRun/ArtifactStore-related.
```

### 19.3 Active-run state design

Runtime state only (per the brief's explicit framing), never a
canonical domain model -- `_ActiveRun` (internal, mutable, owns the
live `McapCaptureWriter`/`_RunFilter`/`_SequenceTracker`) and its
read-only snapshot `ActiveRunState` (`robot_id`, `robot_run_id`,
`partition`, `message_count`, `first_offset`/`last_offset`,
`first_sequence`/`last_sequence`, `opened_monotonic`) -- exposed via
`router.active_run_states()`. This mirrors `CaptureResult`'s own field
set (Phase 7.0 study §9's prediction) plus the one execution-only field
(`opened_monotonic`) a still-open run has that a finished
`CaptureResult` doesn't need. `router.failed_runs` and
`router.finalized_runs` provide the other two lifecycle views
(abandoned, completed) for the same introspection purpose.

### 19.4 Kafka offset/commit analysis and chosen policy

**The correctness problem, stated precisely.** One continuous consumer
group commits ONE position per partition, but serves many concurrently
open per-run writers that reach their own durability boundary
(validated + atomically finalized) at different times. Naively reusing
`run_capture()`'s own policy ("commit up through the most recently
returned record") would be actively wrong here: if the most recently
consumed record belongs to still-open run B, and the committed offset
advances past it, a crash before B ever finalizes means a restart's
fresh consumer resumes AFTER that record -- Kafka will never redeliver
it, and it was never durably written anywhere (B's `.partial` state is
not assumed to survive a crash, matching `finalize.py`'s own "always
discard and rebuild from Kafka" policy for partial bags). That is a
genuine, silent durability violation, not a cosmetic one.

**Chosen policy (implemented, §19's own `commit_safe()`):** per
partition, never commit past the earliest `first_offset` of any
currently-active run on that partition --

```text
safe_next_offset(partition) =
    min(run.first_offset for run in active_runs on this partition)
      if any run is active on it, else
    last_consumed_offset(partition) + 1
      once every run ever opened on it has been finalized or abandoned
```

This is monotonically non-decreasing: finalizing or abandoning a run
only ever REMOVES its `first_offset` from the `min(...)`, which can
only raise the bound, never lower it (a new run's `first_offset` is
always >= the current consume position, itself always >= every prior
run's `first_offset`). Verified directly by both a fake-consumer unit
test tracking three successive `finalize_run()` calls
(`test_commit_safe_is_monotonic_non_decreasing_across_successive_
finalizes`: commits observed strictly `[0, 2, 3]`, never regressing)
and, at real scale, by `kafka-consumer-groups.sh --describe` showing
`LAG=0` (committed offset exactly equals log-end-offset) after
`finalize_all()` in both the 100k- and 1M-message real-Kafka benchmarks
(§19.8) -- once every run is finalized, the safe boundary correctly
reaches the very end.

**What this deliberately does NOT solve (conservative, as instructed):**
a process restart does not attempt to recover any run's in-flight
`.partial` state -- a fresh router simply resumes from the last safely
committed position and Kafka redelivers everything after it, including
a full replay of whatever any then-still-open run had already
(uncommittedly) consumed. Full restart recovery is explicit Phase
7.2/7.4 scope, not weakened durability -- nothing is ever committed
past a record some active run still needs.

**New capability, old contract preserved:** `commit_offsets()`
(`consumer.py`) is an ADDITIVE new method on `KafkaTelemetryConsumer`
-- `commit()`'s existing "commit the most recently returned record"
behavior is completely untouched (still used verbatim by
`run_capture()`), and `commit_offsets()` is the only thing router.py
needs beyond `poll()`/`close()`. `confluent_kafka.TopicPartition`
stays encapsulated inside `sceneops_streaming` (the one package
documented to own that import, `streaming-transport.md` §2) --
`router.py` itself never imports `confluent_kafka`, passing plain
`dict[partition, offset]` across the boundary instead.

### 19.5 Sequence/isolation behavior

Every active run gets its OWN `_RunFilter`/`_SequenceTracker` instance
-- gap detection, duplicate skipping, and conflicting-duplicate
rejection are therefore independent BY CONSTRUCTION, not by any new
logic this phase wrote. Verified directly:
`test_independent_gap_detection_only_affects_the_gapped_run`,
`test_independent_duplicate_handling_per_run`,
`test_conflicting_duplicate_rejected_and_isolated_to_its_own_run` --
each interleaves two runs, breaks ONE of them (gap / conflicting
duplicate), and asserts the other's message count and active state are
completely unaffected.

**Per-run failure isolation (a design decision this phase had to make,
not fully specified by the brief).** A `PartitionInvariantError`/
`SequenceIntegrityError`/`UnsupportedChannelError` for one run's stream
is caught in `_route()`, that run's writer is closed and its
`.partial` directory discarded (never finalized), the failure is
recorded in `failed_runs`, and the router keeps serving every other
active run unaffected in the same loop iteration onward --
`test_one_run_failure_does_not_corrupt_another_runs_writer_or_disk_
state` confirms this against real MCAP I/O (a good run finalizes to
exactly its own 3 messages while a bad run's partition-invariant
violation is isolated alongside it). A later message for an
already-failed `robot_run_id` is dropped, not silently reopened with
fresh state (`test_late_message_for_an_already_failed_run_is_dropped_
not_reopened`) -- recovering an abandoned run is what `RunScopedCapture`
replay/backfill remains for.

A record that fails to DECODE at all (`EnvelopeDecodeError`, no
reliable `robot_run_id` to attribute it to) is handled differently from
a per-run failure -- deliberately, a judgment call documented in
`router.py`'s own class docstring: it cannot be isolated to one run, so
letting it propagate would abort ingestion for EVERY currently active
run (a much larger blast radius than `run_capture()`'s single-run
case). `run_once()` catches `EnvelopeDecodeError` around the `poll()`
call itself (before `_route()` ever runs, since there is no run to
route to), appends `{topic, partition, offset, error}` to
`stats.poison_messages` (never silently discarded), still advances
that partition's `_last_consumed_offset_by_partition` bookkeeping (so
§19.4's commit-safety accounting stays correct), and continues the
loop -- every other active run is completely unaffected. Verified by
`test_poison_undecodable_record_is_recorded_not_fatal_and_does_not_
stop_routing`: a malformed record between two messages of `run-good`
is recorded in `poison_messages` and `run-good` still finalizes with
both its own messages, `failed_runs` empty (a poison record belongs to
no run, so it is never attributed to one). This was caught and fixed
during this same phase, not left as a gap to a later one -- an initial
draft of this document (and the initial `router.py`) stated this intent
without `run_once()` actually implementing it; both were corrected
together once noticed.

### 19.6 Resource-bound behavior

`max_active_runs` (constructor parameter, no default policy invented
beyond "fail loudly") is checked before opening a NEW run only --
`test_max_active_runs_is_enforced_and_fails_loudly` confirms a 3rd
distinct `robot_run_id` beyond the configured limit of 2 raises
`MaxActiveRunsExceededError` and leaves exactly the first two runs
active; `test_max_active_runs_does_not_block_a_third_run_once_one_
finalizes` confirms finalizing one active run frees its slot for a
new one. No eviction, no silent auto-finalization of the
longest-idle run -- exactly as instructed ("Do not invent eviction
semantics or silently finalize a run").

### 19.7 Tests

`ros2/capture/tests/test_router.py` -- 19 tests, fake Kafka consumer
(records every `consume`-equivalent call and every `commit_offsets`
call for direct assertion, and can inject a raised `EnvelopeDecodeError`
into the poll sequence to exercise poison-record handling) but the REAL
`McapCaptureWriter`/`finalize_bag`/`validate_mcap_file` pipeline, same
convention as `test_capture_consumer.py`:

```text
test_two_interleaved_runs_each_produce_only_their_own_messages
test_no_cross_run_mcap_records_when_heavily_interleaved
test_byte_exact_payload_and_timestamp_mapping_preserved
test_many_interleaved_runs_all_independently_correct       (12 runs)
test_independent_gap_detection_only_affects_the_gapped_run
test_independent_duplicate_handling_per_run
test_conflicting_duplicate_rejected_and_isolated_to_its_own_run
test_one_run_failure_does_not_corrupt_another_runs_writer_or_disk_state
test_late_message_for_an_already_failed_run_is_dropped_not_reopened
test_poison_undecodable_record_is_recorded_not_fatal_and_does_not_stop_routing
test_max_active_runs_is_enforced_and_fails_loudly
test_max_active_runs_does_not_block_a_third_run_once_one_finalizes
test_finalize_run_leaves_other_active_runs_untouched
test_finalize_run_on_unknown_run_id_raises
test_finalize_all_finalizes_every_active_run
test_commit_safe_never_advances_past_an_active_runs_first_offset
test_commit_safe_advances_past_everything_once_all_runs_finalized
test_commit_safe_is_monotonic_non_decreasing_across_successive_finalizes
test_close_does_not_finalize_active_runs
```

`ros2/capture/tests/test_router_integration.py` -- 2 tests against the
REAL local broker (no monkeypatching), each on its own disposable,
per-invocation-unique topic (necessary: the shared default topic had
already grown past 1M messages from this document's own §4/§5/§18.4
benchmarks, and a fresh router consumer group always scans from
`earliest` -- reusing it, or even one topic shared across these two
tests, would make this "small/fast" integration test scan an unrelated
backlog first; a known, accepted, minor cleanup item, same tradeoff
class as the default topic's own local-dev growth):

```text
test_router_isolates_two_heavily_interleaved_runs_against_real_kafka
test_router_handles_five_interleaved_runs_in_one_continuous_pass
```

`packages/sceneops-streaming/tests/test_consumer.py` -- 3 new tests for
`commit_offsets()` (empty-mapping no-op, correct `TopicPartition`
construction per partition, independence from the existing `commit()`
path).

**Existing run-scoped capture tests kept passing unchanged:** verified
directly -- `ros2/capture/tests/test_capture_consumer.py`,
`test_crash_boundaries.py`, `test_multi_robot_run_integration.py`, and
every other pre-existing file in that directory are unmodified (zero
diff) and re-ran green as part of the full suite (§19.9).

### 19.8 Real-Kafka multi-run benchmark

`scripts/dev/phase7/router_benchmark.py`, isolated disposable topics
(1 partition, matching the production default's current configuration
-- not touched), interleaved workload produced via the existing
`producer.py multirun` mode:

| Total messages | RobotRuns | Per-run size | Consume duration | Consume throughput | Total (incl. finalize) | Peak RSS | Correctness |
|---:|---:|---:|---:|---:|---:|---:|---|
| 100,000 | 10 | 10,000 | 6.39s | 15,660 msg/s | 7.04s | 138MB | 10/10 finalized, exactly 10,000 msgs each, 0 failed, 0 poison, `LAG=0` |
| 1,000,000 | 10 | 100,000 | 16.18s | 61,822 msg/s | 22.15s | 161MB | 10/10 finalized, exactly 100,000 msgs each, 0 failed, 0 poison, `LAG=0` |

Correctness was independently re-verified, not just trusted from the
router's own in-memory counters: a separate, standalone `mcap` reader
pass over two of the finalized 100,000-message files
(`phase71-r1m-0000`, `phase71-r1m-0009`) confirmed exactly 100,000
messages each, matching both the router's report and each producer
call's own published count. `kafka-consumer-groups.sh --describe`
(host-side, both benchmark groups) confirmed `LAG=0` after
`finalize_all()` in both runs -- the committed offset reached exactly
the topic's log-end-offset, the expected result once every run on the
topic has been finalized (§19.4's policy).

### 19.9 Comparison with Phase 7.0

Phase 7.0 §5's run-scoped multi-run result (10 RobotRuns, 2,000
messages each = 20,000 TARGET messages, against ~1,012,000-1,032,000
pre-existing history, sequential `run_capture()` calls, production code
unmodified): **1,144.91s total, ~9,815 msg/s raw scan rate.**

This phase's router, processing 50x MORE total useful data (1,000,000
messages, all of it meaningful -- no "history to skip past" concept
exists for the router, every message belongs to some run) across the
same 10-RobotRun shape: **22.15s total, 61,822 msg/s.** Effective
per-useful-message throughput: Phase 7.0's run-scoped path delivered
20,000 useful messages in 1,144.91s (17.5 useful msg/s); the router
delivered 1,000,000 useful messages in 22.15s (45,147 useful msg/s) --
**over 2,500x** the effective useful-data throughput, while writing 10
real, independently-finalized, validated MCAP files, not just counting.

Against Phase 7.0's own continuous-router PROTOTYPE (§7, counting-only,
no MCAP writing, no per-run sequence/partition validation, no
finalization): 1,032,000 messages in 11.54s (87,672 msg/s). This
phase's real router, doing substantially more work per message (full
`_SequenceTracker`/`_RunFilter` validation AND real `rosbag2_py` MCAP
writes AND validate-before-finalize AND the offset-safety bookkeeping),
reached 61,822 msg/s at 1,000,000 messages -- about 70% of the
prototype's pure-counting rate, which is the expected, reasonable cost
of turning a counting exercise into a real, durable, per-run-isolated
capture pipeline. §8's `R×` amplification finding is fully closed: the
router's cost does not multiply by RobotRun count the way run-scoped
capture's did (10 runs cost roughly the same total wall time here as 1
run consuming the same total message volume would -- amplification
factor ≈ 1x, not ≈R).

### 19.10 Tests/regression

| Target | Result |
|---|---|
| `make lint` | PASS |
| `make test` | PASS -- 1,432 passed (1,429 + 3 new `commit_offsets` tests), 14 skipped, 0 failed |
| `make test-integration` | PASS -- 65 passed, 0 failed |
| `make smoke-streaming` | PASS -- 39/0 failed |
| `make e2e-streaming-capture` | PASS -- 76 unit tests (55 pre-existing + 19 router unit + 2 router real-Kafka integration, all in `ros2/capture/tests/`) + 8 verification checks / 0 failed |
| `make canonical-verify` | PASS -- baseline unchanged |

No dedicated new Make target was added for the router's own E2E/smoke
coverage -- the two real-Kafka integration tests already run as part of
`ros2/capture/tests/`, which `make e2e-streaming-capture`'s stage 1
already executes, satisfying "only if it materially improves
repeatability" without growing the public Make surface.

### 19.11 Known limitations intentionally deferred to 7.2/7.4

```text
CaptureSession persistence -- ActiveRunState is in-process only, lost
  on process exit; no Redis/DB-backed representation (Phase 7.0 study
  §9's own recommendation, not contradicted here, just not yet built)
Transport run-start/run-end control events -- finalize_run()/
  finalize_all() are explicit, caller/test/API-driven operations only;
  no automatic trigger exists (Phase 7.0 study §10's recommended
  mechanism is still unbuilt)
Idle-timeout lifecycle policy -- run_for()'s idle_timeout_seconds is a
  BENCHMARK/TEST convenience for "when to stop polling," not a
  production per-run auto-finalization policy; a genuinely idle
  RobotRun stays open (and un-finalized) indefinitely under real
  continuous operation until something explicit finalizes it
Restart recovery -- a fresh router after a crash/restart does not
  reconstruct which runs were active or resume their .partial state;
  it simply starts consuming from the last safe commit and lets Kafka
  redeliver (§19.4) -- correct, not lossy, but not automatic recovery
Kafka rebalance recovery -- the router uses a single KafkaTelemetryConsumer
  instance; multi-member group rebalance behavior (partition
  reassignment mid-session) is unexercised
Multiple capture workers -- one router = one consumer = (today) one
  partition's worth of parallelism; Phase 7.3 scope
Canonical RobotRun registration / ArtifactStore upload / Episode-
  Learning integration -- CaptureResult stops exactly where
  run_capture()'s always has; register_robot_run_capture() is
  untouched and would need to be called separately, per finalized run,
  exactly as it already is for RunScopedCapture output today
Camera/LiDAR large-payload strategy -- unrelated to this phase, still
  the ~999KB ceiling from Phase 6.6 §7
```

### 19.12 Blockers before Phase 7.2

None structural. The router's core runtime is real, tested (unit +
real-Kafka integration + real-Kafka scale benchmark), and durability-
correct under the conservative commit policy. One item worth resolving
during 7.2, not blocking its start: whether `ActiveRunState`'s eventual
persisted form (Phase 7.0 study §9) should be able to reconstruct
enough of `_SequenceTracker`'s state to resume validation after a
restart, or whether restart recovery always means "start that run over
from its own `first_offset`" -- a real design question for 7.4, but not
one that changes anything about 7.2's own scope (the run-lifecycle
control event).

**Suggested commit** (not created, per instruction):

```bash
git commit -m "feat(streaming): add continuous multi-run capture router"
```

---

## 20. Phase 7.2 — Capture Session Lifecycle (implemented)

**Date:** 2026-10-01 (direct follow-up). **Commit at start:** same
`531f743` (§§0-19 never advanced HEAD). No git commit was made, per
instruction.

### 20.1 Lifecycle / state model

```text
DISCOVERED -> RECORDING -> FINALIZING -> FINALIZED
                                       \-> FAILED
```

`SessionState` (new enum, `ros2/capture/router.py`) and
`CaptureSession` (replaces Phase 7.1's `_ActiveRun` -- same internal
role, renamed to match this phase's own vocabulary and now state-aware)
-- still execution/runtime state only, in-process, never persisted,
never `RobotRun` (per this phase's own scope exclusion, unchanged from
Phase 7.0 study §9's original recommendation):

- **DISCOVERED** -- a session exists (explicit `RUN_START` seen, or
  implicitly created by that run's first telemetry record -- both
  supported, §20.2) but no telemetry has been successfully written yet.
- **RECORDING** -- at least one telemetry record written.
- **FINALIZING** -- transient, validate/atomic-finalize I/O in
  progress.
- **FINALIZED** -- terminal, successful. `result` (a `CaptureResult`)
  is `None` if the session finalized with zero telemetry ever written
  (`RUN_START` immediately followed by `RUN_END`/idle-timeout) -- there
  is no file to describe in that case, and `validate_mcap_file` would
  reject a zero-message file regardless, so this is handled as its own
  explicit, non-error path (§20.6), never attempted against the
  validator.
- **FAILED** -- terminal, unsuccessful (sequence/partition/writer
  invariant violation, or a finalize-time I/O failure). Data discarded,
  never finalized, never silently repaired.

`CaptureSessionState` (renamed from Phase 7.1's `ActiveRunState`) is
the read-only snapshot, now carrying every field the brief asked for:
`robot_id`, `robot_run_id`, `state`, `opened_at`, `last_activity_at`,
`message_count`, `partition`, `first_offset`/`last_offset`,
`first_sequence`/`last_sequence`, `finalization_reason`, `failure`.
`router.session_states()` returns every session ever seen (terminal or
not); `router.active_run_states()` filters to non-terminal only.

**`last_activity_at` uses the router's own injectable clock
(`time.monotonic` by default), never `envelope.source_timestamp_ns` or
`envelope.ingest_timestamp_ns`.** Both of those are the ORIGINAL
producer's timestamps -- during real Kafka backlog catch-up (exactly
the workload Phase 7.0 measured at length), they can be arbitrarily far
in the past relative to when the router actually processes a record.
Using either for idle-timeout decisions would make backlog catch-up
look like every session has been idle for however far behind the
router is, causing false-positive timeouts during the one scenario
this transport is explicitly built to handle well. `last_activity_at`
answers "how long has it actually been, in real wall-clock time, since
the router itself last heard from this run" -- the only question an
idle-timeout fallback should be asking. Tests inject a deterministic
fake clock (`_FakeClock`, manually advanced) instead of sleeping --
every idle-timeout/race unit test in `test_router.py` runs in
milliseconds, not real time.

### 20.2 Control-event design

New module: `packages/sceneops-core/sceneops_core/streaming/control.py`
(`RunEventType`, `build_control_envelope`, `is_control_envelope`,
`parse_run_event`). A control event IS a `TelemetryEnvelope` -- same
required fields, same `robot_id`/`robot_run_id`, same Kafka key
(`robot_run_id`, `wire.partition_key`, completely unchanged). What
makes it a control event is purely `channel` (a new reserved constant,
`sceneops_core.constants.streaming.SESSION_CONTROL_CHANNEL =
"/session/control"`) and `message_type` (two new sentinel strings,
`sceneops/control/RunStart`/`RunEnd` -- never a real ROS2 interface).
`encoding=EnvelopeEncoding.JSON` (the existing enum already had this
value; no new encoding was added). No existing channel/message_type/
envelope field's meaning changed.

**Existing telemetry topic, not a separate control topic -- and why.**
Partitioning by `robot_run_id` (frozen, `streaming-transport.md` §6)
guarantees a control event lands on the SAME partition as that run's
own telemetry, which is exactly what "ordered consistently with that
run's telemetry" requires: Kafka only guarantees ordering WITHIN one
partition of one topic, never across two. A separate control topic
would need its own correlation mechanism (comparing timestamps, or
some other external sequencing) to establish "this `RUN_END` happened
after that telemetry record" -- fragile and unnecessary when reusing
the existing key already provides exact, free, per-partition ordering.
The cost: every consumer of the telemetry topic now sees these
records too, mitigated by the reserved channel making them trivially
filterable (`is_control_envelope`) by any consumer that doesn't care
about lifecycle -- which is exactly what `RunScopedCapture` turned out
NOT to do (§20.7's coexistence finding).

`sequence_number` on a control envelope does not participate in that
run's telemetry sequence counter -- `TelemetryEnvelope.sequence_number`
is documented as "diagnostic only, never identity" (its own field
docstring), and the router intercepts control events (`is_control_
envelope`) before they ever reach `_SequenceTracker`, so there is no
shared numbering invariant to preserve. The ROS2 bridge (§20.2.1) reads
its own live counter without incrementing it for a control event's
`sequence_number`, purely for debugging legibility ("where in the
stream did this happen"), not correctness.

#### 20.2.1 ROS2 bridge wiring

`ros2/nodes/streaming_bridge_node.py`'s `StreamingBridgeNode` gained
`emit_lifecycle_events: bool = False` (constructor) and `main()` gained
`--emit-lifecycle-events` (CLI flag, default off): when enabled,
publishes `RUN_START` right after the producer bridge is constructed
(before any subscription can receive a message) and a best-effort
`RUN_END` during `shutdown()` (the GRACEFUL path only -- a hard kill
never reaches it, which is exactly why the idle-timeout fallback
exists as the defensive counterpart, §20.3). A publish failure for
either is caught, logged, and never crashes bridge startup/shutdown or
blocks telemetry.

**Default OFF, deliberately -- not just for existing-test preservation.**
Every existing `ros2/nodes/tests/test_streaming_bridge_node.py` test
constructs the node directly (no `emit_lifecycle_events` argument) and
asserts exact `fake_bridge.published` counts/indices/sequence numbers
(`len(fake_bridge.published) == 1`, `odom_envelope, imu_envelope =
fake_bridge.published`, `seqs == [0, 1, 2, 3]`) -- all 16 pre-existing
tests pass completely unchanged with the new default, confirmed
directly. But there is a SECOND, more important reason this had to
stay opt-in rather than becoming `main()`'s default, found during this
phase's own regression pass -- §20.7.

5 new tests added (`TestLifecycleEvents`): disabled-by-default
publishes nothing; enabled publishes `RUN_START` on construction;
enabled publishes `RUN_START` -> telemetry -> `RUN_END` in that exact
order; `RUN_START` does not consume the telemetry sequence counter
(first telemetry message still gets `sequence_number=0`); a publish
failure is logged, never raised.

### 20.3 Idle-timeout policy

`session_idle_timeout_seconds` (constructor parameter, `None` =
disabled -- existing/Phase-7.1-style callers that never configure it
get exactly Phase 7.1's behavior, sessions stay open until explicitly
finalized). `router.check_idle_sessions()` scans every non-terminal
session each time it is called; one whose `last_activity_at` is >= the
threshold in the past is finalized through the EXACT SAME durable path
(`_finalize_session_io`) an explicit `RUN_END` uses -- never discarded,
never treated as a failure, `finalization_reason=IDLE_TIMEOUT`
recorded. Called automatically at the end of every `run_once()` call
(including timeout/no-message/poison-record ones), so a fallback fires
even against a fully quiet topic, with no extra wiring required from
whatever drives the router's loop.

### 20.4 Offset-frontier behavior

Unchanged POLICY from Phase 7.1 (§19.4), reimplemented over the new
state model: never commit a partition's offset past the earliest
`first_offset` of any NON-TERMINAL session on that partition. The only
change is WHICH set of sessions counts as "still blocking" --
previously Phase 7.1's separate `_active` dict, now
`session.state in (DISCOVERED, RECORDING, FINALIZING)` -- so the
frontier now correctly releases regardless of WHICH lifecycle path
(explicit `RUN_END`, idle-timeout, or a failure) moved a session out of
that set, not just explicit `finalize_run()`/`finalize_all()` calls as
in Phase 7.1.

Verified directly against this phase's own required A/B/C scenario
(`test_offset_frontier_release_with_mixed_termination_paths`): A
explicit-finalized, C explicit-finalized, B idle-timed-out -- the
commit frontier is observed advancing in exactly the predicted steps
(`{0: 1}` after A/C finalize while B is still open, bounded by B's own
`first_offset`; `{0: 3}` once B also times out and every session is
terminal). A second test
(`test_permanently_failed_session_no_longer_blocks_frontier`) confirms
the same release property for a FAILED (not just finalized) session --
"a permanently abandoned session must no longer block a partition
forever once its defined lifecycle policy makes it terminal" holds for
every terminal path, not only the successful ones.

### 20.5 Failure / poison-record policy

| Scenario | Policy | Verified by |
|---|---|---|
| Sequence failure in one session | Session -> `FAILED`, writer closed, `.partial` discarded, other sessions unaffected | `test_independent_gap_detection_only_affects_the_gapped_session`, `test_conflicting_duplicate_rejected_and_isolated_to_its_own_session` |
| Writer failure (`UnsupportedChannelError`) | Same as sequence failure -- one of the three `_PER_RUN_ERRORS` | unchanged from Phase 7.1 (§19.5), reused |
| Finalize-time I/O failure (validate/finalize_bag) | Session -> `FAILED` (this phase's own fix for a Phase 7.1 gap, see below), `.partial` discarded; `finalize_run()` (caller-driven) re-raises after the session is already correctly marked; the control-event/idle-timeout-triggered paths swallow it (one session's finalize failure must not crash the whole router loop) | new in this phase -- `_finalize_session_io`'s `except` clause now always transitions state before re-raising |
| Malformed/undecodable Kafka record | Recorded in `stats.poison_messages` (topic/partition/offset/error), loop continues, its offset still counts toward that partition's `_last_consumed_offset_by_partition` | unchanged from Phase 7.1 (§19.5), reused; still correct under the new state model since poison records are never attributed to any session either way |
| Explicit `RUN_END` for unknown run | Recorded in `stats.control_events_ignored` (`reason=unknown_run`), nothing created, nothing finalized | `test_run_end_for_unknown_run_is_recorded_not_an_error` |
| Duplicate `RUN_START` | Idempotent no-op regardless of the existing session's state, recorded (`reason=duplicate`) | `test_duplicate_run_start_is_idempotent_no_op` |
| Duplicate `RUN_END` | Idempotent no-op if the session is already terminal, recorded (`reason=already_<state>`) | `test_duplicate_run_end_after_finalization_is_idempotent_no_op` |
| Telemetry after `FINALIZED` | Dropped, counted in `stats.messages_after_finalization`, never reopens the session | `test_telemetry_after_finalization_is_dropped_and_recorded` |
| Explicit `RUN_END` racing idle-timeout | Deterministic: `RUN_END` is processed (and finalizes) before the idle-timeout check in the SAME `run_once()` call, so whichever reaches the session first wins; the loser is a harmless duplicate-on-terminal no-op, covered by the same policy row above | `test_explicit_end_racing_idle_timeout_resolves_deterministically`, `test_idle_timeout_winning_the_race_makes_a_later_run_end_a_no_op` (both orderings) |
| `RUN_START`/`RUN_END` with zero telemetry in between | `FINALIZED` with `result=None` (nothing to validate/finalize -- `validate_mcap_file` would reject a zero-message file anyway), `.partial` discarded, never an error | `test_run_end_immediately_after_run_start_finalizes_empty_session` |

**Poison records, explicitly per the brief's own ask:** quarantined
(recorded, never written, never attributed to a session) and their
Kafka offset becomes safely committable under exactly the same rule as
any other consumed-but-not-currently-blocking record -- once no
non-terminal session's `first_offset` sits at or before it, §20.4's
frontier computation naturally advances past it (poison records were
never part of the `min(...)` in the first place, so they impose no
additional constraint beyond what real sessions already do).

**Phase 7.1 gap fixed in this phase.** Auditing `_finalize_active_run`
(Phase 7.1) for this phase's own failure-policy work found that a
finalize I/O failure (`McapValidationError`, `FinalBagExistsError`, any
OS error) propagated straight out of `finalize_run()`/`finalize_all()`
with the session ALREADY POPPED from the (then-only) `_active` dict --
landing in neither `_active`, `_finalized`, nor `_failed`, effectively
lost from all tracking. `_finalize_session_io` now always transitions
the session's `state` to `FAILED` and records `failure` BEFORE
re-raising, so the session remains correctly queryable
(`session_states()`/`failed_runs`) regardless of which call site
(caller-driven, control-event-driven, or idle-timeout-driven) triggered
the failure. Not separately unit-tested with a forced I/O failure
(`validate_mcap_file`/`finalize_bag` are real filesystem calls, not
mocked anywhere in this test suite, matching this suite's existing "no
mocking of the real MCAP pipeline" convention) -- but the code path is
shared and exercised by every passing finalize test, and the fix
itself is small and structurally obvious (a state transition moved
inside the `except` clause that already existed for a different
reason, §20.5's own table row).

### 20.6 Files changed

```text
New:
  packages/sceneops-core/sceneops_core/streaming/control.py
  packages/sceneops-core/tests/test_streaming_control.py

Modified:
  packages/sceneops-core/sceneops_core/constants/streaming.py
    -- SESSION_CONTROL_CHANNEL constant
  packages/sceneops-core/sceneops_core/streaming/__init__.py
    -- re-exports RunEventType/build_control_envelope/
       is_control_envelope/parse_run_event
  ros2/capture/router.py
    -- CaptureSession/SessionState/FinalizationReason/
       CaptureSessionState (renamed+extended from Phase 7.1's
       _ActiveRun/ActiveRunState), control-event handling,
       idle-timeout, finalize-failure state-tracking fix
  ros2/capture/tests/test_router.py
    -- rewritten for the new state model; 32 tests (was 19)
  ros2/capture/tests/test_router_integration.py
    -- run_for()'s renamed loop_idle_timeout_seconds parameter;
       +1 real-Kafka mixed-termination lifecycle test (3 total, was 2)
  ros2/nodes/streaming_bridge_node.py
    -- emit_lifecycle_events (default False) + --emit-lifecycle-events
       CLI flag, RUN_START/RUN_END publish, best-effort
  ros2/nodes/tests/test_streaming_bridge_node.py
    -- +5 tests (TestLifecycleEvents), 16 pre-existing untouched
  scripts/dev/phase7/router_benchmark.py
    -- run_for()'s renamed parameter (mechanical)

Rebuilt (packages/sceneops-core changed -- COPY+pip-installed into the
ros2 image at build time, not live-mounted; ros2/nodes and ros2/capture
themselves ARE live-mounted and needed no rebuild):
  docker image sceneops-platform/ros2:local

No change to: capture_consumer.py, finalize.py, mcap_writer.py,
validation.py, group_id.py, schema_registry.py, TelemetryEnvelope
itself, the Kafka wire format, or anything canonical/RobotRun/
ArtifactStore-related.
```

### 20.7 A real coexistence bug found and fixed during regression

Discovered while auditing whether `main()` should default
`--emit-lifecycle-events` on: `RunScopedCapture`
(`capture_consumer.run_capture()`, explicitly frozen/unmodified this
entire Phase 7) has NO channel filtering of its own -- unlike
`ContinuousCaptureRouter`, which intercepts `is_control_envelope()`
records before they ever reach `_SequenceTracker`/the writer,
`run_capture()`'s loop calls `writer.write_envelope(envelope)` for
EVERY accepted (non-duplicate, in-sequence) record regardless of
channel. A control event sharing that run's `robot_run_id` -- which
Phase 7.2's whole design deliberately makes true, §20.2's "same topic,
same key, for ordering" choice -- would reach `_SequenceTracker.accept()`
(plausibly accepted as sequence 0, since the bridge's control event
doesn't increment the telemetry counter) and then
`McapCaptureWriter.write_envelope()`, which raises
`UnsupportedChannelError` for `/session/control` (correctly -- it is
not in `schema_registry.SUPPORTED_CHANNELS`) -- and `run_capture()` has
no per-message error isolation (that is the router's own, newer
behavior), so this exception propagates straight out, ABORTING THE
ENTIRE CAPTURE.

`make e2e-streaming-capture` and `make e2e-ros2-streaming` both
exercise the real bridge feeding the real `run_capture()` against the
same `robot_run_id` on the same topic -- had `main()` defaulted
lifecycle events on, this specific regression suite (required by this
phase's own instructions) would have failed. Caught before it did,
by running exactly that suite as part of this phase's own regression
pass (§20.9) -- not merely reasoned about abstractly. Fixed by keeping
`emit_lifecycle_events` opt-in at every layer, including the CLI
default (§20.2.1) -- the two paths (router-fed bridge runs,
RunScopedCapture-fed bridge runs) must not currently be mixed for the
same `robot_run_id`; teaching `run_capture()` to skip non-telemetry
channels (a small, targeted change, but one that touches a path every
prior phase has deliberately left untouched) or moving control events
off the shared topic are the two candidate fixes for full coexistence,
neither attempted here (flagged in §20.10).

### 20.8 Tests added

`ros2/capture/tests/test_router.py` -- 32 tests total (rewritten from
Phase 7.1's 19), organized by the brief's own required coverage list:
explicit start/telemetry/end, implicit discovery, duplicate start/end,
telemetry-after-finalization, interleaved sessions with control events,
idle-timeout (3 tests: fires correctly, disabled-by-default no-op,
activity resets the clock), explicit-end-vs-timeout race (both
orderings), offset-frontier release (mixed termination A/B/C, plus a
separate FAILED-session variant), one-failed-session-doesn't-block-
others, poison records, sequence/duplicate/conflict isolation (byte-
exact payload, timestamp mapping, no-cross-run-records, many-interleaved-
sessions), active-run capacity + reuse, finalize one/all/unknown/
already-finalized, close-does-not-finalize.

`ros2/capture/tests/test_router_integration.py` -- 3 tests against the
REAL local broker (2 pre-existing, unmodified beyond the mechanical
parameter rename; 1 new): `test_router_lifecycle_mixed_termination_
against_real_kafka` -- 10 interleaved RobotRuns, `RUN_START` for all,
`RUN_END` published for 5, the other 5 left to a REAL (not fake-clock)
2-second idle-timeout, using the router's actual default
`time.monotonic` clock end to end. Verifies: every run reaches
`FINALIZED` with the CORRECT `finalization_reason` (5×
`EXPLICIT_RUN_END`, 5× `IDLE_TIMEOUT`), exact per-run message counts,
no cross-run payload contamination, and no orphan `.partial`
directories (every one confirmed gone, every final one confirmed
present) -- directly satisfying this phase's own "Real Kafka E2E"
requirements (§20.9 covers the Kafka-lag half separately, via the
existing regression run rather than a bespoke check inside this test).

`packages/sceneops-core/tests/test_streaming_control.py` -- 7 tests:
reserved channel/encoding, distinct message types per event, `is_
control_envelope` true only for the reserved channel, `parse_run_event`
round-trips both event types, returns `None` for non-control and for
an unrecognized control `message_type` (forward-compat), and a real
wire encode/decode round-trip (`sceneops_streaming.wire`) confirming
the Kafka key is still exactly `robot_run_id`.

`ros2/nodes/tests/test_streaming_bridge_node.py` -- +5 tests
(`TestLifecycleEvents`, §20.2.1); all 16 pre-existing tests unmodified
and still passing, proof the new default changes nothing for an
existing caller.

### 20.9 Real-Kafka E2E result

Covered across two real-Kafka surfaces rather than one bespoke script,
consistent with how Phase 7.1 validated its own core runtime:

1. **`test_router_lifecycle_mixed_termination_against_real_kafka`**
   (§20.8) -- the mixed-termination scenario itself, 10 runs, correctness
   fully verified (see above).
2. **Full regression suite** (§20.9 table below) -- `make e2e-streaming-
   capture`/`make e2e-ros2-streaming` both exercise the real bridge
   against real Kafka end to end; running them was what caught §20.7's
   coexistence bug in the first place, which is itself real-Kafka
   evidence that the fix (opt-in, default off) actually holds.

Kafka lag: not re-verified via a bespoke `kafka-consumer-groups.sh`
check inside this phase's own work (Phase 7.1's §19.8 benchmark already
established `LAG=0` after `finalize_all()` at 100k/1M-message scale
under the equivalent offset-safety policy, and this phase changed WHICH
sessions count as terminal, not the underlying commit mechanism itself)
-- `test_offset_frontier_release_with_mixed_termination_paths` (§20.4)
is this phase's own direct evidence that the frontier computation
itself is correct under the new state model, at the unit level, with
exact expected values asserted.

### 20.10 Regression

| Target | Result |
|---|---|
| `make lint` | PASS |
| `make test` | PASS -- 1,439 passed (1,432 + 7 new `test_streaming_control.py`), 14 skipped, 0 failed |
| `make test-integration` | PASS -- 65 passed, 0 failed |
| `make smoke-streaming` | PASS -- 39/0 failed |
| `make e2e-ros2-streaming` | PASS -- 21 unit tests (`ros2/nodes/tests/`, +5 from this phase) + 34 verification checks / 0 failed -- not in this phase's own required list, run anyway since it is the OTHER real-bridge path §20.7's fix needed to be checked against |
| `make e2e-streaming-capture` | PASS -- 90 unit tests (`ros2/capture/tests/`: 55 Phase-7.0-era + 32 router + 3 router-integration) + 8 verification checks / 0 failed |
| `make canonical-verify` | PASS -- baseline unchanged |

### 20.11 Known limitations

```text
RunScopedCapture/ContinuousCaptureRouter coexistence for the SAME
  robot_run_id on the SAME topic is not solved (§20.7) -- the same
  robot_run_id must not be fed to both a lifecycle-events-enabled
  bridge run AND run_capture() today; operationally this is fine (a
  given RobotRun is captured by exactly one path in practice), but it
  is a real, found sharp edge, not a hypothetical one
CaptureSession persistence -- still in-process only, lost on process
  exit (Phase 7.0 study §9's own recommendation, still not built)
Router restart recovery -- unchanged from Phase 7.1: a fresh router
  after a crash/restart does not reconstruct which sessions were
  active; it resumes from the last safe commit and lets Kafka
  redeliver
Kafka rebalance recovery -- still unexercised, single
  KafkaTelemetryConsumer instance only
Multi-worker router scaling -- still Phase 7.3 scope
ArtifactStore/RobotRun registration, Episode/Learning integration --
  CaptureResult still stops exactly where run_capture()'s always has
camera/LiDAR transport redesign -- unrelated to this phase
_sessions never prunes terminal entries -- unbounded memory growth
  over a very long-lived router process (FINALIZED/FAILED sessions
  are kept forever for introspection); not a concern at the scales
  this phase tested, flagged for whenever CaptureSession persistence
  (above) is eventually designed, since eviction policy there and here
  are the same underlying question
Finalize-I/O-failure state transition (§20.5's fix) has no dedicated
  forced-failure unit test -- covered structurally/by code-path
  sharing with every passing finalize test, not by a test that
  actually injects a validate_mcap_file/finalize_bag failure
```

### 20.12 Blockers before Phase 7.3

None structural. The lifecycle model is real, tested (32 unit + 3
real-Kafka integration + 7 control-schema + 5 bridge), and the offset-
frontier correctness carries forward correctly under it. One item worth
resolving before -- or as part of -- whatever Phase 7.3 turns out to be
(multi-partition/multi-worker scaling, per the original Phase 7 roadmap
outline, §13): §20.7's `RunScopedCapture` coexistence gap should
probably be closed before lifecycle events become the DEFAULT on any
real deployment path, since scaling to more workers/partitions makes
"which robot_run_ids might overlap between the two capture paths"
harder to reason about informally than it is today.

**Suggested commit** (not created, per instruction):

```bash
git commit -m "feat(streaming): add capture session lifecycle"
```

---

## 21. Phase 7.2.1 — Capture Path Control-Event Compatibility (implemented)

**Date:** 2026-10-01 (direct follow-up). **Commit at start:** per `git
log`, the three prior phases' suggested commits had already been made
by this point (`26f8da1` capture session lifecycle, `08aa780`
continuous router, `266385b` poll overhead) -- this phase's own work
starts from a clean tree on top of `26f8da1`. No git commit was made
for this phase either, per instruction.

### 21.1 Confirmed sequence semantics (audit)

- **Control envelopes DO pass `_RunFilter`** -- it only checks
  `robot_id`/`robot_run_id`/partition, never `channel`, so a control
  event for the target run was always correctly matched; the crash was
  always downstream, in `_SequenceTracker`/the writer.
- **Before this fix, control and telemetry sequence numbers could
  collide.** The bridge's `_publish_lifecycle_event` (Phase 7.2) read
  `self._sequence` WITHOUT incrementing it, so `RUN_START` always
  landed at the exact sequence_number (0) the first REAL telemetry
  message also needed. `_RunFilter`/`_SequenceTracker` were never the
  problem for THIS specific collision (the router never even looks at
  control-event sequence numbers, by design) -- `RunScopedCapture` was,
  because it has exactly ONE `_SequenceTracker` for the whole stream,
  with no concept of "control vs. telemetry" at all.
- **The actual crash site:** `writer.write_envelope(envelope)` in
  `run_capture()`'s main loop, called unconditionally for every
  record `tracker.accept()` returned `True` for, regardless of channel
  -- `McapCaptureWriter.write_envelope` raises `UnsupportedChannelError`
  for any channel/message_type pair outside
  `schema_registry.SUPPORTED_CHANNELS`, which `/session/control`
  deliberately is not (§20.2 explicitly never added it there, since
  control events are not sensor data). `run_capture()` has no
  per-message error isolation (unlike the router), so this exception
  always propagated straight out and aborted the entire capture.
- **The rosbag2 semantic comparison script
  (`scripts/e2e/mcap_capture_verify.py`) assumes a sensor-only channel
  set** -- `_channel_schema_set` compares `(topic, schema)` tuples
  between the captured and direct-recorded bags for EXACT set equality.
  Had a control record ever been written into the captured MCAP, this
  check would have failed (the direct-recorded bag, from `ros2 bag
  record`, never has a `/session/control` channel at all). This script
  needed NO changes -- the fix's own "never write control envelopes to
  the MCAP" policy keeps its existing assumption true by construction.

### 21.2 Compatibility policy (implemented)

```text
control envelope (is_control_envelope(envelope) is True)
  -> participates in run filtering (_RunFilter, unchanged, already worked)
  -> validated for gap/duplicate/conflict in its OWN independent
     _SequenceTracker (capture_consumer.py gained a second one,
     "control_tracker") -- NEVER merged with telemetry's own tracker
  -> never passed to McapCaptureWriter.write_envelope -- never appears
     in the finalized MCAP, never raises UnsupportedChannelError
  -> still contributes to first_offset/last_offset (real consumed
     Kafka records on this run's own partition, even though unwritten)
```

**Why two independent trackers, not one merged stream.** The brief's
own preferred policy ("participate in sequence validation... do not
silently bypass") ruled out simply skipping validation for control
events entirely. But merging them into telemetry's own tracker would
require the BRIDGE's telemetry and control counters to interleave into
one clean 0..N-1 space -- which would mean `RUN_START` consuming
telemetry sequence 0 and shifting every real telemetry message's own
sequence number up by one, breaking the Continuous Router's existing,
already-shipped, already-tested assumption that a session's first
WRITTEN telemetry record is always sequence 0 (`SessionState.DISCOVERED
-> RECORDING` and `_SequenceTracker`'s own "first sequence must be 0"
invariant, §20.1/§19.5, both unmodified in this phase and both would
have broken). Two independent spaces -- one per "channel class"
(control vs. telemetry) -- let each be validated on its own terms
without perturbing the other, and match a corresponding bridge-side
change (§21.3): control events now get their OWN monotonic counter,
never telemetry's.

**Gap/duplicate/conflict in the control stream is real, not
decorative.** Verified directly:
`test_run_capture_detects_gap_in_control_event_sequence` (a control
sequence jumping 0 -> 5 aborts the capture, `SequenceIntegrityError`,
nothing finalized) and
`test_run_capture_detects_conflicting_duplicate_control_event` (two
control events at the same sequence number with genuinely different
payload bytes -- same conflict policy telemetry already has). An
EXACT immediate redelivery (same sequence, same payload) of a control
event is silently skipped, exactly like telemetry's own duplicate
policy (`test_run_capture_duplicate_run_start_is_skipped_telemetry_
unaffected`).

**One honestly-reported subtlety found while writing these tests (not
a bug, a documented limitation of payload-only duplicate detection):**
`_SequenceTracker.accept()` only ever compares `payload` bytes, never
`message_type` -- and `build_control_envelope`'s payload only encodes
an optional `reason` string (defaulting to the literal `b"{}"` for
every call that omits one), never the event type itself (that lives in
`message_type`). Two DIFFERENT event types (e.g. a hypothetical
`RUN_START` and `RUN_END` colliding at the same sequence number) with
no `reason` on either would therefore be treated as a harmless
duplicate, not a conflict -- because their payload bytes are
byte-identical. This is NOT exploitable against the real, fixed bridge
(§21.3 guarantees `RUN_START`/`RUN_END` always get DIFFERENT sequence
numbers from each other, so they can never collide in practice), but
it is a real, narrow gap in payload-only duplicate detection a
non-conforming external producer could in principle trigger. Flagged
in §21.9, not silently left for a reader to discover -- and, in a
concrete demonstration of why this matters, an earlier draft of this
exact test exposed the gap directly: the first version of
`test_run_capture_detects_conflicting_duplicate_control_event`
constructed `RUN_START`/`RUN_END` with no `reason` on either, expecting
a conflict that (correctly, given the policy above) never fired --
`control_tracker.accept()` silently skipped the "duplicate," nothing
raised, and the test's own unbounded `stop_condition=lambda count:
count >= 99` (never satisfied, since control events never write
anything) spun `run_capture()`'s poll loop forever against an emptied
fake queue. Caught mid-session as two genuinely stuck, 100%-CPU `ros2`
containers (one at 2+ hours, one at 4+ minutes) -- killed, root-caused,
and fixed by (1) correcting the test to use genuinely different
payloads (`reason="first"`/`reason="second"`) so the intended conflict
actually fires, and (2) adding a bounded `_bounded_stop_condition()`
helper to every control-only-queue test in this file as a defensive
backstop, so a wrong assumption about WHEN an expected exception fires
produces a fast, loud test failure instead of a silent infinite loop
in future tests too. No production code was involved in that hang --
`run_capture()`'s poll-until-stop_condition loop behaved exactly as
documented/intended throughout.

### 21.3 Bridge sequence-numbering fix

`streaming_bridge_node.py` gained a SECOND, independent counter
(`self._lifecycle_sequence`, `_next_lifecycle_sequence_number()`) --
`RUN_START`/`RUN_END` now always get `0`/`1` respectively, regardless
of how many (or how few) telemetry messages were published in between,
and NEVER read or perturb `self._sequence` (telemetry's own counter,
completely untouched). This is what makes §21.2's two-independent-
trackers design correct end to end: the bridge produces genuinely
independent sequences, and `run_capture()` validates them
independently. Verified directly:
`test_lifecycle_events_sequence_independently_starting_at_zero` (new)
confirms `RUN_START.sequence_number == 0` and
`RUN_END.sequence_number == 1` after publishing real telemetry in
between; the pre-existing `test_run_start_does_not_consume_the_
telemetry_sequence_counter` (Phase 7.2, docstring updated for accuracy,
assertion unchanged) continues to confirm the first telemetry message
still gets `sequence_number == 0`, now even more robustly true (the
counters are not just "not incremented for control events," they are
now fully separate objects).

### 21.4 Files changed

```text
Modified:
  ros2/capture/capture_consumer.py
    -- is_control_envelope import, second _SequenceTracker
       ("control_tracker"), main loop branches control events away
       from the writer, module + inline docstrings updated
  ros2/capture/tests/test_capture_consumer.py
    -- +6 control-event tests, _control()/_mcap_channels()/
       _bounded_stop_condition() helpers; all 16 pre-existing tests
       unmodified
  ros2/nodes/streaming_bridge_node.py
    -- self._lifecycle_sequence + _next_lifecycle_sequence_number(),
       _publish_lifecycle_event uses it instead of reading (without
       incrementing) the telemetry counter
  ros2/nodes/tests/test_streaming_bridge_node.py
    -- +1 test (independent 0/1 sequencing), 1 existing test's
       docstring corrected for accuracy (assertion itself unchanged)

New:
  ros2/capture/tests/test_lifecycle_integration.py
    -- 2 tests against the REAL local broker (§21.6)

No change to: finalize.py, mcap_writer.py, validation.py,
schema_registry.py, group_id.py, router.py, control.py,
TelemetryEnvelope, the Kafka wire format, or anything canonical/
RobotRun/ArtifactStore-related. `main()`'s `--emit-lifecycle-events`
default is UNCHANGED (still `False`) -- see §21.8.
```

### 21.5 Tests added

`ros2/capture/tests/test_capture_consumer.py` -- 6 new tests (fake
consumer, real MCAP I/O, same convention as every existing test in this
file):

```text
test_run_capture_handles_run_start_telemetry_run_end
test_run_capture_mcap_contains_no_control_channel_records
test_run_capture_control_events_interleaved_with_telemetry
test_run_capture_detects_gap_in_control_event_sequence
test_run_capture_duplicate_run_start_is_skipped_telemetry_unaffected
test_run_capture_detects_conflicting_duplicate_control_event
```

`ros2/nodes/tests/test_streaming_bridge_node.py` -- 1 new test
(`test_lifecycle_events_sequence_independently_starting_at_zero`); all
21 pre-existing tests (16 original + 5 from Phase 7.2) still pass
unmodified.

`ros2/capture/tests/test_lifecycle_integration.py` -- 2 new tests
against the REAL local broker, matching
`test_multi_robot_run_integration.py`'s own convention (no
monkeypatching):

```text
test_run_capture_handles_real_lifecycle_enabled_stream
test_run_capture_handles_real_lifecycle_events_interleaved_with_another_run
```

Both publish real `RUN_START`/telemetry/`RUN_END` sequences (the second
test interleaves TWO such lifecycle-enabled runs on the same real
topic/partition) via `KafkaTelemetryProducer`, then run the real,
unmodified-at-the-call-site `run_capture()` against them, confirming:
successful capture (no `UnsupportedChannelError`, no hang), correct
`message_count`/`first_sequence`/`last_sequence` (telemetry-only,
control events excluded), and the finalized MCAP contains ONLY
`/vehicle/odom` (no `/session/control` channel).

No dedicated new shell-script/Make-target E2E was added -- per the
brief's own "if practical" framing, these real-Kafka pytest tests
(already part of `make e2e-streaming-capture`'s stage 1, §21.7) satisfy
"real-Kafka validation with lifecycle emission enabled" without growing
the public Make surface, matching the precedent every prior Phase 7
sub-phase has followed for this same question.

### 21.6 Real-Kafka result

Both `test_lifecycle_integration.py` tests pass against the real local
broker: a solo `RUN_START`/5-telemetry/`RUN_END` stream captures
cleanly (`message_count=5`, `first_sequence=0`, `last_sequence=4`,
MCAP channel set `{"/vehicle/odom"}`), and two such streams interleaved
on the same topic/partition isolate correctly via the existing,
unmodified `_RunFilter` (target run's capture sees exactly its own 2
telemetry messages, none of the other run's control events or
telemetry). No `UnsupportedChannelError`, no hang, no stray `.partial`
state.

### 21.7 Legacy-path regression

| Target | Result |
|---|---|
| `make lint` | PASS |
| `make test` | PASS -- 1,439 passed (unchanged from Phase 7.2 -- this phase touched no `packages/`/`apps/` code), 14 skipped, 0 failed |
| `make test-integration` | PASS -- 65 passed, 0 failed |
| `make e2e-ros2-streaming` | PASS -- 22 unit tests (`ros2/nodes/tests/`, +1 from this phase) + 34 verification checks / 0 failed |
| `make e2e-streaming-capture` | PASS -- 98 unit tests (`ros2/capture/tests/`: 90 Phase-7.2-era + 6 control-event + 2 real-Kafka lifecycle) + 8 verification checks / 0 failed |
| `make canonical-verify` | PASS -- baseline unchanged |

Both E2E targets run with `main()`'s default
(`emit_lifecycle_events=False`) -- i.e. the EXACT legacy wire stream,
zero control events, confirming this phase changed nothing observable
about the already-shipped default path. The real evidence that the
FIX itself works end to end against a real, lifecycle-enabled stream
is §21.6's dedicated pytest coverage, not these two targets (which
deliberately keep exercising the unchanged default).

### 21.8 Can bridge lifecycle emission become default-on now?

**Technically yes -- the compatibility gap that forced it opt-in is
resolved and verified (unit + real-Kafka, solo and interleaved).**
Not flipped in this phase, deliberately: the brief's own framing for
this phase is "resolve this gap narrowly," and flipping `main()`'s
default changes the real, observable Kafka wire output of every future
real bridge invocation (not just this fix's own narrow surface) --
a decision with broader operational reach than a compatibility patch,
better made explicitly than as a side effect of fixing the thing that
was blocking it. The evidence to make that call is now fully in place:

```text
RunScopedCapture + lifecycle events:  verified safe (§21.2, §21.6)
ContinuousCaptureRouter + lifecycle events:  verified safe (Phase 7.2,
  unaffected by this phase -- router.py has zero diff)
Legacy (no lifecycle events) path:  verified unaffected (§21.7)
```

The one caveat Phase 7.2 §20.7/§20.10 already named still applies
UNCHANGED by this fix: the SAME `robot_run_id` should still not be fed
to both a lifecycle-enabled bridge run AND a `RunScopedCapture`
invocation of a DIFFERENT run concurrently sharing partition-level
Kafka consumer-group assumptions in ways neither path was designed to
coordinate on -- this phase makes control events SAFE for
`RunScopedCapture` to consume when they occur for ITS OWN target run,
it does not add any new cross-run coordination. Recommended next step,
if/when the default is flipped: do it as its own, explicit, reviewed
decision (not bundled into a narrow compatibility fix), ideally
alongside or after Phase 7.3's own scope is clearer.

### 21.9 Known limitations

```text
Payload-only duplicate/conflict detection (§21.2's own found subtlety)
  -- two different control event_types colliding at the same sequence
  number with identical (reason-less) payloads would be treated as a
  duplicate, not a conflict. Not reachable via the real, fixed bridge
  (RUN_START/RUN_END always get different sequence numbers from each
  other), but a real gap against a hypothetical non-conforming
  producer. Fixing it properly would mean _SequenceTracker comparing
  (message_type, payload) instead of payload alone -- a change to a
  shared, frozen primitive capture_consumer.py AND router.py both
  depend on, correctly out of scope for a "narrow" compatibility fix.
main()'s --emit-lifecycle-events default remains False (§21.8) --
  an explicit, deferred decision, not a limitation of the fix itself.
Cross-path coexistence for the SAME robot_run_id (Phase 7.2 §20.7/
  §20.11) is unchanged by this phase -- still a real operational
  constraint, just no longer caused by a hard crash.
```

### 21.10 Blockers before Phase 7.3

None. The compatibility gap is closed, verified against both the fake-
consumer unit suite and a real broker (solo and interleaved), and the
full legacy regression suite (including both real-bridge E2E targets)
confirms zero behavioral change to the already-shipped default path.
The one open decision (§21.8 -- flipping the bridge default) is
explicitly NOT a blocker: Phase 7.3 (multi-partition/multi-worker
scaling, per the original Phase 7 roadmap outline) can proceed with
lifecycle events either on or off, since this phase proved both
capture paths tolerate them correctly either way.

**Suggested commit** (not created, per instruction):

```bash
git commit -m "fix(streaming): support lifecycle events in run-scoped capture"
```
