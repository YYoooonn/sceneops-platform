# Producer queue saturation and recovery

Point-in-time verification record (documentation category C). The current contracts are
[Streaming transport](../architecture/streaming-transport.md) §4 (delivery, queue full) and §14
(backpressure boundary, shutdown, failure semantics); nothing here replaces them.

| | |
| --- | --- |
| Date | 2026-10-10 |
| Branch / HEAD | `dev` / `dcb7137` plus the uncommitted change this study produced (and the uncommitted backpressure-study files) |
| Environment | macOS host; Docker Desktop VM, 8 vCPU, 7.65 GiB; `apache/kafka:3.9.2` single-node KRaft, one partition, `LogAppendTime`; host `confluent-kafka` 2.15.1, ROS images 2.16.0 |
| Question | What should the producer and the bridge do when librdkafka's local queue is full, and how do they recover or fail without silently losing telemetry? |

The broker was always a throwaway container frozen with `docker pause` (the TCP connections stay open and the
broker answers nothing) and thawed with `docker unpause`; the shared compose broker was not touched. Queues were
cut to 300–2,000 messages, records are ~100 B (producer level) and small `Odometry` messages (bridge level). The
mechanism does not depend on the limit.

## 1. Root cause

```text
broker unreachable → records pile up in librdkafka's queue → queue reaches its bound
→ produce() raises BufferError                       (before the poll(0) that follows it)
→ delivery reports are never served                  (only poll()/flush() serve them; produce() never does)
→ queue slots are never released                     (a slot is freed when its report is served, not when the broker answers)
→ broker returns, records are delivered, reports wait in the reply queue
→ every later produce() still raises BufferError     (queue full of already-delivered records)
→ final flush() serves the reports → returns success
```

Reproduced at producer level (300–1,000-message queue): 2,000 `BufferError`s while frozen; after the unfreeze all 200
further publishes over 5 s still raised it, `len(producer)` stayed 1,002; `flush()` then drained it in 0 s and returned
success. A committed regression test fails on this code with "producer is wedged" after 30 s of recovery attempts
(negative control: the earlier `_produce_sync` put back, `BufferError` accepted as a refusal).

## 2. Client semantics (measured, `confluent-kafka` 2.15.1)

| Question | Result |
| --- | --- |
| Does `produce()` serve delivery callbacks? | No: 10 records stored 2 s earlier, 0 callbacks after another `produce()`; `poll(0)` ran all 10 |
| Does a healthy broker keep the queue from filling? | No: 1,000 records all stored, no `poll()`: `len` 1,001, next `produce()` raised `BufferError: Local: Queue full` |
| Does `flush()` serve callbacks? | Yes (it drained a wedged queue) |
| Does `poll(0)` free the slots? | Yes: `len` 1,001 → 0, next `produce()` accepted |
| Retry `produce()` immediately in a loop | 2,887,318 `BufferError`s in 2.0 s, 99 % of a core |
| Blocking `poll(t)` while full | idle: a waiting publish cost 0.39 s CPU per 2 s against 0.38 s for a producer doing nothing (librdkafka's own threads against a frozen broker) |
| `BufferError` atomic? | Yes: nothing is queued, so a retry cannot duplicate or reorder |

## 3. Strategies

Outage 4 s at 2,000 rec/s into a 1,000-record queue, then 8 s more; all records read back from the broker.

| Strategy | Normal path | Recovery after unfreeze | Notes |
| --- | --- | --- | --- |
| earlier code: `produce(); poll(0)` | 1.11 µs/record | never (stuck) | |
| D: no polling, fail on `BufferError` | 0.72 µs (the 100,000-message queue filled) | never | delivered-but-unserved records pin the queue |
| A: `poll(0); produce()`, fail on `BufferError` | 0.98 µs | first accept 0.05 s | fails every record while the queue is full |
| B: `produce()`, on `BufferError` `poll(W)` + retry | no poll when not full | first accept 0.05 s | reports are served only once full: delivered records hold queue memory at the bound, delivery errors surface late |
| C: A + bounded `poll(W)` retry (chosen) | 0.98 µs | first accept 0.05 s | absorbs an outage shorter than `W`; blocking, not spinning |

Ordering was preserved and no accepted record was missing or duplicated in any strategy that recovered. With a wait,
the offered load falls to ~1 record per `W` while blocked (the source cannot be slowed; the real source keeps
producing), so loss counts between A and C are not comparable. What C changes is only an outage shorter than `W` while the queue is
full, which it absorbs; against a longer one both lose records.

## 4. Chosen policy

`publish` = `poll(0)`; `produce()`; on `BufferError` `poll(remaining)` until a slot frees or `queue_full_timeout_seconds`
(default 1.0 s) expires, then `ProducerQueueFullError`. After one record exhausted the wait, later records are refused
at once until one is accepted: the stream already has a gap, and without this a finite replay against a dead broker would
wait one timeout per remaining record. The bridge sets the wait to half of `--publish-timeout-seconds`, so the
producer's verdict arrives before the bridge stops waiting for it and a record is never queued after its caller was told
it failed. `publish` means "accepted into librdkafka's queue" or an exception; delivery failures after that surface in
`flush`; `publish_and_wait` is unchanged.

## 5. Sequence and RUN_END correctness

The bridge assigns a telemetry sequence number before it calls the producer and counts a failed message
(`failed_count`); `RUN_END.telemetry_count` is the number of sequence numbers assigned, failures included. A refused
record is therefore a gap (Capture: `SequenceIntegrityError`) or, at the tail, a count mismatch
(`RunIncompleteError`); `flush()` succeeding proves only that accepted records were delivered and cannot hide it. No new
producer state was needed: `published_count == telemetry_count` is the "all attempted records accepted" predicate and
already exists in the bridge. Capture's side is covered by `test_lifecycle_integration.py` (missing tail, missing
middle) and by the bridge runs below. RUN_END is still published after a clean flush even when records were refused
(Capture then fails with a specific error rather than waiting for its idle guard) and withheld when the flush fails.

