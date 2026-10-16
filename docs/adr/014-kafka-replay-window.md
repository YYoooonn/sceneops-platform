# ADR-014: The Kafka replay window is defined by time retention

## Status

Accepted — implemented; the local broker is configured accordingly. Closes the "Kafka
retention is not configured" gap recorded in [ADR-008](./008-acquisition-lifecycle-reliability.md)
(B2). The contract is in [Streaming transport](../architecture/streaming-transport.md) §26.1;
the measurements are in [Kafka replay window study](../history/streaming-retention-study.md)
and [Kafka timestamp type study](../history/kafka-timestamp-type-study.md).

## Context

Kafka is non-canonical transport. Its only recovery role is to let a Capture retry rebuild a
run that was not finalized ([ADR-013](./013-capture-records-no-kafka-progress.md)): the
finalized bag and its receipt are the capture, and Publisher and registration never read
Kafka. A run's records must therefore stay readable from `RUN_START` until a capture has
durably finalized, and may disappear afterwards.

The broker had no explicit retention. The topic inherited 7 days of `retention.ms`, 7 days of
`segment.ms` and 1 GiB segments, with no size limit. Measured on Kafka 3.9.2:

- retention removes whole segments from the start of the log, on a 5-minute check; a record
  outlives `retention.ms` by up to the roll interval plus 5 minutes (4.3–4.5 min observed);
  with the inherited 7-day roll that was up to two weeks of disk on a busy topic;
- `retention.bytes` removes the oldest segments whatever their age: 10 MiB against a 3 MiB
  cap lost its first 63% at the next check, 29 s later;
- a streamed 20 s reference run is 471 MB on the topic at 24 MB/s, so any size limit is a
  window of `limit / rate` (20 GiB: about 14 minutes of continuous streaming);
- a pinned Capture whose start offset has expired fails at once and explicitly; one that is
  overtaken while reading fails only after its fetched records are used up, and was
  reported as a malformed record.

## Decision

- **Time defines the guarantee.** `retention.ms` is the replay window: every record is
  readable for at least that long after it was produced. The window is derived, not chosen:
  `D_max + T_detect + T_retry + T_read` (the longest run, the time to notice a failed
  capture, to start the retry, and to read).
- **Size is a host-protection backstop, never the window.** It is sized so that it does not
  bind within the budgeted ingest. Reaching it ends the guarantee early and is an incident,
  not normal operation.
- **Segments roll by time as well as size** (`segment.ms` = 1 h locally), so that a quiet
  topic's records expire within a bounded time after `retention.ms`.
- **Expiry has meaning only relative to durable capture.** Kafka data expiring after the
  finalized bag and receipt exist is normal; before, the run is `REPLAY_EXPIRED` and
  unrecoverable from Kafka. A start position makes that distinguishable from a run not yet
  published (`ReplayExpiredError` against `StartPositionError`); a scan cannot.
- **Age is the broker's append time.** Topics use `message.timestamp.type=LogAppendTime`, set
  explicitly on the broker, so a record's lifetime starts when Kafka accepted it and no
  producer or robot clock can shorten or extend the window. Envelope timestamps are
  unchanged; the Kafka record timestamp is not a SceneOps event time.
- **Local broker:** 24 h window, 1 h roll, 20 GiB per-partition cap. Local fixtures are
  re-streamable from their locked recordings, so a breach there costs a replay, not data.
  Another deployment derives its window from the formula and its own `T_detect` and
  ingest rate; no value is fixed here for it.

## Alternatives considered

- **Size-defined window.** Rejected: the window becomes ingest rate × bytes, so a burst
  shortens it with no change in configuration or any error until a retry fails.
- **Seven days (the inherited default), or longer.** Rejected for the local broker: at
  0.47 GB per fixture it is disk spent on a recovery latency nobody has; a full host disk
  has wedged Docker here before. It is the right answer only if recovery latency is a week.
- **No size cap.** Rejected locally (a full host disk is worse than a lost replay for
  rebuildable data); kept as the preferable choice where the source cannot be replayed and a
  stopped broker is safer than deleted records.
- **`CreateTime` (the producer's clock).** Rejected: a producer clock behind the broker's by
  more than `retention.ms` makes new records expire at the first retention check, and
  nothing in SceneOps enforces clock agreement. It was the broker default, not a choice.
- **Persisting the start position so an expired run is always distinguishable.** Deferred;
  it changes what a start position is (ADR-013 keeps it runtime information).

## Consequences

- A failed capture has to be re-run within the window; nothing does so automatically.
- The window silently ends early if the size cap binds. No signal exists for it yet.
- The broker's clock now decides record age. A broker clock that jumps forward expires
  records early; this is the only clock the window depends on.
- Broker disk is bounded by `rate × (window + roll + 5 min)`, plus one segment of
  granularity, for each replica.
- A capture that is overtaken while reading fails with `ReplayExpiredError`, no longer as a
  decode error.
