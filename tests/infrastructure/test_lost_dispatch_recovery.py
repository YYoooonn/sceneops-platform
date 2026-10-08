"""Lost broker messages and execution recovery, with real workers and a real broker.

PostgreSQL commits the state that needs asynchronous work, then a Celery message
is sent. These tests lose that message every way it can be lost -- the broker is
down at the send, the broker accepts it and then loses it, a worker's send fails
after its commit -- and show that the production recovery command
(``sceneops-worker recover``) re-sends it from durable state until the work
completes, and that the duplicates this creates run each Job once and advance
each run once.

Real PostgreSQL (the disposable database of `make test-infrastructure
SUITE=recovery`), a throwaway Redis, and Celery worker subprocesses running the
production worker (``dispatch_worker``: every handler is a probe, and the
dispatcher's sends can be failed). The API's dispatch runs in this process
through its own facades. Recovery runs with a 2 s resend threshold.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from collections import Counter

import pytest
from sqlalchemy import text

from recovery_support import RECOVERY_FIXTURES, WorkerProcess, wait_until
from sceneops_core.config import CelerySettings
from sceneops_core.jobs.schemas import JobManifest, JobStatus, JobType
from sceneops_core.pipelines.schemas import CreatePipelineRunRequest, PipelineType
from sceneops_db.postgres.jobs import PostgresJobRepository
from sceneops_db.postgres.pipelines import (
    PostgresPipelineRunRepository,
    PostgresPipelineTaskRunRepository,
)
from sceneops_db.session import get_async_sessionmaker
from sceneops_execution.executions.celery_factory import create_celery_app
from sceneops_execution.executions.dispatcher import CeleryExecutionDispatcher

pytestmark = pytest.mark.usefixtures(*RECOVERY_FIXTURES)

RESEND_AFTER = 2.0
DEAD_BROKER = "redis://127.0.0.1:1/0"


def pipeline_queue(env) -> str:
    return f"{env.queue}.pipeline"


def worker(env, name: str, *, pipeline: bool = False) -> WorkerProcess:
    return WorkerProcess(
        env,
        name,
        app="dispatch_worker.celery_app",
        concurrency=2,
        queue=pipeline_queue(env) if pipeline else env.queue,
        extra_env={
            "DISPATCH_WORKER_NAME": name,
            "SCENEOPS_WORKER_EXECUTION__CELERY__PIPELINE_QUEUE": pipeline_queue(env),
        },
    )


def celery(env, broker_url: str | None = None):
    return create_celery_app(
        name="dispatch-test-client",
        settings=CelerySettings(
            broker_url=broker_url or env.redis.url,
            result_backend=env.redis.result_url,
            job_queue=env.queue,
            pipeline_queue=pipeline_queue(env),
            task_default_queue=env.queue,
        ),
    )


def job_facade(env, broker_url: str | None = None):
    """``POST /jobs/{id}/execute``'s dispatch path."""
    from sceneops_execution.executions.backends.celery import CeleryJobExecutionBackend
    from sceneops_execution.jobs.dispatch_facade import JobDispatchFacade

    return JobDispatchFacade(
        session_factory=get_async_sessionmaker(),
        job_backend=CeleryJobExecutionBackend(
            app=celery(env, broker_url), job_queue=env.queue
        ),
    )


def pipeline_facade(env):
    """``POST /pipelines/runs/{id}/execute``'s dispatch path."""
    from sceneops_execution.executions.backends.celery import (
        CeleryPipelineExecutionBackend,
    )
    from sceneops_execution.pipelines.dispatch_facade import PipelineDispatchFacade

    return PipelineDispatchFacade(
        session_factory=get_async_sessionmaker(),
        pipeline_backend=CeleryPipelineExecutionBackend(
            app=celery(env), pipeline_queue=pipeline_queue(env)
        ),
    )


