"""Cost of the execution-health queries (``sceneops_db.postgres.execution_metrics``)
over a large synthetic execution history.

Workload: ``--jobs`` finished Jobs spread over ``--days`` (19 job types, ~92 %
succeeded, ~6 % failed, ~2 % claimed twice after a lease loss), six JobEvents per
Job plus one recovery event per reclaimed Job, one PipelineRun of four tasks per
``--jobs-per-run`` Jobs, and an in-flight tail (``--queued`` QUEUED and
``--running`` RUNNING Jobs, a few expired leases). Rows are generated in SQL
(``generate_series``), so seeding 200 000 Jobs takes seconds.

Measured: table and index sizes, then each query of ``PostgresExecutionMetrics``
``--repeat`` times (median / max wall time through asyncpg) and its plan
(``EXPLAIN (ANALYZE, BUFFERS)``: execution time, top node, shared buffers).

Prerequisite: a disposable database (`tests/infrastructure/disposable_env.py create
--database sceneops_test_<name>`); the script refuses any other database and
TRUNCATEs the execution tables it seeds.

    SCENEOPS_DATABASE_URL=postgresql+asyncpg://.../sceneops_test_obs \\
      uv run python benchmarks/execution/benchmark_execution_metrics_sql.py \\
      --jobs 200000 --out /tmp/metrics_sql.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import text

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tests" / "infrastructure"))

from disposable_env import check_disposable_database  # noqa: E402
from sceneops_db.postgres import execution_metrics as m  # noqa: E402
from sceneops_db.session import dispose_async_engine, get_async_sessionmaker  # noqa: E402

JOB_TYPES = [
    "register_robot_run", "build_recording_scenes", "register_scenes", "validate_scene",
    "profile_scene", "build_recording_episodes", "register_episodes", "validate_episode",
    "profile_episode", "build_scene_sample_views", "mine_scenarios",
    "score_scenario_readiness", "predict_detection", "evaluate_detection",
    "align_episode", "export_learning_data", "import_labels", "curate_episodes",
    "export_analytics_snapshot",
]  # fmt: skip

SEED = """
TRUNCATE job_events, pipeline_task_runs, pipeline_runs, jobs, execution_records;

INSERT INTO jobs (job_id, type, status, params, steps, result, error, retry_count,
                  max_retries, worker_id, lease_generation, queued_at, enqueued_at,
                  locked_at, heartbeat_at, started_at, finished_at, created_at,
                  updated_at)
SELECT 'hjob-' || g,
       (:types)[1 + (g % :ntypes)],
       CASE WHEN g % 100 < 6 THEN 'failed' ELSE 'succeeded' END,
       jsonb_build_object('dataset_id', 'bench', 'robot_run_id', 'run-' || (g / 7)),
       '[{"job_step_id": "s1", "status": "succeeded"}]'::jsonb,
       CASE WHEN g % 100 < 6 THEN NULL
            ELSE jsonb_build_object('count', g % 997, 'checksum', md5(g::text)) END,
       CASE WHEN g % 100 < 6 THEN jsonb_build_object('type',
            (ARRAY['ValueError', 'ArtifactRecordConflictError', 'JobLeaseExpired'])[1 + g % 3],
            'message', 'synthetic') END,
       0, 0, 'celery:' || md5(g::text), CASE WHEN g % 50 = 0 THEN 2 ELSE 1 END,
       t, t, t + q, t + q + x, t + q, t + q + x, t, t + q + x
  FROM (
    SELECT g,
           now() - make_interval(secs => (:days * 86400.0) * (g::float / :jobs)) AS t,
           make_interval(secs => 0.005 + random() * random() * 2) AS q,
           make_interval(secs => 0.05 + exp(random() * 4) / 10) AS x
      FROM generate_series(1, :jobs) g
  ) s;

INSERT INTO job_events (event_id, job_id, type, level, job_type, status, worker_id,
                        data, error, created_at)
SELECT 'hev-' || j.job_id || '-' || e.n, j.job_id, e.type,
       CASE WHEN e.type = 'failed' THEN 'error' ELSE 'info' END,
       j.type, j.status, j.worker_id, '{}'::jsonb,
       CASE WHEN e.type = 'failed' THEN j.error END,
       j.created_at + make_interval(secs => e.n * 0.01)
  FROM jobs j
 CROSS JOIN LATERAL (VALUES
   (1, 'queued'), (2, 'locked'), (3, 'started'), (4, 'step_started'),
   (5, CASE WHEN j.status = 'failed' THEN 'step_failed' ELSE 'step_succeeded' END),
   (6, CASE WHEN j.status = 'failed' THEN 'failed' ELSE 'succeeded' END)
 ) AS e(n, type)
 WHERE j.job_id LIKE 'hjob-%';