## 6. Bridge outage runs

Real bridge `main()` (queue 2,000 messages, `--exit-after-idle-seconds 5`, `--publish-timeout-seconds 5`), 6,000–8,000
`Odometry` at 1,000/s over DDS, a pinned Capture afterwards. Tool: `experiment_producer_outage.py`. "old" = earlier
`_produce_sync` / earlier exit decision restored on the same image.

| Scenario | Outage | Bridge exit / time | Accepted / refused | On broker | RUN_END | Capture |
| --- | --- | --- | --- | --- | --- | --- |
| healthy | – | 0 / 11.4 s | 6,000 / 0 | 6,000, no holes | count 6,000 | finalized |
| short | 1 s (queue not full) | 0 / 11.4 s | 6,000 / 0 | 6,000, no holes | count 6,000 | finalized |
| absorbed | 3 s (by rate the queue fills ~1 s before the thaw; the wait is 2.5 s) | 0 / 11.5 s | 6,000 / 0 | 6,000, no holes | count 6,000 | finalized |
| medium | 7.5 s | 1 / 14.6 s | 4,135 / 3,865 | 4,135, one hole (3,572–7,436) | count 8,000 | `SequenceIntegrityError` |
| medium, old producer | 7.5 s | 1 / 13.4 s | 3,561 / 4,439 | 3,561 (tail lost, producer stuck after the broker returned) | count 8,000 | `RunIncompleteError` (8,000 declared, 3,565 observed) |
| never | forever | 1 / 35.6 s | 3,580 / 4,420 | 1,587 (the rest died with the process) | withheld | `RunEndNotObservedError` |
| never, old producer | forever | 1 / 33.7 s | 3,569 / 4,431 | 1,575 | withheld | `RunEndNotObservedError` |
| never, SIGTERM at 15 s | forever | 1 / 34.7 s | 3,743 / 4,257 | 1,749 | withheld | `RunEndNotObservedError` |
| tail stall | 23.5 s, queue never full | 1 / 25.4 s | 8,000 / 0 | 8,000, no holes | withheld | `RunEndNotObservedError` |
| tail stall, old exit decision | same | **0** / 25.3 s | 8,000 / 0 | 8,000, no holes | withheld | `RunEndNotObservedError` |

Shutdown of a run that never recovers is bounded by the first flush (10 s), `close()`'s second flush (10 s) and the idle
or signal trigger: SIGTERM at 15 s ended the process at 34.7 s.

**Second finding.** The telemetry flush timed out (RUN_END withheld) while the broker returned during `close()`'s second
flush, which succeeded because a delivery error is reported only once: the earlier bridge exited 0 with all telemetry on
the broker and no RUN_END, so an orchestrator checking only the bridge's exit status (the baseline scripts do) saw
success. `StreamingBridgeNode.delivery_unconfirmed` (a failed flush or close) now makes `main()` exit non-zero, and a failing
`close()` no longer skips the summary line and `destroy_node`.

## 7. Changes and tests

`producer.py` (poll before produce, bounded retry, `ProducerQueueFullError`, `queue_full_timeout_seconds`),
`errors.py`, `node.py` (wait = half the publish timeout; `delivery_unconfirmed`, `shutdown` records close failures,
`main` exit status). Tests: `test_producer.py` (5 new, fake client with a queue bound), `test_producer_overload_integration.py`
(4, real frozen broker: recovery with exact order and no duplicates, outage shorter than the wait, no spinning and one wait
per episode, a broker that never returns; `make streaming-producer-test`, part of `SUITE=kafka`),
`test_streaming_bridge_node.py` (5 new: a refused record is a failure with a gap and a counted RUN_END, unconfirmed
delivery on a failed flush and a failed close, sticky after a later successful close, clean shutdown unaffected).
`make streaming-test`: bridge 60, capture 183, `tests/streaming` 6 passed; `packages/sceneops-streaming/tests`: 141 passed,
4 skipped (the broker-freeze module, off without `SCENEOPS_BROKER_FAULT_TESTS=1`).

## 8. Limitations

- Each scenario was run once (the healthy and short runs agree with each other; the counts of refused records vary by
  a few hundred between repeated runs of the same scenario, e.g. 3,741 / 3,865 refused for `medium`).
- A hole means the run fails: there is no resume, no application-level flow control, and the source cannot be slowed. An
  outage that fills the queue (1 GiB / 100,000 messages in production; 44.7 s at 25 MB/s in the backpressure study) plus the
  wait fails the run; only an outage shorter than that is absorbed.
- Records accepted but not yet delivered when the process exits are lost with it (`never`: 1,587 of 3,580 on the broker).
- A permanently dead broker costs two 10 s flush timeouts at shutdown.
- `publish_and_wait` (RUN_START) does not use the bounded wait; its queue is empty at that point.
- One host, one broker, replication factor 1; `docker pause` models an unresponsive broker, not a network partition or a
  broker restart.

Reproduce: `make streaming-producer-test`; `.venv/bin/python tools/benchmarks/streaming/experiment_producer_outage.py
{healthy|short|absorbed|medium|never|never-term|tail-stall} [--old-producer] [--old-exit]` (after
`make streaming-ros-build`).
