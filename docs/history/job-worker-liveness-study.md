# Job worker liveness study

Point-in-time record. The current contract is
[Jobs and pipelines](../architecture/jobs-and-pipelines.md) §5 ("Ownership") and the
decision is [ADR-010](../adr/010-job-ownership-lease-and-fencing.md). Follows the
[Job state transition concurrency study](./job-state-transition-concurrency-study.md).

```text
date         2026-10-07
branch       dev
HEAD         e630773 (before: clean tree; after: uncommitted working tree on top of it)
environment  one macOS 15.3 host; PostgreSQL 16.15 (compose stack), disposable database
             per run; Redis 7.4.9 throwaway container; Celery 5.6.3 / kombu 5.6.2,
             prefork, --concurrency=1, task_acks_late, task_reject_on_worker_lost,
             worker_prefetch_multiplier=1; MinIO disposable bucket
workers      the production worker (celery_app, run_job_task, JobRunner) with one Job
             type served by a probe handler that holds at chosen points and writes one
             write-once object; killed with SIGKILL, paused with SIGSTOP
```

## Question

Concurrency work made Job state transitions safe against stale actors. What happens
to liveness when the worker that owns a `RUNNING` Job disappears, and what does Celery
redelivery contribute?

## Before: no lease (HEAD e630773)

`heartbeat_at` was written at claim and at the terminal write only; ownership was
`WHERE status = RUNNING AND worker_id = <celery:task id>`.

| Case | Fault | Observed |
| --- | --- | --- |
| A | whole worker SIGKILLed right after the claim | Job `running`, `heartbeat_at` = `locked_at`, message in Redis `unacked`. A second worker with the default visibility timeout (3600 s) received nothing in 30 s. A worker started with a 10 s visibility timeout restored the message on startup (40.4 s after the kill): **same task id, same `worker_id`**, claim refused (`Job is already running`), message acknowledged. Job still `running` 64.7 s after the kill, with no message left anywhere. |
| B | pool child SIGKILLed after its artifact write, parent alive | `task_reject_on_worker_lost` requeued the message: redelivered to the new child **0.21 s** after the kill, same task id and `worker_id`, claim refused, acknowledged. Job `running` forever; the dead attempt's object exists. |
| B0 | whole worker SIGKILLed before the claim | Job `queued`. A 10 s-visibility worker started shortly after the kill restored the message 92.2 s after it and the Job **succeeded**: redelivery restores liveness only while the durable state is still claimable. |
| B3 | whole worker SIGKILLed after the terminal commit, before the `advance` message | Job `succeeded`, no `advance` sent. The redelivery 92.0 s later was refused (`already succeeded`) and sent no `advance` either: the run would wait forever. |
| C | owner A paused (SIGSTOP); Job abandoned and retried; worker B claims through a **new** message | B's `worker_id` differs; A resumed and its terminal write was refused (`JobOwnershipLostError`); B succeeded. |
| D | as C, but B claims through the **redelivery of A's own message** | B's `worker_id` equals A's. A resumed and **its `succeeded` was written while B was running**; B's terminal write was then refused. The fence admitted the stale owner. |

The 92 s redeliveries are kombu's restore cadence: `restore_visible` runs every 10th
call of a 10 s timer (~100 s), plus once at worker startup, and only in a live worker;
a message becomes restorable once older than the visibility timeout.

## After: lease + generation + recovery (uncommitted working tree)

Lease 6 s (renewed every 2 s) and an in-process recovery pass every 1 s for the timings
(scratch script, not kept); 2 s leases in the kept suite
(`tests/infrastructure/test_job_lease_recovery.py`).

Whole worker SIGKILLed after its claim, a fresh worker started right after, 5 runs:

| measure | median | min | max |
| --- | --- | --- | --- |
| lease remaining at the kill | 5.93 s | 5.90 s | 5.96 s |
| kill → reclaim (stale detection) | 6.22 s | 6.20 s | 6.83 s |
| lease expiry → reclaim (recovery poll lag) | 0.29 s | 0.27 s | 0.92 s |
| reclaim → new claim | 0.20 s | 0.15 s | 0.23 s |
| kill → `succeeded` | 6.46 s | 6.39 s | 7.10 s |

Every run: 2 messages sent (the original, still unacknowledged in Redis, and
recovery's), 2 claims, 1 successful owner, final `succeeded` at generation 2.

Pool child SIGKILLed in its handler, 3 runs: the immediate redelivery (0.20–0.23 s)
was refused as before; recovery finished the Job 6.90–7.04 s after the kill; 3
messages (original, its redelivery, recovery's), 2 claims, 1 successful owner,
generation 2.

Heartbeat cost: 14 renewals observed in 30 s at a 6 s lease (one UPDATE per third of
the lease); at the 60 s default, one UPDATE per running Job every 20 s.

Kept suites (real infrastructure):

| Check | Result |
| --- | --- |
| Case D again (paused owner, redelivery with the same `worker_id` takes over) | the new claim is generation 2 with A's `worker_id`; A's late writes raise `JobOwnershipLostError`; B succeeds |
| handler blocking the event loop (`time.sleep`) for 3 leases | lease renewed from the keeper's thread; recovery never reclaimed it; generation 1 |
| renewal holding the row lock vs. reclaim, and the reverse order | the second statement waits, PostgreSQL re-evaluates its `WHERE`: renewal first keeps the Job, reclaim first makes the renewal return nothing |
| 8 concurrent reclaims of one expired Job | exactly 1 changes it |
| 6 concurrent recovery passes | 1 requeue, 1 message |
| pipeline-owned Job, worker dead | run waits; recovery requeues the same Job; a new claim finishes it and reports; the next `advance` settles the task and submits the next one |

## Limits

- One host, local Docker, one worker process per worker; timings are of this
  environment and of 6 s / 2 s leases, not of the 60 s default.
- "Detection" is lease expiry plus the recovery interval by construction; the
  measurements show the implementation adds ~0.3 s, not that failure detection is
  that fast in general.
- SIGSTOP stands in for a GC pause, VM freeze or partition from PostgreSQL; no network
  partition between worker and database was injected.
- A hung handler in a live process was not tested: by design the lease does not detect
  it.