def recover_process(env, *, broker_url: str | None = None) -> subprocess.Popen:
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "sceneops_worker.main",
            "recover",
            "--resend-after-seconds",
            str(RESEND_AFTER),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(env.tmp),  # no .env.local: the dev stack's hostnames must not leak
        env={
            **os.environ,
            "SCENEOPS_WORKER_EXECUTION__CELERY__BROKER_URL": broker_url
            or env.redis.url,
            "SCENEOPS_WORKER_EXECUTION__CELERY__RESULT_BACKEND": env.redis.result_url,
            "SCENEOPS_WORKER_EXECUTION__CELERY__JOB_QUEUE": env.queue,
            "SCENEOPS_WORKER_EXECUTION__CELERY__PIPELINE_QUEUE": pipeline_queue(env),
        },
    )


def summary_of(process: subprocess.Popen) -> dict:
    stdout, stderr = process.communicate(timeout=120)
    assert process.returncode == 0, stderr[-4000:]
    return json.loads(stdout.strip().splitlines()[-1])


def recover(env, **kw) -> dict:
    """``sceneops-worker recover``: one pass; its JSON summary."""
    return summary_of(recover_process(env, **kw))


def resent(summary: dict, sweep: str, resource_id: str) -> bool:
    return any(
        a["resource_id"] == resource_id and a["outcome"] == "resent"
        for a in summary[sweep]
    )


def recover_until_resent(env, sweep: str, resource_id: str, timeout=60) -> float:
    """Run passes until one re-sends ``resource_id``; the seconds it took."""
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        if resent(recover(env), sweep, resource_id):
            return time.monotonic() - started
        time.sleep(0.5)
    raise TimeoutError(f"{sweep} of {resource_id} never re-sent")


def events(env, key: str | None = None) -> list[dict]:
    path = env.marker_dir / "events.jsonl"
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text().splitlines() if line]
    return [r for r in rows if key is None or r["id"] == key]


def wait_event(env, name: str, key: str | None = None, timeout=60) -> None:
    wait_until(
        lambda: any(e["event"] == name for e in events(env, key)),
        timeout=timeout,
        interval=0.1,
    )


async def rows(sql: str, **params) -> list[dict]:
    async with get_async_sessionmaker()() as session:
        result = await session.execute(text(sql), params)
        return [dict(r) for r in result.mappings().all()]


async def job_row(job_id: str) -> dict:
    (row,) = await rows(
        "SELECT status, lease_generation, (SELECT count(*) FROM execution_records "
        "WHERE resource_id = :j) AS records FROM jobs WHERE job_id = :j",
        j=job_id,
    )
    return row


async def wait_job(job_id: str, status: str, timeout=60) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if (await job_row(job_id))["status"] == status:
            return
        time.sleep(0.2)
    raise TimeoutError(f"{job_id} never {status}: {await job_row(job_id)}")


async def wait_run(run_id: str, status: str, timeout=90) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        (run,) = await rows(
            "SELECT status FROM pipeline_runs WHERE pipeline_run_id = :r", r=run_id
        )
        if run["status"] == status:
            return
        time.sleep(0.2)
    raise TimeoutError(f"{run_id} never {status}")


async def create_job(status: JobStatus = JobStatus.PENDING) -> str:
    job_id = f"job-lost-{uuid.uuid4().hex[:10]}"
    async with get_async_sessionmaker()() as session:
        await PostgresJobRepository(session).create(
            JobManifest(job_id=job_id, type=JobType.CURATE_EPISODES, status=status)
        )
        await session.commit()
    return job_id


async def create_run() -> str:
    from sceneops_execution.pipelines.service import PipelineService

    async with get_async_sessionmaker()() as session:
        detail = await PipelineService(
            pipeline_repository=PostgresPipelineRunRepository(session),
            task_repository=PostgresPipelineTaskRunRepository(session),
        ).create_pipeline_run(
            CreatePipelineRunRequest(
                type=PipelineType.EPISODE_LEARNING_DATA_BUILDING,
                params={"test": uuid.uuid4().hex},
                force=True,
            )
        )
        await session.commit()
    return detail.pipeline_run.pipeline_run_id


