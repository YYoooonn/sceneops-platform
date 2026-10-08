# Execution observability study

Point-in-time record. The current contract is
[Jobs and pipelines](../architecture/jobs-and-pipelines.md) §5 ("Execution health") and
the decision is [ADR-012](../adr/012-execution-health-from-durable-state.md). Follows the
[Lost dispatch study](./lost-dispatch-study.md).

```text
date         2026-10-08
branch       dev
HEAD         f034b97 (all changes uncommitted on top of it)
environment  one macOS 15.3.2 host, 8 cores, 16 GiB; PostgreSQL 16.15 (compose stack),
             disposable database sceneops_test_obs; Redis 7.4.9 throwaway container;
             Celery 5.6.3 / kombu 5.6.2, prefork; SQLAlchemy 2.0.50, asyncpg 0.31.0
workers      benchmarks/execution/load_worker.py: the production worker (JobRunner, lease
             keeper, orchestrator, dispatcher) with probe handlers of log-normal duration
             (median 0.3 s, sigma 0.4; align_episode 0.4 s; export_learning_data 0.6 s)
             and injectable send failures; one job worker of 4 slots (worker loss: two of
             2), one pipeline worker of 2
driver       benchmarks/execution/execution_observability_experiments.py: Poisson
             arrivals through the API's dispatch facades (standalone profile_scene 40 %,
             validate_scene 30 %, export_learning_data 30 %; EPISODE_LEARNING_DATA_BUILDING
             runs of 2 tasks); execution recovery every 1 s with a 10 s resend threshold;
             a metrics snapshot every 1 s (10 s window)
truth        submit times (driver), handler start/end (worker marker log), recovery
             actions (pass reports); none of it read from the metrics under test
```

## Question

Can an operator tell, from SceneOps' own durable state, that the execution system is slow,
backed up, recovering work or unhealthy, without injecting a fault and reading rows, and
what must be instrumented for that?

## Step 1 — signals before any change

| Signal | Where | Operationally exposed? |
| --- | --- | --- |
| Job status counts | `jobs.status` | `GET /operations/summary`, which reports running, failed, succeeded and pending but **not `queued`**: a backlog is invisible there |
| last dispatch, claim, heartbeat, lease, start, finish | `queued_at`, `locked_at`, `heartbeat_at`, `lease_expires_at`, `started_at` (first claim only), `finished_at` | per Job in the API; no aggregate |
| claims per Job | `lease_generation` | per Job |
| transitions | `job_events`: CREATED / QUEUED (API: data `{}`; orchestrator; resend: `previous_queued_at`; lease reclaim: `expired_generation`, level warning) / LOCKED / STARTED / SUCCEEDED / FAILED | per Job (`GET /jobs/{id}/events`) |
| sends | `execution_records` (one per `run_job` send; none for `advance` sends from workers) | `GET /executions` |
| pipeline progress | `pipeline_runs`, `pipeline_task_runs` (task `started_at` = Job submitted, `finished_at` = Job observed) | per run |
| domain counts | `jobs.result` (`observation_count`, `created_payload_count`, `reused_count`, `new_shard_counts` …), per type, not uniform | per Job |
| recovery pass | `sceneops-worker recover` JSON summary (outcome counts per sweep) | stdout of the loop only |
| broker | nothing: no queue depth, no worker inventory, no capacity | — |
| bytes, stage timings, cache hits, send latency, DB pool wait | not recorded | — |

On the reference database (220 real Jobs of the golden contract) the row timestamps alone
showed what an average hides: `register_robot_run` execution p50 1.6 s, max 1 046 s;
`build_recording_scenes` p50 12.8 s, max 538 s — mean ~54 s for a 1.6 s operation. Why
those three runs took 10 minutes is not answerable from durable state (no stage timing).

Timestamp traps found by reading the transitions:

- `queued_at` is the last dispatch; execution recovery moves it on every resend.
- `started_at` is the first claim (`coalesce`), `locked_at` the last one, rewritten by
  the worker with its own clock; `finished_at` is the worker's clock, `queued_at` and
  lease times PostgreSQL's.
- A refused duplicate claim raises `RuntimeError` in the Celery task: an ERROR line and a
  40-line traceback per duplicate.
- `worker_id` = `celery:<task id>` names the message, not the worker.

## Step 4 — SQL-derived baseline

`sceneops_db/postgres/execution_metrics.py` (read-only): backlog and oldest queued age,
running count, expired leases, heartbeat age, what active runs wait on; per Job type over
a window: finished, failure rate, failures by error, throughput, p50/p95/p99/max of queue
wait, execution, end to end; recovery actions from JobEvents; per-task pipeline stage
breakdown. Baseline definitions used only existing columns (queue wait =
`locked_at - queued_at`).

Cost (`benchmark_execution_metrics_sql.py`, 200 550 Jobs over 7 days, 1 204 000 events,
40 000 runs, 160 000 task runs; median of 5–10):

