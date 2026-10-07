# ADR-010: Job ownership lease and fencing

## Status

Accepted — implemented. Extends [ADR-009](./009-job-centric-execution-and-durable-boundaries.md),
whose consequences listed worker loss as unrecovered. The contract is in
[Jobs and pipelines](../architecture/jobs-and-pipelines.md) §5 ("Ownership"); the
experiments behind this decision are in
[Job worker liveness study](../history/job-worker-liveness-study.md).

## Context

Job state transitions were already safe against stale actors: one in-flight Job per
execution key, an atomic claim, compare-and-set dispatch, and worker writes
conditional on `status = RUNNING AND worker_id = <this worker>`. They were not live:

- A worker that died after its claim left the Job `RUNNING` forever, and its
  PipelineRun `RUNNING` with it. `heartbeat_at` was written at claim and completion
  only, and nothing inspected it except the `REGISTER_ROBOT_RUN` reconciler.
- Celery (`task_acks_late`, `task_reject_on_worker_lost`, Redis transport) does
  redeliver the message of a lost worker — at once when a pool child dies, after the
  visibility timeout (1 h) when the whole worker dies — but the redelivery finds the
  Job `RUNNING` and is refused, then acknowledged. The broker's one retry is spent on a
  Job nobody will finish.
- `worker_id` is `celery:<task id>`. A redelivery carries the same task id, so it
  identifies the message, not the execution. Once a Job could be made claimable again,
  a redelivery of the original message would claim it under the original owner's
  `worker_id`, and the original owner — if it was only paused — would pass the
  ownership check and overwrite the new claim's state. This was reproduced with real
  Celery workers (the study's Case D).

## Decision

1. **A claim is a generation.** `jobs.lease_generation` is incremented by every claim
   and is the fencing token: every write of a claim (renewal, start, terminal state)
   is conditional on `status = RUNNING AND lease_generation = <its claim>`.
   `worker_id` stays as a diagnostic name.
2. **A claim holds a lease.** `jobs.lease_expires_at = now() + lease` on PostgreSQL's
   clock, set by the claim and extended by renewals. The worker renews every third of
   the lease (60 s by default) from a thread of its own, so synchronous handler work on
   the event loop cannot starve it. A renewal that finds its claim gone cancels the
   handler.
3. **Recovery reclaims expired leases.** A stateless one-shot command
   (`sceneops-worker jobs recover-leases`) moves a `RUNNING` Job whose lease has passed
   back to `QUEUED` and sends a new message — the same Job, so a pipeline task keeps
   waiting on it. After `JOB_CLAIM_BUDGET` (3) claims an expired lease fails the Job
   (`JobLeaseExpired`) and advances its pipeline. Recovery is one conditional UPDATE
   per branch, so concurrent passes and a racing renewal resolve in PostgreSQL.
4. **Expiry is not revocation; the reclaim is.** A claim whose lease passed but was
   not reclaimed may still renew and finish.
5. **Dispatch compare-and-set includes the generation**, because `RUNNING` → `QUEUED`
   returns a Job to an earlier status without touching `retry_count`.

## Alternatives considered

- **Heartbeat timestamp only, a sweeper fails stale Jobs.** Detects death, but the
  sweeper's threshold and the worker's heartbeat cadence live in two places, and a stale
  owner is fenced only by `worker_id` — which a redelivery repeats. Rejected as the
  ownership mechanism; the heartbeat remains as evidence.
- **Lease without a fencing token.** Bounds how long a dead worker holds a Job, but
  cannot stop a paused worker that wakes after the takeover: expiry is a statement about
  time, and the old owner's clock and the database's disagree exactly when it matters.
- **Delegate to Celery redelivery** (visibility timeout, `acks_late`). The broker knows
  whether a message was acknowledged, not whether the Job's state is durable; its
  redelivery is refused by the claim, takes an hour when a whole worker dies, needs
  another live worker to happen at all, and carries the old identity. It remains a
  useful second path, never the authority.
- **Replace the Job instead of requeueing it.** The registration reconciler does this
  for its own reasons (a logical registration and its attempt budget span Jobs). For a
  generic Job it would orphan the PipelineTaskRun's `job_id` and duplicate history;
  requeueing keeps one Job per task attempt and lets the generation tell claims apart.
- **Reuse `retry_count` for lost workers.** It counts explicit retries after failures,
  bounded by `max_retries` (0 for pipeline tasks); a lost worker is not a failure of the
  Job, and pipeline tasks would never be recovered.

## Consequences

- A dead, killed or unreachable worker's Job runs again within one lease plus one
  recovery interval, while the recovery loop runs (`make recovery-up` locally; any
  scheduler or CronJob in deployment).
- One extra conditional UPDATE per running Job every 20 s by default.
- A worker paused or cut off from PostgreSQL for longer than the lease loses its Job
  although it is alive (a false positive); it is fenced and the Job runs again.
  Correctness of the duplicate execution rests on idempotent domain writes
  (write-once, content-pinned artifacts; convergent registration): fencing covers Job
  rows, not objects or domain records a handler already wrote.
- A lease proves the process is alive, not that the handler progresses: a hung
  handler in a live process keeps its Job.
- A Job reclaimed but whose new message is lost stays `QUEUED`; resending lost
  dispatches is not part of this decision.
