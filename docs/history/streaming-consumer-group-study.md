# Consumer-group state of a pinned Capture

Point-in-time verification record (documentation category C). The current contract is in
[Streaming transport](../architecture/streaming-transport.md) §21 and §26; the decision is
[ADR-013](../adr/013-capture-records-no-kafka-progress.md).

| | |
| --- | --- |
| Date | 2026-10-09 / 2026-10-10 |
| Branch / base HEAD | `dev` / `252a1a4` (working tree with the change uncommitted) |
| Environment | macOS host, Docker Compose; `apache/kafka:3.9.2` single-node KRaft (defaults: `offsets.retention.minutes=10080`, 50-partition `__consumer_offsets`); `capture` image with `confluent-kafka` 2.16.0 / librdkafka 2.16.0; one scratch broker for the broker-recreation and Kafka-down cases |
| Question | Does Kafka consumer-group state still provide a necessary correctness or operational guarantee for a pinned (`assign()`) Capture whose per-run group receives `RUN_END + 1` after finalize? |

## Method

A harness in the capture container published runs (3-partition topics, other runs' records
before and after) and captured them as separate OS processes. Crash points were injected by
`os._exit(137)`: after the third record, after `RUN_END` before validate, and right after
`finalize_bag`. Variants: the code as it was (commit on), the same code with `commit()`
replaced by a no-op, and (after the change) the new code. Every recovery was compared with an
independent clean capture of the same run by `same_recorded_content` (all but `log_time`).

## Results

Recovery, 7 scenarios per variant (normal run; crash mid-run, after `RUN_END`, after finalize,
each followed by a retry from the same start position; retry of a finalized run; all capture
groups deleted, then re-capture with the recording also removed; mid-run crash, groups deleted,
retry): all converged on the same recording, with and without the commit, with the group
present or deleted. After a crash the committed offset was unset in every case (the commit
only follows finalize), and no retry consulted it: a retry of a finalized run with a committed
offset past `RUN_END` re-read the run from its start position. The new code gave the same
outcomes for normal, three crash points, and the no-hint scan (all partitions assigned from
the beginning).

| Case | Result |
| --- | --- |
| Commit fails after finalize (injected transport error) | old code: exit 1 although the bag and receipt were valid and complete; retry converged |
| Kafka stopped right after finalize (new code, scratch broker) | exit 0, bag and receipt intact |
| Group state lost with topic data kept (scratch broker: `__consumer_offsets` removed, broker restarted) | group list empty, topic offsets intact; re-capture from the same position succeeded |
| `DeleteRecords` on `__consumer_offsets` | refused by the broker (`INVALID_TOPIC_EXCEPTION`) |
| `Consumer` without `group.id` | `ValueError: group.id must be set`, even for `assign()` only |
| `assign()` consumer, `enable.auto.commit=false`, never commits | group never listed, not while assigned and not after close |
| `subscribe()` consumer, never commits | group listed (`Empty`) after close |

Operational value: for a completed run the group's `CURRENT-OFFSET` was `RUN_END + 1` and
`LAG` was 480 after 480 records of unrelated runs were appended to the partition (a stale
group from an old test run showed lag 73 on the default topic). While an assigned consumer
runs, `kafka-consumer-groups --describe` has nothing to describe.

Accumulation, 100 sequential pinned captures of 5-record runs on one topic (per capture:
median 0.07 s, p95 1.08 s, equal with and without the commit): the commit variant added 100
groups and 20.5 KB to the `__consumer_offsets` logs (~205 B per group); the no-commit variant
added 0 groups and 0 bytes. The dev broker held 64 `Empty` capture groups from earlier test
runs before the study. This is small broker overhead; the finding is that the state has no
semantic use, not that it is costly.

## Limits

One single-node broker, runs of 3-6 telemetry records (accumulation: 5), local filesystem.
Rebalance, multi-broker group coordination and retention expiry of committed offsets were not
exercised; they only matter to consumers that use the group. The scratch-broker state loss
removed `__consumer_offsets` directories, not a full broker disk loss. Topic data loss
(retention) is a different failure and was not part of this question.

Side effect to note: the "delete all capture groups" scenario removed the dev broker's
64 pre-existing empty `sceneops-mcap-capture-*` groups.