| Query | Baseline (no new index) | With `ix_jobs_finished_at` / `ix_pipeline_runs_finished_at` |
| --- | --- | --- |
| backlog (in flight only) | 0.99 ms | 0.86 ms |
| runs waiting | 0.60 ms | 0.56 ms |
| jobs window 5 min (85 rows) | 19.5 ms — Seq Scan of jobs | 1.24 ms — Index Scan |
| jobs window 1 h (1 176 rows) | 19.9 ms — Seq Scan | 4.6 ms |
| jobs window 24 h (28 557 rows) | 88.6 ms | 83.3 ms (sort-bound) |
| recovery window 5 min | 0.84 ms | 0.61 ms (+0.70 ms by worker) |
| pipelines window 5 min | 3.5 ms — Seq Scan of pipeline_runs | 0.58 ms |
| pipeline stages 1 h / 24 h | 9.2 / 132 ms | 6.8 / 144 ms |
| whole snapshot, 5 min window | 32.5 ms | 9.5 ms (one measurement) |

Without the index a window metric reads all history whatever its length; with it, it
reads the window. Index size 4.4 MB at 200 000 Jobs; `jobs` grew 127.7 → 136.8 MB with the
column and index (~45 B per Job).

## Steps 7 — experiments, baseline metrics (before instrumentation)

| Scenario | Jobs | Truth queue wait p50 / p95 / max | Metric (`locked_at - queued_at`) p50 / p95 / max | Max oldest queued: metric / truth | Max broker depth / max `QUEUED` |
| --- | --- | --- | --- | --- | --- |
| healthy (first run) | 244 | 0.08 / 0.42 / 0.59 s | 0.07 / 0.41 / 0.58 s | — | 5 / 5 |
| backlog | 625 | 23.8 / 40.0 / 40.9 s | 5.6 / 9.8 / 10.8 s | 11.2 / 40.7 s | 783 / 292 |
| slow handler | 276 | 2.9 / 10.7 / 12.1 s | 1.9 / 9.2 / 10.5 s | 11.0 / 12.1 s | 29 / 29 |
| worker loss | 243 | 0.33 / 1.17 / 1.77 s | 0.33 / 1.16 / 1.76 s | 1.5 / 1.5 s | 6 / 6 |
| lost dispatch | 201 | 0.19 / 11.2 / 11.7 s | 0.12 / 0.96 / 1.10 s | 10.5 / 11.3 s | 8 / 36 |

- **Backlog**: 490 of 625 Jobs had their queue wait underestimated by more than 1 s
  (median 21 s). Every resend (969, threshold 10 s) moved `queued_at`, so queue wait and
  oldest queued age never exceeded about one threshold while the real wait reached 41 s.
- **Lost dispatch**: the 36 Jobs whose sends failed waited ~11 s; the derived p95 was
  0.96 s. The incident disappeared from the latency distribution.
- **Duplicates**: 982 refused claims across backlog and slow handler, each an ERROR
  traceback in the worker log, for zero failed Jobs.
- **Worker loss**: the reclaimed Job's `worker_id` named a Celery task, not the killed
  worker.

A repeat of the healthy run was invalidated: the host went to sleep (sampler gaps of 949 s,
288 s, 1 018 s, 190 s; a live worker lost its lease once). Every later run was wrapped in
`caffeinate` and checked for gaps; none had any.

## Step 6 — instrumentation added

| Gap (ranked) | Change | Extra writes |
| --- | --- | --- |
| 1. queue time and oldest queued age capped at the resend threshold | `jobs.enqueued_at`: set on entering `QUEUED` from another status, kept by redispatch / resend / claim; metrics use it | none (same UPDATE) |
| 2. window metrics scan all history | partial `finished_at` indexes on jobs and pipeline_runs | one index entry per terminal UPDATE (already non-HOT: `status` is indexed) |
| 3. refused duplicate claims logged as task failures | `JobNotClaimableError`; the task logs one `job claim refused` warning, returns `not_claimed` | none |
| 4. reclaims not attributable to a worker | LOCKED event: `attempt` = generation, `worker_node` (Celery node name, captured by `celeryd_after_setup` before the pool forks), `pid` | ~60 B in an existing event |
| 5. no operator surface; broker depth unknown | `sceneops-worker execution-status` / `make execution-status` (JSON, optional broker depth) | none |

Not instrumented, with the reason: handler stage timings and bytes (the slow-handler
scenario was localized to its Job type by per-type execution percentiles; inside a handler
is a tracing question); advance resends (no Job row to attach to; rare; in recovery's
summary); Celery send latency and DB pool wait (no scenario showed them as a cause);
periodic metric snapshots in PostgreSQL (nothing required persistence).

First attempt at gap 4 recorded `socket.gethostname()`: both workers of the worker-loss
run share one host, so the reclaim was attributed to `yyoooonn.local`. Celery's
`request.hostname` in a prefork child is also only the host. The node name from
`celeryd_after_setup` attributed both reclaims to `jobs-b@yyoooonn.local`, the killed worker.

