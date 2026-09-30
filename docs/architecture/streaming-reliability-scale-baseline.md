# Streaming Reliability & Scale Baseline (Phase 6.6)

> Point-in-time record (category C — historical evidence, per this repo's
> documentation taxonomy) of the streaming pipeline's operational
> guarantees and measured limits, established by real failure-injection
> tests and real benchmarks against the local Kafka/Postgres/MinIO stack.
> Not a living architecture doc — see
> [Streaming transport](./streaming-transport.md) and
> [Robot run / MCAP](../workflows/robot-run-and-mcap.md) for the current,
> maintained contracts this record validates. Numbers here are frozen to
> the date/commit/environment below; re-run the referenced
> tests/benchmarks for current numbers, don't assume these stay accurate.

**Date:** 2026-09-30
**Commit (start of this work):** `0cfaa353b0c612d345297018ccf7a05abf7954ce` (branch `feat/streaming-robot-data`)
**Environment:** macOS (Darwin 24.3.0, arm64), Docker 27.5.1, 8 logical CPUs, single-node local Compose stack (`apache/kafka:3.9.2` KRaft mode, 1 broker, 1 partition on `sceneops.robot.telemetry.v1`; Postgres 16; MinIO). Not a production/multi-broker/multi-partition environment — see §9's caveat.

---

## 1. Reliability matrix (A–G)

