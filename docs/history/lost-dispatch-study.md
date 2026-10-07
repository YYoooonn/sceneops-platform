# Lost dispatch study

Point-in-time record. The current contract is
[Jobs and pipelines](../architecture/jobs-and-pipelines.md) §5 ("Messages are
wake-ups") and the decision is [ADR-011](../adr/011-state-derived-redispatch.md).
Follows the [Job worker liveness study](./job-worker-liveness-study.md).

```text
date         2026-10-08
branch       dev
HEAD         before: e630773 with the job-ownership-lease work staged on top (committed
             unchanged as 35199bc during the study); after: uncommitted working tree on
             35199bc
environment  one macOS 15.3 host; PostgreSQL 16.15 (compose stack), disposable database
             per run; Redis 7.4.9 throwaway container (default snapshotting, no AOF);
             Celery 5.6.3 / kombu 5.6.2, prefork; job and pipeline workers, 2 each
workers      the production worker with every handler replaced by a probe whose result
             carries its task's declared outputs (real EPISODE_LEARNING_DATA_BUILDING
             runs complete), and dispatcher sends that a fault file can fail
api          the API's own dispatch facades, in the test process
```

## Question

PostgreSQL commits state that needs asynchronous work, then a Celery message is sent.
What happens when that message is not delivered, and does SceneOps need a
transactional outbox to fix it?

## Boundaries found

| Boundary | State committed before the send | Message |
| --- | --- | --- |
| API Job dispatch | Job `QUEUED` | `run_job(job_id)` |
| API PipelineRun dispatch | run `QUEUED` | `advance(run_id)` |
| Orchestrator submits a task | task `RUNNING` + Job `QUEUED` | `run_job(job_id)` |
| `JobRunner` after the terminal commit | Job terminal, task `RUNNING` | `advance(run_id)` |
| Lease recovery | Job `QUEUED` (requeued) | `run_job(job_id)`; `advance` when it fails the Job |
| RobotRun registration | Job `QUEUED` (through the API Job dispatch) | `run_job(job_id)` |

All messages carry an identifier only. Duplicate sends were already safe.

## Before: no redispatch

| Case | Fault | Observed |
| --- | --- | --- |
| A | API dispatch with the broker unreachable | `OperationalError` after 0.67 s (kombu's publish retries), API 500. Job `queued`, 0 ExecutionRecords, empty queue; unchanged 30 s later with a live worker. A manual re-POST was accepted (QUEUED → QUEUED compare-and-set) and the Job succeeded 0.21 s later. |
| A2 | orchestrator's task dispatch fails | run `running`, task `running`, Job `queued`, no record; unchanged 30 s later. Re-executing the run is refused (`RUNNING`); a manual `POST /jobs/{id}/execute` of the task Job completed the run. |
| B | `JobRunner`'s `advance` fails after `succeeded` | run and task `running` over a `succeeded` Job; unchanged 30 s later; one manual `advance` completed the run. |
| C | lease recovery's send fails | outcome `dispatch_failed`, Job `queued`; the next pass took no action (the Job is no longer `RUNNING`); unchanged 30 s later. |
| D | duplicates | 4 `run_job` messages for one Job: 1 handler run. 5 `advance` messages for a waiting run: run succeeded with exactly 1 Job per task. |

## After: state-derived redispatch

Resend threshold 5 s, a recovery pass every 1 s in the test process (real Celery
dispatcher). Loss = the failed send; resend = the pass that re-sent it.

| Lost message | runs | loss → resend (median, min–max) | resend → done | loss → run succeeded | executions per Job |
| --- | --- | --- | --- | --- | --- |
| API `run_job` (broker unreachable) | 5 | 5.85 s (5.17–6.03) | 0.06 s | — | 1 |
| orchestrator `run_job` | 3 | 5.76 s (5.62–5.82) | 0.34 s | 6.16 s | 1 |
| `JobRunner` `advance` | 3 | 5.59 s (5.57–5.65) | 0.32 s | 5.91 s | 1 |

Backlog: 20 Jobs dispatched with no job worker for ~16 s (3 thresholds): 62 messages
queued before a worker started, 72 sent in all (20 dispatches, 52 resends), 20 handler
runs.

Cost: one pass with nothing due, over 50 000 finished Jobs and 5 000 finished runs:
6.8 ms median (16.9 ms max, 10 passes); the overdue-Job and expired-lease reads are index
scans of 0.05 ms and 0.02 ms. A resend writes one conditional `UPDATE` (plus, for a Job,
one JobEvent and one ExecutionRecord).

Kept suites (`apps/worker/tests/execution/`, `tests/infrastructure/test_lost_dispatch_recovery.py`):
broker down during the API dispatch; an accepted message lost by a Redis SIGKILL
(snapshot taken before the send); the orchestrator's send lost; `advance` lost; lease
recovery's send lost; a pass dying before its send and after it (one duplicate, one
claim); 4 and 3 racing passes (each message re-sent once); every lost message of a
pipeline in turn (the run completes, one Job per task); duplicate `run_job` and
`advance` messages (one execution, one Job per task).

## Limits

- One host, local Docker, a 5 s threshold for timings (the default is 300 s); delays are
  threshold plus pass interval by construction.
- Broker message loss was produced by SIGKILL of a Redis without AOF; the compose
  stack's Redis runs with `appendonly yes` (fsync every second), which narrows but does
  not close that window.
- Network partitions and a lost `advance` *task execution* (delivered, then the step
  crashed) were not injected separately; the latter leaves the same durable state as a
  lost message.