## Step 7 — experiments, instrumented

| Scenario | Truth queue wait p95 / max | Metric (`locked_at - enqueued_at`) p95 / max, worst type | Max oldest queued: metric / truth | Throughput (finished / 10 s) | Recovery | Refused claims |
| --- | --- | --- | --- | --- | --- | --- |
| healthy | 0.63 / 1.26 s | 0.62 / 1.07 s | 0.55 / 0.57 s | 41.5 | none | 0 |
| backlog | 43.3 / 44.6 s | 43.3 / 44.6 s | 44.53 / 44.54 s | 71 (RUNNING 4 of 4) | 973 resends | 973 warnings |
| slow handler | 10.7 / 13.3 s | 10.7 / 13.3 s | 12.91 / 12.93 s | 42 → 32 | 12 resends | 12 |
| worker loss (rerun with node names) | 1.22 / 1.54 s | 1.21 / 1.53 s | 2.6 / 2.7 s | 51 → 35 | 2 lease requeues, `jobs-b@…: 2` | 0 |
| lost dispatch | 12.0 / 12.2 s | 11.96 / 12.24 s | 11.86 / 11.87 s | 48 → 3 → recovered | 35 resends, 30 API send errors | 0 |

Per-scenario reading of the snapshot alone:

- **Backlog**: `QUEUED` rose to 288 while `RUNNING` stayed at the 4 slots, oldest queued
  age climbed linearly to 44.5 s, throughput held at ~71 per 10 s; broker depth reached
  810, 2.8× the backlog, because resends add messages. PostgreSQL's `QUEUED` count matched
  truth exactly. Pipeline stage breakdown: queue wait p50 23–28 s, execution 0.5–0.6 s,
  handoff 0.06 s — the queue owned end-to-end latency (run p50 62.5 s).
- **Slow handler** (export_learning_data median 0.6 → 2.5 s at t = 15 s): execution p50
  of that type 2.37 s vs 0.31–0.42 s for the others; queue wait p95 rose to 8–11 s for
  every type, including those whose handler did not change (one shared queue). Heartbeat
  age reached 8.3 s: a slow handler is not a dead one, the lease kept renewing.
- **Worker loss** (kill jobs-b at t = 20.0 s, lease 6 s): heartbeat age rose linearly from
  ~22 s and reached 6.6 s; `lease_expired` = 2 at 26.1 s; reclaim at 26.6 s; restarts on
  jobs-a at 26.8 / 27.0 s (kill → restart ≈ 7 s = lease + pass interval). End to end of
  the two reclaimed Jobs ≈ 7.6 s, visible as export_learning_data end-to-end max 9.0 s and
  `claimed_more_than_once` = 2, while their queue wait and execution stayed normal.
  Throughput 51 → 35 per 10 s.
- **Lost dispatch** (every send fails 15–25 s): `QUEUED` rose 0 → 34 linearly while
  `RUNNING` = 0, broker depth = 0 and throughput fell to 3 per 10 s; oldest queued age
  climbed until the first resends (10 s threshold), then everything drained in ~10 s.

## Step 8 — overhead of the instrumentation

- PostgreSQL writes per Job: unchanged in count (`enqueued_at` rides the transition
  UPDATE; worker identity rides the existing LOCKED event). JobEvents per Job: 4–6 in
  both runs.
- Storage: +8 B per Job (`enqueued_at`) + ~22 B per finished Job (index) + ~60 B per
  claim (LOCKED event data).
- Read cost: one snapshot per second during the runs, p50 9.5–14.7 ms; p95 20–49 ms,
  except during the backlog (p95 115 ms, max 661 ms) when PostgreSQL was also serving
  ~1 600 dispatch and claim writes per minute.
- Job latency: healthy execution p50 0.30–0.33 s and queue wait p50 0.08–0.10 s before
  and after: no difference within run-to-run variation. Not benchmarked separately.
- Not caused by instrumentation but measured: in the backlog every resend wrote one
  JobEvent and one ExecutionRecord (1 598 `job_run` records for 625 Jobs) and cost one
  refused claim on a worker slot.

## Limits

- One host, local Docker; worker and PostgreSQL clocks share the host, so mixed-clock
  durations carry no skew here.
- Probe handlers sleep; no CPU, I/O or storage load. Bytes, CPU and DB pool saturation were
  not exercised.
- Short thresholds (resend 10 s, lease 6 s) compress the defaults (300 s, 60 s); delays
  scale with them.
- Backlog throughput (~71 per 10 s) is below the ~85 expected from 4 slots and the handler
  medians; whether the 973 refused claims occupying slots explain the gap was not
  measured.
- Workloads of 200–625 Jobs per scenario; the SQL cost was measured at 200 000 Jobs,
  not beyond.
