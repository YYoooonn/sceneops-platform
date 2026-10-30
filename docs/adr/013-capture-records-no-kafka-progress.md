# ADR-013: Capture records no progress in Kafka

## Status

Accepted — implemented. Supersedes the offset-commit parts of
[ADR-007](./007-canonical-ingestion-architecture.md) §29.14 and
[ADR-008](./008-acquisition-lifecycle-reliability.md) (the "commit after finalize"
ordering, and the crash window "after finalize, before offset commit"). The contract is in
[Streaming transport](../architecture/streaming-transport.md) §21 and §26; the experiments
are in [Streaming consumer-group study](../history/streaming-consumer-group-study.md).

## Context

Capture read one RobotRun from Kafka through a per-run consumer group and committed
`RUN_END + 1` to it after the MCAP was durably finalized. Once the start position came
from the acknowledged `RUN_START` and was `assign()`ed, nothing read that committed offset
any more:

- it was not the start position (a group resuming past a completed run finds none of it);
- it was not recovery state (a crashed attempt replays from its start position and
  converges on the finalized bag or rebuilds the partial one);
- it was not ownership (a local per-run lock and an explicit partition assignment);
- it did not decide that a run was captured (the finalized bag and its receipt do).

What it left was broker state: one group per captured run, retained for
`offsets.retention.minutes` (7 days), whose lag is the count of records *other* runs
appended to the partition since, and which does not exist at all while a run is being
captured.

## Decision

Capture is an explicitly assigned Kafka reader that records no progress:

- every partition it reads is `assign()`ed (one at the start position, or all of them from
  the beginning when there is none); it never subscribes, joins a group or is rebalanced;
- `enable.auto.commit` is off and `KafkaTelemetryConsumer` has no commit API;
- the `group.id` librdkafka insists on is a constant (`ASSIGNED_READER_GROUP_ID`) that is
  never joined or written to, so Capture creates no consumer group on the broker;
- "this RobotRun has been durably captured" is the atomic finalize of the bag and its
  receipt. Publication and registration are later, separate stages.

## Alternatives considered

- **Keep the commit as a completion marker.** Rejected: a second, weaker record of
  something the finalized bag already states, in non-canonical transport that can be
  deleted or reset without consequence, and a way for Capture to fail after the recording
  is valid (a commit error after finalize).
- **Keep per-run groups for the no-hint scan only.** Rejected: it keeps a group join
  (~3 s) and a rebalance for a path that only needs "every partition from the beginning",
  which `assign()` states directly.

## Consequences

- A crash at any point, and loss of all consumer-group state (including a broker recreated
  over the same topic data), cannot change what is recovered; only loss of topic data can.
- Kafka consumer-group lag says nothing about captures. Capture status is its log, the
  finalized bag and the receipt.
- Anything that wants Kafka-side progress for captures would have to define what the
  progress means first; none does today.
- Retention, backpressure and cross-host ownership are unchanged and still open.
