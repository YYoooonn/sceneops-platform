"""Controlled workloads against the real execution path, observed only through the
durable-state metrics of ``sceneops_db.postgres.execution_metrics`` -- and checked
against ground truth recorded outside PostgreSQL.

Each scenario runs, in a disposable database:

* Celery worker subprocesses of ``load_worker`` (the production worker; handlers
  are probes with a controllable log-normal duration and failure rate; the
  workers' sends can be failed) on a throwaway Redis (``RecoveryRedis``);
* an arrival process in this process submitting standalone Jobs and
  EPISODE_LEARNING_DATA_BUILDING PipelineRuns through the API's own dispatch
  facades (Poisson arrivals);
* execution recovery (``recover_execution``) every ``recovery_interval_s``;
* a sampler taking ``PostgresExecutionMetrics.snapshot`` every second, plus the
  broker's queue lengths;
* a timeline of injected events (kill a worker, slow a job type, fail sends).

Ground truth, independent of the metrics under test: submit times (this process),
handler start/end (the workers' marker log), recovery actions (the passes'
reports). The report compares metric and truth per scenario.

Scenarios: healthy, backlog, slow_handler, worker_loss, lost_dispatch.

Prerequisites: Docker (throwaway Redis), the local PostgreSQL / MinIO servers, a
disposable database (the script refuses any other and TRUNCATEs its execution
tables before every scenario):

    python tests/infrastructure/disposable_env.py create --database sceneops_test_obs ...
    SCENEOPS_DATABASE_URL=postgresql+asyncpg://.../sceneops_test_obs \\
      uv run python benchmarks/execution/execution_observability_experiments.py \\
      --scenarios healthy,backlog --out /tmp/obs
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import random
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT / "tests" / "infrastructure"))
os.environ["PYTHONPATH"] = os.pathsep.join(
    p for p in [str(HERE), os.environ.get("PYTHONPATH", "")] if p
)

import redis  # noqa: E402
from sqlalchemy import text  # noqa: E402

from disposable_env import check_disposable_database  # noqa: E402
from recovery_support import RecoveryEnv, RecoveryRedis, WorkerProcess  # noqa: E402
from sceneops_core.config import ArtifactSettings, CelerySettings  # noqa: E402
from sceneops_core.jobs.schemas import JobManifest, JobStatus, JobType  # noqa: E402
from sceneops_core.pipelines.schemas import (  # noqa: E402
    CreatePipelineRunRequest,
    PipelineType,
)
from sceneops_db.postgres.execution_metrics import PostgresExecutionMetrics  # noqa: E402
from sceneops_db.postgres.jobs import PostgresJobRepository  # noqa: E402
from sceneops_db.postgres.pipelines import (  # noqa: E402
    PostgresPipelineRunRepository,
    PostgresPipelineTaskRunRepository,
)
from sceneops_db.session import dispose_async_engine, get_async_sessionmaker  # noqa: E402
from sceneops_worker.execution import create_celery_app  # noqa: E402
from sceneops_worker.execution.dispatcher import CeleryExecutionDispatcher  # noqa: E402
from sceneops_worker.execution.recovery import recover_execution  # noqa: E402

STANDALONE_MIX = [  # (job type, share of standalone arrivals)
    (JobType.PROFILE_SCENE, 0.4),
    (JobType.VALIDATE_SCENE, 0.3),
    (JobType.EXPORT_LEARNING_DATA, 0.3),
]
BASE_DELAY = {
    "*": [0.3, 0.4],
    "align_episode": [0.4, 0.4],
    "export_learning_data": [0.6, 0.4],
}


@dataclass
class Scenario:
    name: str
    duration_s: float
    job_rate: float  # standalone Jobs per second
    pipeline_rate: float  # PipelineRuns per second (2 Jobs each)
    job_workers: list[tuple[str, int]] = field(default_factory=lambda: [("jobs-a", 4)])
    lease_s: float = 60.0
    resend_after_s: float = 10.0
    recovery_interval_s: float = 1.0
    drain_timeout_s: float = 120.0
    control: dict = field(default_factory=lambda: {"delay": dict(BASE_DELAY)})
    # (seconds after start, action, argument)
    timeline: list[tuple[float, str, object]] = field(default_factory=list)


SCENARIOS = {
    # ~45 % of 4 slots: 3 Jobs/s x ~0.43 s + 0.5 runs/s x 2 Jobs.
    "healthy": Scenario("healthy", duration_s=60, job_rate=3.0, pipeline_rate=0.5),
    # ~150 % of capacity for 40 s, then nothing: the queue builds and drains.
    "backlog": Scenario(
        "backlog", duration_s=40, job_rate=12.0, pipeline_rate=1.5, drain_timeout_s=180
    ),
    # export_learning_data becomes ~4x slower at t=15 s; the rest is unchanged.
    "slow_handler": Scenario(
        "slow_handler",
        duration_s=75,
        job_rate=3.0,
        pipeline_rate=0.5,
        timeline=[
            (
                15,
                "control",
                {"delay": dict(BASE_DELAY, export_learning_data=[2.5, 0.4])},
            )
        ],
    ),
    # Two workers of 2 slots; one is SIGKILLed at t=20 s. Lease 6 s.
    "worker_loss": Scenario(
        "worker_loss",
        duration_s=60,
        job_rate=3.0,
        pipeline_rate=0.5,
        job_workers=[("jobs-a", 2), ("jobs-b", 2)],
        lease_s=6.0,
        timeline=[(20, "kill", "jobs-b")],
    ),
    # Every send fails for 10 s: the API's run_job (broker down at the API), the
    # orchestrator's run_job and JobRunner's advance (workers' sends).
    "lost_dispatch": Scenario(
        "lost_dispatch",
        duration_s=50,
        job_rate=3.0,
        pipeline_rate=0.5,
        timeline=[
            (15, "faults", {"fail_dispatch_job": ["*"], "fail_advance": ["*"]}),
            (15, "api_fault", True),
            (25, "faults", {}),
            (25, "api_fault", False),
        ],
    ),
}


# ── environment ──────────────────────────────────────────────────────────────


def make_env(tmp: Path) -> RecoveryEnv:
    redis_server = RecoveryRedis()
    redis_server.start_new()
    marker_dir = tmp / "markers"
    marker_dir.mkdir(parents=True, exist_ok=True)
    env = RecoveryEnv(
        redis=redis_server,
        queue=f"sceneops.obs.{uuid.uuid4().hex[:6]}",
        tmp=tmp,
        root_prefix="obs",
        artifact=ArtifactSettings(
            backend="minio",
            root_uri="s3://sceneops-test-obs/artifacts/obs",
            endpoint_url=os.environ.get("MINIO_ENDPOINT_URL", "http://localhost:9000"),
            region="ap-northeast-2",
            access_key_id=os.environ.get("MINIO_ROOT_USER", "minioadmin"),
            secret_access_key=os.environ.get("MINIO_ROOT_PASSWORD", "minioadmin"),
        ),
        fault_file=tmp / "faults.json",
        marker_dir=marker_dir,
    )
    env.clear_faults()
    return env


def pipeline_queue(env) -> str:
    return f"{env.queue}.pipeline"


def celery(env):
    return create_celery_app(
        name="obs-client",
        settings=CelerySettings(
            broker_url=env.redis.url,
            result_backend=env.redis.result_url,
            job_queue=env.queue,
            pipeline_queue=pipeline_queue(env),
            task_default_queue=env.queue,
        ),
    )


def worker(env, name, concurrency, *, scenario, control_file, pipeline=False):
    return WorkerProcess(
        env,
        name,
        app="load_worker.celery_app",
        concurrency=concurrency,
        queue=pipeline_queue(env) if pipeline else env.queue,
        extra_env={
            "DISPATCH_WORKER_NAME": name,
            "LOAD_CONTROL_FILE": str(control_file),
            "SCENEOPS_WORKER_EXECUTION__CELERY__PIPELINE_QUEUE": pipeline_queue(env),
            "SCENEOPS_WORKER_RUNTIME__JOB_LEASE_SECONDS": str(scenario.lease_s),
        },
    )


class FaultableJobBackend:
    """The API's Celery job backend, whose send raises while ``failing``: the
    broker is unreachable from the API after the QUEUED commit."""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.failing = False

    async def dispatch_job(self, job_id):
        if self.failing:
            raise ConnectionError("injected: broker unreachable (API run_job)")
        return await self.inner.dispatch_job(job_id)


def facades(env, app):
    from app.platform.executions.backends.celery import (
        CeleryJobExecutionBackend,
        CeleryPipelineExecutionBackend,
    )
    from app.platform.jobs.dispatch_facade import JobDispatchFacade
    from app.platform.pipelines.dispatch_facade import PipelineDispatchFacade

    backend = FaultableJobBackend(
        CeleryJobExecutionBackend(app=app, job_queue=env.queue)
    )
    jobs = JobDispatchFacade(
        session_factory=get_async_sessionmaker(), job_backend=backend
    )
    pipelines = PipelineDispatchFacade(
        session_factory=get_async_sessionmaker(),
        pipeline_backend=CeleryPipelineExecutionBackend(
            app=app, pipeline_queue=pipeline_queue(env)
        ),
    )
    return jobs, pipelines, backend


async def truncate() -> None:
    async with get_async_sessionmaker()() as session:
        await session.execute(
            text(
                "TRUNCATE job_events, pipeline_task_runs, pipeline_runs, jobs, "
                "execution_records"
            )
        )
        await session.commit()


# ── the run ──────────────────────────────────────────────────────────────────


class Recorder:
    def __init__(self) -> None:
        self.submits: list[dict] = []
        self.samples: list[dict] = []
        self.recovery: list[dict] = []
        self.timeline: list[dict] = []
        self.errors: list[dict] = []


async def submit_job(jobs_facade, recorder, job_type: JobType) -> None:
    job_id = f"job-obs-{uuid.uuid4().hex[:12]}"
    async with get_async_sessionmaker()() as session:
        await PostgresJobRepository(session).create(
            JobManifest(job_id=job_id, type=job_type, status=JobStatus.PENDING)
        )
        await session.commit()
    t = time.time()
    recorder.submits.append(
        {"t": t, "kind": "job", "id": job_id, "type": job_type.value}
    )
    try:
        await jobs_facade.dispatch(job_id)
    except Exception as exc:  # noqa: BLE001 - the API answers 500; the Job stays QUEUED
        recorder.errors.append(
            {"t": time.time(), "id": job_id, "error": repr(exc)[:200]}
        )


async def submit_run(pipelines_facade, recorder) -> None:
    from app.platform.pipelines.service import PipelineService

    async with get_async_sessionmaker()() as session:
        detail = await PipelineService(
            pipeline_repository=PostgresPipelineRunRepository(session),
            task_repository=PostgresPipelineTaskRunRepository(session),
        ).create_pipeline_run(
            CreatePipelineRunRequest(
                type=PipelineType.EPISODE_LEARNING_DATA_BUILDING,
                params={"obs": uuid.uuid4().hex},
                force=True,
            )
        )
        await session.commit()
    run_id = detail.pipeline_run.pipeline_run_id
    recorder.submits.append({"t": time.time(), "kind": "run", "id": run_id})
    await pipelines_facade.dispatch(run_id)


async def arrivals(scenario, jobs_facade, pipelines_facade, recorder, stop) -> None:
    rate = scenario.job_rate + scenario.pipeline_rate
    rng = random.Random(scenario.name)
    next_at = time.monotonic()
    types, weights = zip(*STANDALONE_MIX)
    while not stop.is_set():
        next_at += rng.expovariate(rate)
        delay = next_at - time.monotonic()
        if delay > 0:
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
                return
            except TimeoutError:
                pass
        if rng.random() < scenario.pipeline_rate / rate:
            await submit_run(pipelines_facade, recorder)
        else:
            await submit_job(
                jobs_facade, recorder, rng.choices(types, weights=weights)[0]
            )


async def recovery_loop(scenario, dispatcher, recorder, stop) -> None:
    while not stop.is_set():
        report = await recover_execution(
            session_factory=get_async_sessionmaker(),
            dispatcher=dispatcher,
            resend_after_seconds=scenario.resend_after_s,
        )
        t = time.time()
        for sweep, actions in (
            ("leases", report.leases),
            ("dispatches", report.dispatches),
            ("advances", report.advances),
        ):
            for action in actions:
                recorder.recovery.append(
                    {
                        "t": t,
                        "sweep": sweep,
                        "id": getattr(action, "job_id", None)
                        or getattr(action, "resource_id", None),
                        "outcome": action.outcome.value,
                    }
                )
        try:
            await asyncio.wait_for(stop.wait(), timeout=scenario.recovery_interval_s)
        except TimeoutError:
            pass


async def sampler(env, recorder, stop, *, live_window_s: float = 10.0) -> None:
    broker = redis.Redis.from_url(env.redis.url)
    while not stop.is_set():
        t0 = time.perf_counter()
        async with get_async_sessionmaker()() as session:
            snapshot = await PostgresExecutionMetrics(session).snapshot(
                window_seconds=live_window_s
            )
        cost_ms = (time.perf_counter() - t0) * 1000
        try:
            broker_state = {
                "jobs_queue": broker.llen(env.queue),
                "pipeline_queue": broker.llen(pipeline_queue(env)),
                "unacked": broker.hlen("unacked"),
            }
        except redis.RedisError as exc:
            broker_state = {"error": repr(exc)[:120]}
        recorder.samples.append(
            {
                "t": time.time(),
                "snapshot_ms": round(cost_ms, 2),
                "broker": broker_state,
                "snapshot": json.loads(json.dumps(asdict(snapshot), default=str)),
            }
        )
        try:
            await asyncio.wait_for(stop.wait(), timeout=max(0.0, 1.0 - cost_ms / 1000))
        except TimeoutError:
            pass


async def idle() -> bool:
    async with get_async_sessionmaker()() as session:
        row = (
            await session.execute(
                text(
                    "SELECT (SELECT count(*) FROM jobs WHERE status IN "
                    "('pending','queued','running')) + (SELECT count(*) FROM "
                    "pipeline_runs WHERE status IN ('queued','running'))"
                )
            )
        ).scalar_one()
    return row == 0


async def run_scenario(env, scenario: Scenario, out_dir: Path) -> dict:
    await truncate()
    env.redis.flush()
    env.clear_faults()
    for marker in env.marker_dir.iterdir():
        marker.unlink()
    for log in env.tmp.glob("worker-*.log"):
        log.unlink()
    control_file = env.tmp / "control.json"
    control_file.write_text(json.dumps(scenario.control))

    workers = {
        name: worker(env, name, n, scenario=scenario, control_file=control_file)
        for name, n in scenario.job_workers
    }
    workers["pipeline"] = worker(
        env, "pipeline", 2, scenario=scenario, control_file=control_file, pipeline=True
    )
    for w in workers.values():
        w.start()

    app = celery(env)
    jobs_facade, pipelines_facade, api_backend = facades(env, app)
    dispatcher = CeleryExecutionDispatcher(
        app=app, job_queue=env.queue, pipeline_queue=pipeline_queue(env)
    )
    recorder = Recorder()
    stop_arrivals, stop_all = asyncio.Event(), asyncio.Event()
    started = time.time()

    async def timeline():
        for at, action, arg in sorted(scenario.timeline, key=lambda e: e[0]):
            await asyncio.sleep(max(0.0, started + at - time.time()))
            recorder.timeline.append({"t": time.time(), "action": action, "arg": arg})
            if action == "kill":
                workers[arg].kill()
            elif action == "control":
                control_file.write_text(json.dumps(arg))
            elif action == "faults":
                env.set_faults(**arg)
            elif action == "api_fault":
                api_backend.failing = bool(arg)

    tasks = [
        asyncio.create_task(
            arrivals(scenario, jobs_facade, pipelines_facade, recorder, stop_arrivals)
        ),
        asyncio.create_task(recovery_loop(scenario, dispatcher, recorder, stop_all)),
        asyncio.create_task(sampler(env, recorder, stop_all)),
        asyncio.create_task(timeline()),
    ]
    await asyncio.sleep(scenario.duration_s)
    stop_arrivals.set()
    arrivals_end = time.time()
    drain_deadline = time.monotonic() + scenario.drain_timeout_s
    while time.monotonic() < drain_deadline and not await idle():
        await asyncio.sleep(0.5)
    drained = await idle()
    await asyncio.sleep(2.0)  # a last sample of the idle state
    stop_all.set()
    await asyncio.gather(*tasks, return_exceptions=True)
    finished = time.time()
    for w in workers.values():
        w.stop()

    async with get_async_sessionmaker()() as session:
        final = await PostgresExecutionMetrics(session).snapshot(
            window_seconds=finished - started + 5
        )
        jobs = await _rows(
            session,
            """
            SELECT job_id, type, status, pipeline_run_id, lease_generation,
                   error->>'type' AS error_type,
                   extract(epoch FROM created_at) AS created_at,
                   extract(epoch FROM queued_at) AS queued_at,
                   extract(epoch FROM enqueued_at) AS enqueued_at,
                   extract(epoch FROM locked_at) AS locked_at,
                   extract(epoch FROM started_at) AS started_at,
                   extract(epoch FROM finished_at) AS finished_at
              FROM jobs
            """,
        )
        runs = await _rows(
            session,
            """
            SELECT pipeline_run_id, status,
                   extract(epoch FROM created_at) AS created_at,
                   extract(epoch FROM finished_at) AS finished_at
              FROM pipeline_runs
            """,
        )
        events_written = await _rows(
            session, "SELECT type, level, count(*) AS n FROM job_events GROUP BY 1, 2"
        )
        records = await _rows(
            session,
            "SELECT execution_kind, count(*) AS n FROM execution_records GROUP BY 1",
        )

    markers = (
        [
            json.loads(line)
            for line in (env.marker_dir / "events.jsonl").read_text().splitlines()
            if line
        ]
        if (env.marker_dir / "events.jsonl").exists()
        else []
    )
    raw = {
        "scenario": asdict(scenario),
        "started": started,
        "arrivals_end": arrivals_end,
        "finished": finished,
        "drained": drained,
        "submits": recorder.submits,
        "submit_errors": recorder.errors,
        "timeline": recorder.timeline,
        "recovery": recorder.recovery,
        "samples": recorder.samples,
        "markers": markers,
        "final_snapshot": json.loads(json.dumps(asdict(final), default=str)),
        "jobs": jobs,
        "runs": runs,
        "job_events": events_written,
        "execution_records": records,
        # A refused claim (a duplicate message): one "job claim refused" warning,
        # or a Celery task failure where the task still raises it.
        "claim_refusals": {
            name: w.log().count("job claim refused")
            + w.log().count("raised unexpected: RuntimeError")
            for name, w in workers.items()
        },
    }
    (out_dir / f"{scenario.name}.raw.json").write_text(json.dumps(raw, default=str))
    return raw


async def _rows(session, sql: str) -> list[dict]:
    result = await session.execute(text(sql))
    return [
        {k: (float(v) if hasattr(v, "is_finite") else v) for k, v in row.items()}
        for row in result.mappings().all()
    ]


# ── analysis ─────────────────────────────────────────────────────────────────


def percentiles(values: list[float]) -> dict:
    values = sorted(v for v in values if v is not None)
    if not values:
        return {"n": 0}

    def at(q):
        k = (len(values) - 1) * q
        lo, hi = math.floor(k), math.ceil(k)
        return values[lo] + (values[hi] - values[lo]) * (k - lo)

    return {
        "n": len(values),
        "p50": round(at(0.5), 3),
        "p95": round(at(0.95), 3),
        "p99": round(at(0.99), 3),
        "max": round(values[-1], 3),
        "mean": round(sum(values) / len(values), 3),
    }


def analyse(raw: dict) -> dict:
    t0 = raw["started"]
    submits = {s["id"]: s["t"] for s in raw["submits"]}
    starts: dict[str, list[tuple[float, int]]] = {}
    for m in raw["markers"]:
        if m["event"] == "start":
            starts.setdefault(m["id"], []).append((m["t"], m.get("generation", 0)))
    requeues: dict[str, list[float]] = {}
    for r in raw["recovery"]:
        if r["sweep"] == "leases" and r["outcome"] == "requeued":
            requeues.setdefault(r["id"], []).append(r["t"])

    truth_wait, metric_wait, enqueued_wait, under = {}, {}, {}, []
    intervals = []  # (enter queue, leave queue) by ground truth
    for job in raw["jobs"]:
        submitted = submits.get(job["job_id"], job["created_at"])
        first = min((t for t, _ in starts.get(job["job_id"], [])), default=None)
        if first is not None:
            truth_wait.setdefault(job["type"], []).append(first - submitted)
        intervals.append((submitted, first or raw["finished"]))
        for requeued in requeues.get(job["job_id"], []):
            later = [t for t, _ in starts.get(job["job_id"], []) if t > requeued]
            intervals.append((requeued, min(later) if later else raw["finished"]))
        if job["locked_at"] and job.get("enqueued_at"):
            enqueued_wait.setdefault(job["type"], []).append(
                job["locked_at"] - job["enqueued_at"]
            )
        if job["locked_at"] and job["queued_at"]:
            metric = job["locked_at"] - job["queued_at"]
            metric_wait.setdefault(job["type"], []).append(metric)
            if first is not None and (first - submitted) - metric > 1.0:
                under.append(round((first - submitted) - metric, 2))

    series = []
    for sample in raw["samples"]:
        t = sample["t"]
        snap = sample["snapshot"]
        backlog = snap["backlog"]
        waiting = [t - a for a, b in intervals if a <= t < b]
        series.append(
            {
                "t": round(t - t0, 1),
                "queued": sum(b["queued"] for b in backlog),
                "oldest_queued_age_s": round(
                    max(
                        (float(b["oldest_queued_age_s"] or 0) for b in backlog),
                        default=0,
                    ),
                    2,
                ),
                "true_queued": len(waiting),
                "true_oldest_age_s": round(max(waiting, default=0), 2),
                "running": sum(b["running"] for b in backlog),
                "lease_expired": sum(b["lease_expired"] for b in backlog),
                "oldest_heartbeat_age_s": round(
                    max(
                        (float(b["oldest_heartbeat_age_s"] or 0) for b in backlog),
                        default=0,
                    ),
                    2,
                ),
                "finished_10s": sum(j["succeeded"] + j["failed"] for j in snap["jobs"]),
                "failed_10s": sum(j["failed"] for j in snap["jobs"]),
                "broker_jobs": sample["broker"].get("jobs_queue"),
                "broker_unacked": sample["broker"].get("unacked"),
                "runs_waiting": {
                    w["reason"]: [w["runs"], round(float(w["oldest_wait_s"] or 0), 1)]
                    for w in snap["pipelines_waiting"]
                },
                "recovery_10s": snap["recovery"],
                "snapshot_ms": sample["snapshot_ms"],
            }
        )

    finished_jobs = [j for j in raw["jobs"] if j["finished_at"]]
    span = raw["finished"] - t0
    recovery_counts: dict[str, int] = {}
    for r in raw["recovery"]:
        key = f"{r['sweep']}:{r['outcome']}"
        recovery_counts[key] = recovery_counts.get(key, 0) + 1
    handler_runs = sum(1 for m in raw["markers"] if m["event"] == "start")
    claim_refusals = raw.get("claim_refusals")
    return {
        "scenario": raw["scenario"]["name"],
        "drained": raw["drained"],
        "submitted": {
            "jobs": sum(1 for s in raw["submits"] if s["kind"] == "job"),
            "runs": sum(1 for s in raw["submits"] if s["kind"] == "run"),
            "api_send_errors": len(raw["submit_errors"]),
        },
        "jobs_total": len(raw["jobs"]),
        "jobs_by_status": _count(raw["jobs"], "status"),
        "runs_by_status": _count(raw["runs"], "status"),
        "handler_runs": handler_runs,
        "claim_refusals_in_worker_logs": claim_refusals,
        "recovery_actions": recovery_counts,
        "throughput_jobs_per_min_overall": round(len(finished_jobs) / span * 60, 1),
        "queue_wait_truth": {k: percentiles(v) for k, v in truth_wait.items()},
        "queue_wait_metric": {k: percentiles(v) for k, v in metric_wait.items()},
        "queue_wait_enqueued": {k: percentiles(v) for k, v in enqueued_wait.items()},
        "queue_wait_underestimated_by_gt_1s": percentiles(under),
        "final_snapshot": raw["final_snapshot"],
        "pipeline_e2e_truth": percentiles(
            [
                r["finished_at"] - r["created_at"]
                for r in raw["runs"]
                if r["finished_at"]
            ]
        ),
        "timeline": [
            {"t": round(e["t"] - t0, 1), "action": e["action"], "arg": e["arg"]}
            for e in raw["timeline"]
        ],
        "sampler_cost_ms": percentiles([s["snapshot_ms"] for s in raw["samples"]]),
        "series": series,
        "job_events": raw["job_events"],
        "execution_records": raw["execution_records"],
    }


def _count(rows, key) -> dict:
    out: dict = {}
    for row in rows:
        out[row[key]] = out.get(row[key], 0) + 1
    return out


def write_summary(out: Path, name: str, summary: dict) -> None:
    (out / f"{name}.summary.json").write_text(
        json.dumps(summary, indent=1, default=str)
    )
    brief = {k: v for k, v in summary.items() if k not in ("series", "final_snapshot")}
    print(json.dumps(brief, default=str), flush=True)


async def main(args) -> None:
    database = urlsplit(
        os.environ["SCENEOPS_DATABASE_URL"].replace("+asyncpg", "")
    ).path.lstrip("/")
    check_disposable_database(database)
    args.out.mkdir(parents=True, exist_ok=True)
    if args.reanalyse:
        for name in args.scenarios:
            raw = json.loads((args.out / f"{name}.raw.json").read_text())
            write_summary(args.out, name, analyse(raw))
        return
    env = make_env(args.out / "env")
    try:
        for name in args.scenarios:
            scenario = SCENARIOS[name]
            print(f"== {name}", flush=True)
            raw = await run_scenario(env, scenario, args.out)
            write_summary(args.out, name, analyse(raw))
    finally:
        env.redis.remove()
        await dispose_async_engine()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--scenarios",
        type=lambda s: s.split(","),
        default=list(SCENARIOS),
    )
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--reanalyse", action="store_true", help="re-read <out>/<scenario>.raw.json"
    )
    asyncio.run(main(parser.parse_args()))
