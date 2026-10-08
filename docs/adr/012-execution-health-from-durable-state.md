# ADR-012: Execution health is derived from durable state

## Status

Accepted — implemented. Builds on [ADR-009](./009-job-centric-execution-and-durable-boundaries.md),
[ADR-010](./010-job-ownership-lease-and-fencing.md) and
[ADR-011](./011-state-derived-redispatch.md), whose durable state it reads. The contract
is in [Jobs and pipelines](../architecture/jobs-and-pipelines.md) §5 ("Execution
health"); the experiments are in
[Execution observability study](../history/execution-observability-study.md).

## Context

The execution system recovers lost workers and lost messages on its own, so failures no
longer stop work: they make it slow. An operator needs to tell, without injecting a
fault or reading rows by hand, whether Jobs wait longer than normal, run longer than
normal, fail, lose workers, need resends, or pile up, and where a pipeline's time goes.

Most of that is already in PostgreSQL: every Job transition is one conditional UPDATE
with a timestamp, every claim, lease reclaim and resend writes a JobEvent, every task
run links its Job. Two things kept the obvious queries from being right:

- `jobs.queued_at` is the last dispatch, and execution recovery moves it on every
  resend (ADR-011 needs it as the resend threshold and compare-and-set value). Measured
  from it, queue time and the age of the oldest queued Job stop at the resend
  threshold: in a backlog whose true p95 queue wait was 40 s, the derived p95 was 9.8 s
  and the oldest queued Job never looked older than 11 s.
- `worker_id` names the Celery message, so a reclaim could not be attributed to the
  worker that died; and a refused duplicate claim, the expected cost of at-least-once
  delivery, was logged as an ERROR traceback (969 in one backlog run with no failure).

There is no monitoring backend, and adding one does not answer these questions by
itself: an exporter would publish the same wrong number.

## Decision

1. **Derive, do not record.** Execution health is read-only SQL over jobs, job_events,
   pipeline_runs and pipeline_task_runs (`sceneops_db/postgres/execution_metrics.py`),
   exposed as `sceneops-worker execution-status` (one JSON snapshot, plus the broker's
   queue depths). No metric row, counter table or periodic snapshot is written.
2. **Record a fact only where a transition already writes.** `jobs.enqueued_at` is set
   by the UPDATE that moves a Job into `QUEUED` from another status and kept by
   redispatches and resends; the `LOCKED` JobEvent that every claim already writes
   carries the claim's generation and the claiming process (Celery node name and
   pid). Neither adds a write.
3. **Queries cost what they read now, not what has happened.** Current-state readings
   go through the status indexes (in-flight rows only); window readings go through
   partial `finished_at` indexes on jobs and pipeline_runs.
4. **A refused claim is a warning, not a failure.** `JobRunner` raises
   `JobNotClaimableError`; the Celery task logs one `job claim refused` line and
   returns `not_claimed`.
5. **No monitoring backend yet.** Exporting is deferred until something must watch the
   numbers continuously (an alert); the readings are grouped below for that day.

| Class | Readings | Why |
| --- | --- | --- |
| Export continuously (alerts, dashboards) | oldest queued age and `QUEUED` count by type; `RUNNING` count; heartbeat age and expired leases; failures by type and error; finished per minute; queue-wait and execution p95 by type; lease reclaims and resends per window; runs `job_finished_awaiting_advance` with their oldest wait; broker depth | change within seconds, low cardinality (19 Job types, 4 pipeline types), each answers an alertable question |
| Query on demand | per-task pipeline stage breakdown; p99 / max; end-to-end by type; reclaims by worker; long windows | diagnosis after an alert; seconds of SQL are acceptable |
| Debug only (events and logs) | per-Job timeline, per-claim events, error messages, refused-claim lines, recovery pass summaries | high cardinality (Job ids, worker processes, messages); never a metric label |

## Consequences

- Queue time is measured from `enqueued_at`; it describes the wait the finishing claim
  ended. Time lost to a dead worker appears in end to end and in "claimed more than
  once", not in queue wait or execution.
- `jobs.queued_at` keeps its ADR-011 meaning ("last dispatch"). Two timestamps exist
  because they answer two questions: when to resend, and how long the work has waited.
- The health of the system is only as observable as its durable state: a re-sent
  `advance` and a refused claim leave no row, worker capacity is not recorded, and
  nothing samples the snapshot. Those are the first candidates for an exporter.
- A future Prometheus exporter (or OpenTelemetry metrics) can serve the first class by
  running these queries on scrape (one full snapshot measured at 9.5 ms over 200 000
  historical Jobs) and add the in-process signals the database cannot hold (refused
  claims, send latency, pool wait) as counters and histograms. Job ids, run ids and
  worker processes stay out of its labels. Tracing would cover the inside of a handler, which
  this decision does not measure.