INSERT INTO job_events (event_id, job_id, type, level, job_type, status, data, created_at)
SELECT 'hev-' || job_id || '-r', job_id, 'queued', 'warning', type, 'queued',
       jsonb_build_object('expired_generation', 1, 'lease_expires_at', locked_at),
       locked_at
  FROM jobs WHERE lease_generation = 2;

INSERT INTO pipeline_runs (pipeline_run_id, type, status, params, created_at,
                           updated_at, started_at, finished_at)
SELECT 'hrun-' || r, 'recording_scene_building',
       CASE WHEN r % 100 < 3 THEN 'failed' WHEN r % 100 < 4 THEN 'blocked'
            ELSE 'succeeded' END,
       '{}'::jsonb, j.created_at, j.finished_at, j.created_at, j.finished_at
  FROM generate_series(1, :jobs / :per_run) r
  JOIN jobs j ON j.job_id = 'hjob-' || (r * :per_run);

INSERT INTO pipeline_task_runs (pipeline_task_run_id, pipeline_run_id,
       pipeline_task_id, pipeline_task_name, task_order, status, job_type, job_id,
       created_at, updated_at, started_at, finished_at)
SELECT 'htask-' || r || '-' || k, 'hrun-' || r,
       (ARRAY['build_recording_scenes', 'register_scenes', 'validate_scene',
              'profile_scene'])[k],
       'task ' || k, k, 'succeeded', j.type, j.job_id,
       j.created_at, j.finished_at, j.queued_at,
       j.finished_at + make_interval(secs => 0.01 + random() * 0.05)
  FROM generate_series(1, :jobs / :per_run) r
 CROSS JOIN generate_series(1, 4) k
  JOIN jobs j ON j.job_id = 'hjob-' || (r * :per_run - 4 + k);

INSERT INTO jobs (job_id, type, status, params, lease_generation, queued_at,
                  enqueued_at, locked_at, heartbeat_at, lease_expires_at, started_at, created_at,
                  updated_at)
SELECT 'fjob-' || g, (:types)[1 + (g % :ntypes)],
       CASE WHEN g <= :queued THEN 'queued' ELSE 'running' END,
       '{}'::jsonb, CASE WHEN g <= :queued THEN 0 ELSE 1 END,
       now() - make_interval(secs => g), now() - make_interval(secs => g),
       CASE WHEN g > :queued THEN now() - make_interval(secs => 5) END,
       CASE WHEN g > :queued THEN now() - make_interval(secs => 5) END,
       CASE WHEN g > :queued THEN now() + make_interval(secs => CASE WHEN g % 10 = 0
            THEN -30 ELSE 55 END) END,
       CASE WHEN g > :queued THEN now() - make_interval(secs => 5) END,
       now() - make_interval(secs => g), now()
  FROM generate_series(1, :queued + :running) g;

