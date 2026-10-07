# Job state transition concurrency study

Point-in-time record. The current contract is
[Jobs and pipelines](../architecture/jobs-and-pipelines.md) §5 ("Job state
transitions"). Follows the [Job creation concurrency study](./job-creation-concurrency-study.md).

```text
date         2026-10-07
branch       dev
HEAD         a1227fe (uncommitted working tree on top of it, including the
             in-flight execution-key index of the previous study)
environment  one macOS host; PostgreSQL 16.15 (compose stack), READ COMMITTED default;
             disposable database sceneops_test per run
client       SQLAlchemy async + asyncpg, one asyncio loop
```

## Question

Every worker and reconciliation write of a Job was a conditional `UPDATE`. The
dispatch path (`JobService.mark_queued`, used by `POST /jobs/{id}/execute` and by
robot-run registration) read the Job, checked it, then wrote it with
`PostgresJobRepository.update`. Can a dispatcher whose read is stale move a Job
backward?

## Mechanism found

`update(job)` re-selects the row, receives a fresh ORM object (the object of the
first read is no longer referenced, and SQLAlchemy's identity map holds objects
weakly), and copies every field of the stale manifest onto it. Every column another
actor changed after the read therefore differs and is written back: the write is a
whole-row last-writer-wins, not a status change.

## Reproduction

Deterministic: the dispatcher is paused after its read
(`test_job_dispatch_concurrency_integration.py`); another actor commits through the
repository calls JobRunner and reconciliation use; the dispatcher then writes.

| ordering (after the dispatcher read the Job) | before | after |
| --- | --- | --- |
| a worker claims it (`RUNNING`, worker-1) | row becomes `queued`, `worker_id` and `started_at` erased; worker-2 can claim it; worker-1 loses ownership and cannot write its result | dispatcher refused; `RUNNING` by worker-1; second claim refused; worker-1 finishes |
| a worker claims and finishes it (`SUCCEEDED`) | `SUCCEEDED` → `queued`; worker-2 can claim and run it again | dispatcher refused; stays `SUCCEEDED` |
| reconciliation abandons it (`FAILED`, `JobAbandoned`) | revived as `queued`; `error` and `finished_at` erased | dispatcher refused; stays `FAILED` with its error |
| another retry of the same `FAILED` Job queues it | both retries succeed; `retry_count` 1 for two retries (lost increment) | one retry queues it (`retry_count` 1); the other is refused |
| another retry queues it, a worker runs it and it fails again | stale retry re-queues it with `retry_count` 1 (status recurred: ABA) | refused: `retry_count` no longer matches |

Two dispatchers of one `PENDING` Job, both reading before either writes, through
`JobDispatchFacade` with a counting backend, 20 races each (scratch script, not
kept; the pre-fix `mark_queued` re-created verbatim):

| | messages sent | `QUEUED` events | dispatches refused |
| --- | --- | --- | --- |
| before | 40 | 40 | 0 |
| after | 20 | 20 | 20 |

## Limits

- One host, one event loop; the guarantee itself is PostgreSQL's conditional
  `UPDATE`, not the client's timing.
- Duplicate handler execution was shown as a second successful claim, not by running
  two real handlers.
- A redispatch of a `QUEUED` Job (the recovery of a lost message) still sends a
  message whenever the Job is still `QUEUED`; two of them send two, and the claim
  keeps the execution single.
- `PipelineService.mark_queued` has the same read-then-whole-row-write shape for
  PipelineRuns and was not changed or measured.