async def assert_completed_once(env, run_id: str) -> None:
    """The run succeeded with one Job per task, each run by its handler once."""
    jobs = await rows(
        "SELECT job_id, pipeline_task_id, status FROM jobs WHERE pipeline_run_id = :r",
        r=run_id,
    )
    assert Counter(j["pipeline_task_id"] for j in jobs) == {
        "align_episode": 1,
        "export_learning_data": 1,
    }
    assert {j["status"] for j in jobs} == {"succeeded"}
    for job in jobs:
        assert [e["event"] for e in events(env, job["job_id"])].count("ran") == 1


def queue_length(env, queue: str) -> int:
    out = subprocess.run(
        ["docker", "exec", env.redis.name, "redis-cli", "LLEN", queue],
        capture_output=True,
        text=True,
    ).stdout.strip()
    return int(out or 0)


# ── run_job lost at the API ───────────────────────────────────────────────────


async def test_a_job_dispatch_lost_to_a_broker_outage_is_sent_again(env):
    job_id = await create_job()
    env.redis.stop()
    try:
        with pytest.raises(Exception, match="(?i)connect|refused|error"):
            await job_facade(env).dispatch(job_id)  # the API answers 500
    finally:
        env.redis.start()
    assert (await job_row(job_id)) == {
        "status": "queued",
        "lease_generation": 0,
        "records": 0,
    }
    assert queue_length(env, env.queue) == 0

    with worker(env, "jobs"):
        recover_until_resent(env, "dispatches", job_id)
        await wait_job(job_id, "succeeded")

    assert await job_row(job_id) == {
        "status": "succeeded",
        "lease_generation": 1,
        "records": 1,
    }
    assert [e["event"] for e in events(env, job_id)] == ["ran"]


async def test_a_message_the_broker_accepted_and_lost_is_sent_again(env):
    job_id = await create_job()
    subprocess.run(["docker", "exec", env.redis.name, "redis-cli", "SAVE"], check=True)
    await job_facade(env).dispatch(job_id)  # accepted: the API answers 200
    assert queue_length(env, env.queue) == 1
    # The broker dies before persisting it (SIGKILL; its last snapshot is older).
    subprocess.run(["docker", "kill", env.redis.name], capture_output=True, check=True)
    env.redis.start()
    assert queue_length(env, env.queue) == 0
    assert (await job_row(job_id))["records"] == 1  # the record of a lost message

    with worker(env, "jobs"):
        recover_until_resent(env, "dispatches", job_id)
        await wait_job(job_id, "succeeded")

    assert (await job_row(job_id))["records"] == 2
    assert [e["event"] for e in events(env, job_id)] == ["ran"]


# ── a pipeline's own messages ─────────────────────────────────────────────────


async def test_a_task_job_dispatch_lost_by_the_orchestrator_completes_its_pipeline(env):
    run_id = await create_run()
    env.set_faults(fail_dispatch_job=["*"])

    with worker(env, "jobs"), worker(env, "pipeline", pipeline=True):
        await pipeline_facade(env).dispatch(run_id)
        wait_event(env, "send_failed:run_job")
        env.clear_faults()
        (task_job,) = await rows(
            "SELECT job_id FROM jobs WHERE pipeline_run_id = :r", r=run_id
        )
        assert (await job_row(task_job["job_id"]))["status"] == "queued"

        recover_until_resent(env, "dispatches", task_job["job_id"])
        await wait_run(run_id, "succeeded")

    await assert_completed_once(env, run_id)


async def test_a_lost_advance_after_a_finished_job_completes_its_pipeline(env):
    run_id = await create_run()
    env.set_faults(fail_advance=[run_id])

    with worker(env, "jobs"), worker(env, "pipeline", pipeline=True):
        await pipeline_facade(env).dispatch(run_id)
        wait_event(env, "send_failed:advance", run_id)
        env.clear_faults()
        (task_job,) = await rows(
            "SELECT job_id, status FROM jobs WHERE pipeline_run_id = :r", r=run_id
        )
        assert task_job["status"] == "succeeded"  # the run waits on its report

        recover_until_resent(env, "advances", run_id)
        await wait_run(run_id, "succeeded")

    await assert_completed_once(env, run_id)


