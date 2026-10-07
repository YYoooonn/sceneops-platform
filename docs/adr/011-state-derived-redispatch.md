# ADR-011: State-derived redispatch of lost messages

## Status

Accepted — implemented. Extends [ADR-009](./009-job-centric-execution-and-durable-boundaries.md)
(durable state first, message second) and
[ADR-010](./010-job-ownership-lease-and-fencing.md) (whose recovery could lose its own
send). The contract is in [Jobs and pipelines](../architecture/jobs-and-pipelines.md)
§5; the experiments are in [Lost dispatch study](../history/lost-dispatch-study.md).

## Context

Every asynchronous step commits PostgreSQL state and then calls `Celery.send_task`.
PostgreSQL and Redis share no transaction, so the two writes can disagree:

- **commit, then send** (what SceneOps does): the commit can succeed and the send
  fail, or the process can die between them, or the broker can accept the message
  and lose it before persisting it. Durable state then waits on a message that will
  never arrive.
- **send, then commit** is worse: a worker can receive work that names a row that is
  not committed yet, or never will be; it would act on state that does not exist.

Four sends do this: the API's Job and PipelineRun dispatch, the orchestrator's
submission of a task's Job, `JobRunner`'s `advance` after a Job's terminal commit, and
lease recovery's requeue. Before this decision a lost send in any of them left a Job
`QUEUED` or a run waiting forever; only a person (`POST /jobs/{id}/execute`,
`make worker-advance-pipeline`) or, for registration only, the acquisition reconciler
after 900 s, moved it again. Reproduced on real infrastructure for every boundary.

The messages carry an identifier and nothing else: `run_job(job_id)`,
`advance(pipeline_run_id)`. They are notifications that state is ready, not commands
carrying information of their own, and every consumer re-reads PostgreSQL and is
idempotent (atomic claim, compare-and-set transitions, generation fencing, a row-locked
orchestration step).

## Decision

1. **Derive lost wake-ups from durable state.** A `QUEUED` Job waits on `run_job`; a
   `QUEUED` PipelineRun, or a `RUNNING` one whose `RUNNING` task's Job is terminal (or
   missing), waits on `advance`. These are all the states that need a message: every
   dispatch path leaves its Job `QUEUED`, and after every committed orchestration step a
   run is terminal or waits on exactly one in-flight Job.
2. **Re-send after a threshold.** One recovery pass (`sceneops-worker recover`: lease
   sweep, `run_job` sweep, `advance` sweep) re-sends a message to state that has waited
   longer than `--resend-after-seconds` (300 s) since its last dispatch (`jobs.queued_at`,
   `pipeline_runs.updated_at`).
3. **One sender per resend, no lock during the send.** A pass moves that timestamp to
   now with a conditional `UPDATE` on the value it read and commits, then sends. Of
   concurrent passes exactly one wins; the timestamp also starts the next interval.
4. **Completion is a state change.** A dispatch is complete when the Job is claimed or
   the run steps. Nothing records that a message was delivered.
5. **No outbox.** No dispatch-intent table, no publisher process.

## Alternatives considered

- **Retry `send_task` inline.** Kombu already retries a publish briefly (the API's
  failed dispatch raised after 0.67 s). Longer retries add request latency, and no
  in-process retry survives the process dying between commit and send.
- **Transactional outbox.** An outbox row committed with the state change, published
  by a separate process, marked delivered. It closes the same windows, but here the
  outbox row would duplicate what the state row already says (`QUEUED` *is* the intent),
  needs its own lifecycle, retention and publisher, and still delivers at least once:
  the publisher can die after the send and before marking the row. It is the right
  design for messages carrying information that cannot be re-derived later (domain
  events for external consumers); SceneOps has none.
- **Broker-side guarantees** (publisher confirms, `acks_late`, result backend). They
  report what the broker holds, not whether PostgreSQL's state is matched by a
  message. Redis has no publisher confirms, and an accepted message is lost if Redis
  dies before persisting it (reproduced). `acks_late` protects consumption, not
  publication.
- **Distributed transaction (2PC).** Redis/Celery cannot take part in an XA
  transaction, and 2PC would couple PostgreSQL's availability to the broker's.

## Consequences

- Any committed state that needs a message gets one within the resend threshold plus
  the pass interval, as long as PostgreSQL, the broker and the recovery loop are
  eventually available.
- Delivery is at least once, never exactly once. A message still waiting behind a
  backlog is indistinguishable from a lost one and is sent again once per threshold;
  each duplicate costs a worker one refused claim or one no-op step.
- Recovery reads three indexed queries per pass when nothing is due and writes only
  when it re-sends (one conditional `UPDATE`, plus a JobEvent and an ExecutionRecord
  for a Job).
- `jobs.queued_at` means "last dispatch" and `pipeline_runs.updated_at` doubles as
  "last advance requested".
- A future message that carries information PostgreSQL does not hold cannot be
  recovered this way; it would need an outbox.
