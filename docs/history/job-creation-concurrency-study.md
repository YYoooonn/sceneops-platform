# Job creation concurrency study

Point-in-time record. The current contract is
[Jobs and pipelines](../architecture/jobs-and-pipelines.md) §5; the decision is the
"one in-flight Job per execution key" amendment of
[ADR-008](../adr/008-acquisition-lifecycle-reliability.md).

```text
date         2026-10-07
branch       dev
HEAD         a1227fe (uncommitted working tree on top of it)
environment  one macOS host; PostgreSQL 16.15 (compose stack), READ COMMITTED default;
             disposable database sceneops_test per run (tests/infrastructure/disposable_env.py)
client       SQLAlchemy async + asyncpg, pool_size=5 / max_overflow=10, one asyncio loop
workload     JobService.create_job(REGISTER_ROBOT_RUN, unique manifest_uri per trial);
             each caller its own session and transaction, committed before returning
```

## Question

`create_job` deduplicated on `execution_key` by reading first and inserting after,
with no constraint behind the read. Do concurrent requests for the same Job create
duplicates, and does `claim_for_run` have the same weakness?

## Experiment A: concurrent creation of one key

Deterministic: an `asyncio.Barrier` held every caller between its lookup and its
insert (`test_job_creation_concurrency_integration.py`). 8 callers:

| | Job rows | distinct ids returned | `CREATED` events | exceptions |
| --- | --- | --- | --- | --- |
| before | 8 | 8 | 8 | 0 |
| after | 1 | 1 | 1 | 0 |

Ungated, 30 trials per row (scratch script, not kept; `rows/key` is
`{rows: trials}`):

| callers | arrival spacing | before: rows/key | before: trials with duplicates | after: rows/key | after: errors |
| --- | --- | --- | --- | --- | --- |
| 2 | 0 ms | {2: 30} | 30/30 | {1: 30} | 0 |
| 8 | 0 ms | {5: 17, 6: 7, 7: 6} | 30/30 | {1: 30} | 0 |
| 8 | 1 ms | {2: 4, 3: 10, 4: 13, 5: 3} | 30/30 | {1: 30} | 0 |
| 8 | 5 ms | {1: 25, 2: 5} | 5/30 | {1: 30} | 0 |
| 8 | 20 ms | {1: 27, 2: 3} | 3/30 | {1: 30} | 0 |

Mean trial time was within measurement noise before and after (8 callers, 0 ms:
66.5 ms vs 71.2 ms). The window is the whole span from lookup to commit (insert,
event insert, commit, connection checkout), which is why callers 20 ms apart still
collided.

## Experiment B: concurrent claim of one Job

`claim_for_run` is one `UPDATE ... WHERE status IN (pending, queued) RETURNING`
(`test_job_claim_concurrency.py`):

- 10 claimers released together by a barrier: exactly 1 owner, every run.
- A second claim issued while the first is uncommitted blocks on the row lock, then
  re-evaluates its predicate on the committed row and updates 0 rows.
- The same claim written as `SELECT status` then unconditional `UPDATE` (contrast
  only): 10 of 10 workers believed they owned the Job; the row kept the last writer.

## Bug found while validating the fix

The first fix rendered the `ON CONFLICT ... WHERE status IN (...)` arbiter predicate
with bind parameters. Every test passed, but the repeated measurement failed with
`there is no unique or exclusion constraint matching the ON CONFLICT specification`:
asyncpg prepares statements and PostgreSQL plans a prepared statement generically,
without parameter values, after five executions on one connection, so the partial
index can no longer be inferred. Reproduced with `SET plan_cache_mode =
force_generic_plan` and with 12 inserts on one connection; fixed by rendering the
predicate as literal SQL shared by the index and the conflict target.

## Limits

- One host, one event loop, one client library; not measured across processes or
  hosts. The guarantee itself does not depend on that: PostgreSQL enforces it.
- The reuse of a succeeded Job remains a read. A request whose lookup preceded
  another Job's entire run can still create one more Job; not measured.
- PipelineRun creation uses the same read-then-insert pattern and was not changed or
  measured.