async def test_a_lost_lease_recovery_send_completes_its_pipeline(env):
    run_id = await create_run()
    with worker(env, "pipeline", pipeline=True):
        await pipeline_facade(env).dispatch(run_id)

        async def submitted() -> bool:
            return bool(
                await rows("SELECT 1 FROM jobs WHERE pipeline_run_id = :r", r=run_id)
            )

        deadline = time.monotonic() + 60
        while not await submitted():
            assert time.monotonic() < deadline
            time.sleep(0.2)
        (task_job,) = await rows(
            "SELECT job_id FROM jobs WHERE pipeline_run_id = :r", r=run_id
        )
        job_id = task_job["job_id"]
        # A worker consumed the message, claimed the Job and died.
        wait_until(lambda: queue_length(env, env.queue) == 1, timeout=30)
        subprocess.run(
            ["docker", "exec", env.redis.name, "redis-cli", "DEL", env.queue],
            check=True,
            capture_output=True,
        )
        async with get_async_sessionmaker()() as session:
            await PostgresJobRepository(session).claim_for_run(
                job_id,
                worker_id="celery:dead",
                runnable_statuses={JobStatus.QUEUED},
                lease_seconds=60,
            )
            await session.execute(
                text(
                    "UPDATE jobs SET lease_expires_at = now() - interval '1 second' "
                    "WHERE job_id = :j"
                ),
                {"j": job_id},
            )
            await session.commit()

        lost = recover(env, broker_url=DEAD_BROKER)
        assert [a["outcome"] for a in lost["leases"] if a["job_id"] == job_id] == [
            "dispatch_failed"
        ]
        with worker(env, "jobs"):
            recover_until_resent(env, "dispatches", job_id)
            await wait_run(run_id, "succeeded")

    await assert_completed_once(env, run_id)
    assert (await job_row(job_id))["lease_generation"] == 2


# ── duplicates and racing recovery actors ─────────────────────────────────────


async def test_duplicate_messages_run_each_job_once_and_advance_each_task_once(env):
    run_id = await create_run()
    with worker(env, "pipeline", pipeline=True):
        await pipeline_facade(env).dispatch(run_id)
        wait_until(lambda: queue_length(env, env.queue) == 1, timeout=30)
        (task_job,) = await rows(
            "SELECT job_id FROM jobs WHERE pipeline_run_id = :r", r=run_id
        )
        job_id = task_job["job_id"]
        # No job worker yet: the message waits, and recovery cannot tell it
        # from a lost one. It re-sends it once per threshold.
        for expected in (2, 3, 4):
            time.sleep(RESEND_AFTER + 0.2)
            assert resent(recover(env), "dispatches", job_id)
            assert queue_length(env, env.queue) == expected
        dispatcher = CeleryExecutionDispatcher(
            app=celery(env), job_queue=env.queue, pipeline_queue=pipeline_queue(env)
        )
        for _ in range(5):  # and five stray advances for the waiting run
            dispatcher.advance_pipeline(run_id)

        with worker(env, "jobs"):
            await wait_run(run_id, "succeeded")

    await assert_completed_once(env, run_id)
    assert (await job_row(job_id))["records"] == 4  # 1 dispatch + 3 resends


async def test_racing_recovery_processes_send_each_lost_message_once(env):
    job_ids = [await create_job(JobStatus.QUEUED) for _ in range(10)]
    async with get_async_sessionmaker()() as session:
        await session.execute(
            text(
                "UPDATE jobs SET queued_at = now() - interval '1 hour' "
                "WHERE job_id = ANY(:ids)"
            ),
            {"ids": job_ids},
        )
        await session.commit()

    passes = [recover_process(env) for _ in range(3)]
    summaries = [summary_of(p) for p in passes]

    for job_id in job_ids:
        outcomes = [
            a["outcome"]
            for s in summaries
            for a in s["dispatches"]
            if a["resource_id"] == job_id
        ]
        assert outcomes.count("resent") == 1, outcomes
    assert queue_length(env, env.queue) == 10
    with worker(env, "jobs"):
        for job_id in job_ids:
            await wait_job(job_id, "succeeded")
    for job_id in job_ids:
        assert [e["event"] for e in events(env, job_id)] == ["ran"]