ANALYZE jobs; ANALYZE job_events; ANALYZE pipeline_runs; ANALYZE pipeline_task_runs;
"""

QUERIES = {
    "backlog": (m._BACKLOG, False),
    "pipelines_waiting": (m._PIPELINES_WAITING, False),
    "jobs_window": (m._JOBS_WINDOW, True),
    "recovery_window": (m._RECOVERY_WINDOW, True),
    "reclaims_by_worker": (m._RECLAIMS_BY_WORKER, True),
    "pipelines_window": (m._PIPELINES_WINDOW, True),
    "pipeline_stages": (m._PIPELINE_STAGES, True),
}


def _bind(sql: str, params: dict) -> str:
    """``SEED`` with its parameters inlined: it is several statements, which
    asyncpg cannot prepare with bind parameters."""
    for name, value in sorted(params.items(), key=lambda kv: -len(kv[0])):
        if isinstance(value, list):
            literal = "ARRAY[" + ", ".join(f"'{v}'" for v in value) + "]::text[]"
        else:
            literal = str(value)
        sql = sql.replace(f":{name}", literal)
    return sql


async def seed(session, args) -> float:
    started = time.perf_counter()
    statements = _bind(
        SEED,
        {
            "types": JOB_TYPES,
            "ntypes": len(JOB_TYPES),
            "jobs": args.jobs,
            "days": args.days,
            "per_run": args.jobs_per_run,
            "queued": args.queued,
            "running": args.running,
        },
    )
    raw = await (await session.connection()).get_raw_connection()
    await raw.driver_connection.execute(statements)
    await session.commit()
    return time.perf_counter() - started


async def sizes(session) -> dict:
    rows = await session.execute(
        text(
            """
            SELECT relname, n_live_tup,
                   pg_total_relation_size(relid), pg_relation_size(relid),
                   pg_indexes_size(relid)
              FROM pg_stat_user_tables
             WHERE relname IN ('jobs', 'job_events', 'pipeline_runs',
                               'pipeline_task_runs')
            """
        )
    )
    return {
        r[0]: {
            "rows": r[1],
            "total_bytes": r[2],
            "heap_bytes": r[3],
            "index_bytes": r[4],
        }
        for r in rows.all()
    }


def _plan_summary(plan: dict) -> dict:
    root = plan["Plan"]

    def nodes(node):
        yield node
        for child in node.get("Plans", []):
            yield from nodes(child)

    scans = sorted(
        {
            f"{n['Node Type']}:{n.get('Index Name') or n.get('Relation Name')}"
            for n in nodes(root)
            if "Scan" in n["Node Type"]
        }
    )
    return {
        "execution_ms": round(plan["Execution Time"], 3),
        "planning_ms": round(plan["Planning Time"], 3),
        "shared_hit": root.get("Shared Hit Blocks"),
        "shared_read": root.get("Shared Read Blocks"),
        "scans": scans,
    }


async def measure(session, args) -> dict:
    end = (await session.execute(text("SELECT now()"))).scalar_one()
    from datetime import timedelta

    results = {}
    for window_s in args.windows:
        params = {"start": end - timedelta(seconds=window_s), "end": end}
        for name, (stmt, windowed) in QUERIES.items():
            if not windowed and window_s != args.windows[0]:
                continue
            key = f"{name}@{window_s}s" if windowed else name
            timings = []
            for _ in range(args.repeat):
                t0 = time.perf_counter()
                (await session.execute(stmt, params if windowed else {})).all()
                timings.append((time.perf_counter() - t0) * 1000)
            explain = text(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {stmt.text}")
            plan = (
                await session.execute(explain, params if windowed else {})
            ).scalar_one()[0]
            rows_in_window = None
            if windowed:
                rows_in_window = (
                    await session.execute(
                        text(
                            "SELECT count(*) FROM jobs "
                            "WHERE finished_at >= :start AND finished_at < :end"
                        ),
                        params,
                    )
                ).scalar_one()
            results[key] = {
                "median_ms": round(statistics.median(timings), 3),
                "max_ms": round(max(timings), 3),
                "finished_jobs_in_window": rows_in_window,
                **_plan_summary(plan),
            }
    t0 = time.perf_counter()
    await m.PostgresExecutionMetrics(session).snapshot(window_seconds=args.windows[0])
    results["snapshot_total_ms"] = round((time.perf_counter() - t0) * 1000, 3)
    return results


async def main(args) -> dict:
    database = urlsplit(
        __import__("os").environ["SCENEOPS_DATABASE_URL"].replace("+asyncpg", "")
    ).path.lstrip("/")
    check_disposable_database(database)
    sessionmaker = get_async_sessionmaker()
    try:
        async with sessionmaker() as session:
            seed_s = None if args.no_seed else await seed(session, args)
            report = {
                "database": database,
                "workload": vars(args) | {"windows": args.windows},
                "seed_seconds": seed_s,
                "sizes": await sizes(session),
                "queries": await measure(session, args),
            }
            await session.rollback()
            return report
    finally:
        await dispose_async_engine()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--jobs", type=int, default=200_000)
    parser.add_argument("--days", type=float, default=7.0)
    parser.add_argument("--jobs-per-run", type=int, default=5)
    parser.add_argument("--queued", type=int, default=500)
    parser.add_argument("--running", type=int, default=50)
    parser.add_argument("--repeat", type=int, default=10)
    parser.add_argument(
        "--windows", type=lambda s: [float(x) for x in s.split(",")],
        default=[300.0, 3600.0, 86400.0],
        help="window lengths in seconds, comma-separated",
    )  # fmt: skip
    parser.add_argument("--no-seed", action="store_true", help="measure only")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    report = asyncio.run(main(args))
    text_report = json.dumps(report, indent=2, default=str)
    if args.out:
        args.out.write_text(text_report)
    print(text_report)