| # | Failure point | Expected state | Actual state (tested) | Retry action | Converges? | Duplicate canonical state? | Data loss possible? |
|---|---|---|---|---|---|---|---|
| A | Kafka unavailable before publish | `publish()` buffers client-side, no immediate error; `flush()`/`close()` eventually reports failure | Confirmed: `publish()` never raised synchronously; `close()`'s `flush(timeout_seconds=10.0)` (default) raises `TimeoutError` once the broker stays unreachable past that window | Caller must retry publish+flush against a broker that is now reachable (a fresh `KafkaTelemetryProducer`, or the same one — new instance confirmed to work) | Yes, once retried after Kafka is back | No | No (records never left the client; nothing was falsely acknowledged) |
| B | Kafka unavailable during stream | Same as A — in-flight publishes fail to deliver, surfaced only at the next `flush()`/`close()` | Confirmed identical to A: 5 messages `publish()`-buffered while broker down, `flush(timeout_seconds=8.0)` raised `TimeoutError('5 telemetry record(s) still undelivered...')` | Same as A | Yes | No | No (same reasoning) |
| C | Capture process dies before finalize | No valid final MCAP; offsets not committed; retry discards the stale `.partial` and rebuilds from Kafka | Confirmed via `ros2/capture/tests/test_crash_boundaries.py::test_boundary_c_...`: a stale, incomplete `.partial` dir (simulating a mid-write crash) is discarded by `prepare_partial_bag_dir()`, and a fresh `run_capture()` finalizes cleanly | None needed beyond re-invoking capture | Yes | No | No |
| D | Capture crash after finalize, before Kafka offset commit | Valid final MCAP exists; offsets may be uncommitted; retry must not destructively overwrite | **Gap found and fixed** (see §2) — previously `run_capture()` crashed with `FinalBagExistsError` on retry instead of converging; now catches it, compares checksums, and either converges (commits + returns the existing result) or re-raises on a genuine conflict | None needed — the next capture attempt (Kafka redelivers, nothing was committed) now converges automatically | Yes (after the fix) | No | No |
| E | ArtifactStore upload succeeds, DB registration fails | Retry reuses the already-uploaded object, completes DB registration | Confirmed by Phase 6.4's existing `apps/worker/tests/robots/test_registration_integration.py::test_orphaned_object_reuse_against_real_infra` (re-run in this phase, still passes; also re-verified manually at 11.1MB scale, §6) | Re-run `register-capture` with the same `robot_run_id`/file | Yes | No | No |
| F | Episode job fails after materialization | Materialized temp file cleaned up; canonical MCAP unchanged; retry succeeds; no duplicate state | Confirmed by new `apps/worker/tests/robots/test_episode_retry_integration.py` — a real `BuildEpisodesJobHandler` run, failure injected inside `RosbagAdapter.extract_episode_source` (after materialization), then retried | Re-run `raw_log_episode_building` (existing `force: true` semantics) | Yes | No (failed attempt wrote nothing; retry writes exactly its own expected artifact count) | No |
| G | Worker/process restart with disposable temp files | Orphaned temp files don't interfere with later, unrelated materializations; container ephemerality eventually reclaims them | Confirmed by new `apps/worker/tests/robots/test_materialization.py::test_orphaned_temp_dir_from_simulated_crash_does_not_interfere_later` | None — no cleanup subsystem exists or is needed; the worker container's OS temp dir has no persistent volume backing it | N/A (nothing to converge — isolation is structural, `tempfile.mkdtemp`'s own randomness) | No | No (temp copies are disposable by design; the canonical object is untouched) |

## 2. Capture crash-boundary fix (item D)

`ros2/capture/capture_consumer.py`'s `run_capture()` previously let
`finalize_bag()`'s `FinalBagExistsError` propagate uncaught. A retry
after "finalized but never committed" (Kafka redelivers the same
messages, since nothing was actually acknowledged) would independently
re-consume, re-write, and re-validate the same content, then crash on
`finalize_bag()` because the prior attempt's final file already exists
— never destructive, but never convergent either; a naive retry loop
would crash forever on the same file.

**Fix (smallest required change):** catch `FinalBagExistsError`; compare
the just-written (still-partial) file's checksum against the existing
final file's checksum. If they match, this is the same capture
completing a second time — discard the redundant partial copy, commit
the Kafka offset now, and return a `CaptureResult` describing the
existing final file. If they differ, it's a genuine conflict — re-raise,
never silently resolved. See `ros2/capture/tests/test_crash_boundaries.py::test_boundary_d_...`.

Frozen commit-boundary ordering (Phase 6.3) is unchanged — this only
adds a convergence path for one specific, previously-uncrashable-but-
unrecoverable retry scenario.

## 3. Duplicate / gap / out-of-order matrix

All 7 requested rows verified directly against `_SequenceTracker`
(`ros2/capture/tests/test_sequence_matrix.py`), against the frozen v1
policy — no behavior changed, this is verification only.

| Sequence pattern | Row | Result |
|---|---|---|
| 0,1,2,3 | normal | All 4 accepted, written in order |
| 0,1,1,2 | exact immediate duplicate | The second `1` silently skipped (not written twice); `2` still accepted |
| same seq, different payload | conflicting duplicate | Rejected (`SequenceIntegrityError`, "conflicting redelivery") |
| 0,1,3 | gap | Rejected on `3` ("sequence gap") — v1 has no reorder buffer |
| 0,2,1 | late sequence | Rejected on the SECOND message (`2` is itself a gap); the tracker never evaluates the third message (`1`) separately — late arrivals following a gap are caught by the gap check first |
| 0,1,2,1 | duplicate after later records | Rejected ("out-of-order/late") — only the single immediately-preceding sequence is ever treated as a safe-to-skip duplicate |
| 5,6,7 | non-zero first sequence | Rejected immediately ("first sequence... expected 0") |

No case was broadened. Every intentional v1 rejection stays a rejection.

## 4. Multi-RobotRun isolation (real Kafka)

Local topic `sceneops.robot.telemetry.v1`: **`PartitionCount: 1`** (verified via `kafka-topics.sh --describe`) — every `robot_run_id` currently lands on the same partition, making interleaving the realistic default, not an edge case.

Tested against the real broker (`ros2/capture/tests/test_multi_robot_run_integration.py`):

1. **Within one capture invocation**, `RunFilter` correctly isolates the target `robot_run_id` from an interleaved different run on the same partition — the finalized MCAP contains only the target run's messages, verified by payload inspection, not just message count.
2. **Real limit found**: two SEQUENTIAL, independent capture invocations sharing the frozen `CAPTURE_CONSUMER_GROUP_ID` ("sceneops-mcap-capture", frozen since Phase 6.3 — no per-robot_run_id group scheme exists) do **not** get independent offset cursors. If run A's poll loop has to read past run B's interleaved messages to reach A's own target count, A's `commit()` advances the *shared* group's committed offset past those B messages too, even though A never wrote them anywhere. A later, separate capture for B can then find its own early messages already "consumed" by the group. Empirically, this doesn't corrupt B's MCAP silently — it manifests as B's capture failing loudly with `SequenceIntegrityError` ("first sequence... expected 0"), because the missing prefix also violates the frozen first-sequence-must-be-0 invariant (Phase 6.3). **This is a real, documented v1 limit, not fixed in this phase** — building per-robot_run_id offset isolation would need either a per-run consumer group scheme or a partition-per-run guarantee, both out of scope (`CAPTURE_CONSUMER_GROUP_ID` is frozen; "do not solve this with a process-local or Redis lock").

**Operational implication:** run exactly one capture at a time per shared consumer group in this v1 design; concurrent or overlapping-window captures for different `robot_run_id`s on the same partition are not currently safe.

## 5. Kafka restart behavior

See matrix rows A/B (§1) — measured via a real stop/start of the local
`kafka` container mid-produce. Summary: **no transparent recovery
claim** — `publish()` buffers client-side and returns immediately
regardless of broker reachability; only `flush()`/`close()` surface the
failure, and only after the caller's own timeout. A freshly-constructed
`KafkaTelemetryProducer` after the broker returns works immediately with
no special reconnection handling required (confirmed). The existing
`KafkaTelemetryProducer.flush()`'s own `TimeoutError` (§ its docstring)
is the correct signal for "give up and retry," not a lower-level
`KafkaException` a caller would need to interpret.

## 6. Backpressure and scale benchmark

No unbounded application-level queue exists anywhere in the produce→capture path — verified directly: Kafka consumer-group lag (`kafka-consumer-groups.sh --describe`) tracked the full published backlog exactly (`lag == message_count` immediately after producing) and drained to exactly `0` after capture completed, at every scale tested. Kafka itself is the only backlog buffer; `sceneops-streaming`/`ros2/capture` never buffer messages in Python beyond the single in-flight record `librdkafka`'s own client queue holds.

Reproducible via `scripts/dev/benchmark_streaming_capture_scale.sh MESSAGE_COUNT [PAYLOAD_BYTES]` (real Kafka, real MCAP capture, no mocking):

| Message count | Payload bytes | Produce duration | Produce rate | Capture duration | Capture rate | Kafka lag after produce | Kafka lag after capture | MCAP size | Peak RSS (capture process) |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 3,000 | 64 | 0.32s | 10,386 msg/s | 3.47s | 864 msg/s | 3,000 | 0 | 342,506 B | 59.4 MB |
| 30,000 | 64 | 3.30s | 9,106 msg/s | 6.68s | 4,491 msg/s | 30,000 | 0 | 3,339,952 B | 81.2 MB |
| 100,000 | 64 | 11.11s | 9,002 msg/s | 16.06s | 6,226 msg/s | 100,000 | 0 | 11,111,278 B | 137.2 MB |

Produce rate is roughly constant (~9–10k msg/s, single-partition, single-producer, no batching tuning) across scales — expected, since produce cost is per-message and dominated by client-side serialization/queueing, not backlog depth. **Capture throughput increases with scale** (864 → 4,491 → 6,226 msg/s) as MCAP-writer/consumer-loop fixed overhead amortizes over more messages — consistent with capture being closer to per-message-constant-plus-fixed-startup-cost than the other way around. Peak RSS grows sub-linearly with message count (59→81→137 MB across a 33x message-count increase), not flat — some retained state scales with volume (likely `rosbag2_py`/MCAP writer internals, not `_SequenceTracker`, which is deliberately O(1)); still comfortably small at these scales, no memory concern observed up to 100k messages / 11MB.

CPU was not independently profiled (out of scope for "easy to measure" — wall-clock duration is the primary signal collected here).

## 7. Large payloads

| Payload size | Result |
|---|---|
| 64 B – 500,000 B | Publishes and captures cleanly (500,000 B x 50 messages: produce 360 msg/s, capture 15.2 msg/s, 25 MB MCAP) |
| 999,000 B | Publishes and captures cleanly |
| 1,000,000 B | **Fails immediately** — `producer.publish()` raises synchronously: `cimpl.KafkaException: KafkaError{code=MSG_SIZE_TOO_LARGE,...}` |

**Practical current limit: ~999 KB per message payload.** The failure is a clean, synchronous, fail-fast client-side rejection (librdkafka's own default `message.max.bytes`, ~1 MiB) — never a silent drop, never deferred to `flush()`. No Kafka broker/client size limit was raised or reconfigured in this phase; if larger sensor payloads (camera/LiDAR frames) are needed later, `message.max.bytes` (both broker and client `StreamingSettings`) would need explicit, deliberate reconfiguration — out of scope here, flagged as a Phase 6.7+ consideration, not silently changed.

## 8. Artifact materialization at scale

Real 11,111,278-byte (11.1 MB, 100,000-message) MCAP, registered via `sceneops-worker robots register-capture` against real Postgres+MinIO, then materialized back via `materialize_recording()`:

```text
materialize duration = 0.147s
size_bytes            = 11,111,278 (exact match to source)
temp dir disk usage   = 11M (matches file size, no overhead)
exists during use     = True
exists after cleanup  = False (file and parent temp dir both removed)
```

Registration itself (upload + checksum verification + DB write) and an
exact-retry attempt (`created=False`) both completed correctly at this
scale — no code path in either registration or materialization branches
on file size, so this is confirmatory, not a new mechanism.

## 9. Canonical registration reliability (re-verified at scale)

Phase 6.4's full idempotency/conflict/concurrency suite
(`apps/worker/tests/robots/test_registration_integration.py`, real
Postgres+MinIO) re-run unchanged in this phase — all cases (exact retry,
same-run-id-different-checksum conflict, orphaned-object reuse,
existing-object-different-checksum conflict, concurrent identical
registration) still pass. The concurrent-registration case additionally
re-confirmed manually against the 11.1 MB fixture (§8) via a direct
exact-retry call. No distributed locking was introduced — convergence
still relies entirely on `robots.robot_id`/`artifacts.artifact_id`/
`robot_runs.run_id` being real Postgres primary keys.

## 10. Episode / Learning retry

See matrix row F (§1). `apps/worker/tests/robots/test_episode_retry_integration.py`
(real Postgres+MinIO): a controlled failure injected inside
`RosbagAdapter.extract_episode_source` (i.e., *after* materialization
produced and validated a local temp file) leaves the canonical MCAP
checksum unchanged, cleans up the temp file, writes zero Episode-domain
artifacts, and a plain retry (re-invoking `BuildEpisodesJobHandler`,
the existing job semantics — no new retry mechanism) succeeds and
writes exactly the expected artifact count, no duplicates.

## 11. Mission-boundary segmentation: decision

**Option A chosen**: `whole_run` is the supported Episode segmentation
strategy for a Kafka-captured `RobotRun`. `mission_boundary` remains
supported only where every channel shares a compatible recording
timeline (i.e., a direct `ros2 bag record` capture, where the recorder
stamps every channel with its own uniform receipt time).

No Option B (a separate replay/capture timeline representation) was
built — introducing one risks corrupting the CAN channels' real,
already-verified source-timestamp semantics (Phase 6.2.1's frozen
contract) for no benefit `whole_run` doesn't already provide.

`BuildEpisodesJobHandler` now fails loudly
(`UnsupportedSegmentationError`) instead of silently returning zero
Episodes when `mission_boundary` is requested and Missions are present
but never overlap any frame/state timestamp — see
`docs/workflows/robot-run-and-mcap.md` §6 and
`apps/worker/tests/jobs/test_build_episodes_segmentation_validation.py`.
The legitimate "no Missions at all → `whole_run` fallback" behavior is
unchanged.

## 12. Regression

`make lint`, `make test` (1419 passed, 14 skipped), `make test-integration`
(65 passed), `make smoke-streaming` (39/39), `make e2e-ros2-streaming`
(34/34), `make e2e-streaming-capture` (8/8), `make e2e-robot-run-registration`
(6 stages), `make e2e-robot-run-learning` (11 stages) — all pass on this
commit. Canonical baseline `sceneops-canonical/v0.0` confirmed unchanged
(10 scenes / 10 episodes) before and after every test/benchmark in this
document.

## 13. Known limits carried forward

- ~~Multi-RobotRun concurrent/overlapping capture is not isolated under
  the shared, frozen `CAPTURE_CONSUMER_GROUP_ID` (§4)~~ — **fixed in
  Phase 6.6.1, see §14 below.**
- Practical message-payload ceiling ~999 KB (§7) — camera/LiDAR-scale
  payloads need deliberate Kafka reconfiguration, not attempted here.
- Peak RSS grows with message count, not flat (§6) — not concerning at
  100k messages, unmeasured beyond that.
- Crash-leftover temp files rely entirely on container-lifecycle
  ephemerality (§1 row G) — untested under an actual `SIGKILL`/container
  crash, only simulated via an intentionally-orphaned context manager.
- This entire baseline is single-broker, single-partition,
  single-machine — no multi-broker, multi-partition, or network-
  partition scenarios were exercised.

## 14. Addendum: run-scoped capture consumer groups (Phase 6.6.1)

Follow-up to §4's finding, same day. `CAPTURE_CONSUMER_GROUP_ID` is now
a **capture consumer-group base**, not the literal Kafka `group.id` a
capture attempt uses — every capture derives its own group from the
base plus its `robot_run_id` (`ros2/capture/group_id.py`,
`derive_capture_group_id`), so independent RobotRuns never share
committed-offset state, even when interleaved on the same (still
single-) partition.

**Re-verified against real Kafka**
(`ros2/capture/tests/test_multi_robot_run_integration.py::test_sequential_independent_captures_each_see_complete_sequence`):
the exact scenario §4 documented as failing — run A's capture reading
past run B's interleaved messages to reach its own target count — no
longer causes run B's later, independent capture to miss its early
messages. Both runs now see their own complete `0..N-1` sequence and
write only their own messages, regardless of processing order.

**Historical-rescan tradeoff, measured directly.** Consumer-group
isolation is not partition scalability — this fix trades correctness
for repeated historical rescanning. Every run-scoped group starts with
no committed offset, so `auto.offset.reset=earliest` means a brand-new
group may scan the ENTIRE accumulated topic before reaching its own
messages. Directly observed: the same 3,000-message capture benchmark
(§6) that took 3.47s against a lightly-used topic took **43.9s** once
the local dev topic had accumulated roughly 150,000 prior messages from
this document's own earlier benchmark runs — over 12x slower, entirely
attributable to the rescan, not to capture logic itself (Kafka lag
still reached exactly `0` at the end; correctness held, only latency
changed). This is the expected, accepted cost of per-run isolation on a
single, ever-growing topic — not a regression, and not fixed here (no
partitions added, no topic redesign attempted, matching this follow-up's
explicit scope).

**Practical implication:** local dev/CI topics that accumulate a lot of
history across repeated test runs will make future run-scoped captures
progressively slower to start seeing their own data. A production
deployment would want either topic retention tuned to bound this, or
enough partitions that a fresh group's `earliest` scan stays cheap
(neither implemented here).
