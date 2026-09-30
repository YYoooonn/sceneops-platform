# Phase 7.0 — Multi-Run Streaming Architecture Study

> Point-in-time record (category C — historical evidence, per this
> repo's documentation taxonomy) of the Phase 7.0 architecture/benchmark
> study: current run-scoped capture audited, benchmarked against
> growing topic history and a multi-run workload, and compared against
> two prototyped alternatives (partition-aware capture, a Continuous
> Capture Router). Not a living architecture contract — see
> [Streaming transport](./streaming-transport.md) and
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
(Part 3) and `docs/architecture/streaming-reliability-scale-baseline.md`
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
7.0.1  Poll-loop implementation fix
       Stop wrapping every single ros2/capture Kafka poll() call in
       asyncio.to_thread -- §4.1 measured this as ~85-90% of A's
       apparent rescan cost at 1M-message history, entirely
       independent of the run-scoped-vs-continuous or single-vs-multi-
       partition questions. Cheap, low-risk (no protocol/contract
       change), and benefits A, B, AND any future C implementation
       equally, since C will need its own hot poll loop regardless of
       which of A/B/C's consumption strategy it's built on. Do this
       FIRST, or at minimum in parallel with 7.1 -- it is not gated on
       any of the architecture decisions below.

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
  docs/architecture/streaming-multirun-phase7-study.md   (this file)
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
