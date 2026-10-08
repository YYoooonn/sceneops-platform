"""Worker loss and the Job ownership lease, with real workers.

Real PostgreSQL (the disposable database of `make test-infrastructure
SUITE=recovery`), real MinIO (its disposable bucket), a throwaway Redis and Celery
worker subprocesses running the production worker (``lease_worker``: the real
JobRunner, lease keeper and fencing; only the handler of the probe Job type is a
probe). Workers are killed with SIGKILL, paused with SIGSTOP, or lose a pool
child; recovery is the production command ``sceneops-worker recover``.

Workers run with a 2 s lease (renewed every 0.67 s) so the tests wait seconds,
not minutes. Waits poll for an observable fact (an event the probe wrote, a row
PostgreSQL holds); the only timed wait is the lease itself passing.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
import uuid

import pytest
from sqlalchemy import text

from recovery_support import (
    RECOVERY_FIXTURES,
    WorkerProcess,
    async_wait_until,
    wait_until,
)
from sceneops_core.config import CelerySettings
from sceneops_core.constants.tasks import JOB_RUN_TASK
from sceneops_core.jobs.schemas import JobManifest, JobStatus, JobType
from sceneops_db.postgres.jobs import PostgresJobRepository
from sceneops_db.session import get_async_sessionmaker
from sceneops_storage import create_artifact_store
from sceneops_execution.executions.celery_factory import create_celery_app

pytestmark = pytest.mark.usefixtures(*RECOVERY_FIXTURES)

LEASE_SECONDS = 2.0
# Served by lease_worker's probe handler.
PROBE_JOB_TYPE = JobType.CURATE_EPISODES


def lease_worker(env, name: str, **extra_env: str) -> WorkerProcess:
    return WorkerProcess(
        env,
        name,
        app="lease_worker.celery_app",
        concurrency=1,
        extra_env={
            "LEASE_WORKER_NAME": name,
            "SCENEOPS_WORKER_RUNTIME__JOB_LEASE_SECONDS": str(LEASE_SECONDS),
            "LEASE_BLOCK_SECONDS": str(3 * LEASE_SECONDS),
            **extra_env,
        },
    )


async def create_probe_job() -> str:
    job_id = f"job-lease-{uuid.uuid4().hex[:10]}"
    async with get_async_sessionmaker()() as session:
        await PostgresJobRepository(session).create(
            JobManifest(job_id=job_id, type=PROBE_JOB_TYPE, status=JobStatus.QUEUED)
        )
        await session.commit()
    return job_id


def send(env, job_id: str) -> str:
    """One job message, as the API's dispatch sends it; returns its task id."""
    client = create_celery_app(
        name="lease-test-client",
        settings=CelerySettings(
            broker_url=env.redis.url,
            result_backend=env.redis.result_url,
            job_queue=env.queue,
            task_default_queue=env.queue,
        ),
    )
    return client.send_task(
        JOB_RUN_TASK, args=[job_id], queue=env.queue, routing_key=env.queue
    ).id


def recover(env, *, broker_url: str | None = None) -> list[dict]:
    """``sceneops-worker recover``: one pass; the lease sweep's actions."""
    result = subprocess.run(
        [sys.executable, "-m", "sceneops_worker.main", "recover"],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(env.tmp),  # no .env.local: the dev stack's hostnames must not leak
        env={
            **os.environ,
            "SCENEOPS_WORKER_EXECUTION__CELERY__BROKER_URL": broker_url
            or env.redis.url,
            "SCENEOPS_WORKER_EXECUTION__CELERY__RESULT_BACKEND": env.redis.result_url,
            "SCENEOPS_WORKER_EXECUTION__CELERY__JOB_QUEUE": env.queue,
        },
    )
    assert result.returncode == 0, result.stderr[-4000:]
    return json.loads(result.stdout.strip().splitlines()[-1])["leases"]


def outcomes(actions: list[dict], job_id: str) -> list[str]:
    return [a["outcome"] for a in actions if a["job_id"] == job_id]


def events(env, job_id: str) -> list[dict]:
    path = env.marker_dir / "events.jsonl"
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text().splitlines() if line]
    return [row for row in rows if row["job_id"] == job_id]


def wait_event(
    env,
    job_id: str,
    name: str,
    *,
    worker: str | None = None,
    after: float = 0.0,
    timeout: float = 90,
) -> dict:
    found: list[dict] = []

    def seen() -> bool:
        found[:] = [
            e
            for e in events(env, job_id)
            if e["event"] == name
            and e["t"] >= after
            and (worker is None or e["worker"] == worker)
        ]
        return bool(found)

    wait_until(seen, timeout=timeout, interval=0.1)
    return found[0]


def release(env, job_id: str, worker: str) -> None:
    (env.marker_dir / f"release.{job_id}.{worker}").touch()


async def row(job_id: str) -> dict:
    async with get_async_sessionmaker()() as session:
        result = await session.execute(
            text(
                "SELECT status, worker_id, lease_generation, lease_expires_at, "
                "heartbeat_at, error->>'type' AS error_type, "
                "lease_expires_at < now() AS lease_passed FROM jobs WHERE job_id = :j"
            ),
            {"j": job_id},
        )
        return dict(result.mappings().one())


async def wait_lease_passed(job_id: str) -> None:
    async def passed() -> bool:
        return bool((await row(job_id))["lease_passed"])

    await async_wait_until(passed, timeout=4 * LEASE_SECONDS + 10, interval=0.1)


async def artifact_exists(env, job_id: str) -> bool:
    uri = f"{env.artifact.root_uri}/liveness/{job_id}/output.json"
    return await create_artifact_store(env.artifact).exists(uri)


# ── a whole worker dies after claiming ────────────────────────────────────────


async def test_a_worker_killed_after_its_claim_is_recovered_under_a_new_claim(env):
    job_id = await create_probe_job()
    env.set_faults(in_handler=[job_id])

    with lease_worker(env, "doomed") as doomed:
        task_id = send(env, job_id)
        held = wait_event(env, job_id, "hold:in_handler")
        assert (held["worker_id"], held["generation"]) == (f"celery:{task_id}", 1)
        doomed.kill()  # SIGKILL of the process group: no ack, no renewal

    await wait_lease_passed(job_id)
    assert (await row(job_id))["status"] == "running"  # nothing else moves it

    assert outcomes(recover(env), job_id) == ["requeued"]
    assert outcomes(recover(env), job_id) == []  # QUEUED: nothing to recover

    env.clear_faults()
    with lease_worker(env, "healthy"):
        done = wait_event(env, job_id, "run_returned", worker="healthy")

    assert (done["status"], done["generation"]) == ("succeeded", 2)
    final = await row(job_id)
    assert (final["status"], final["lease_generation"]) == ("succeeded", 2)
    assert final["worker_id"] != f"celery:{task_id}"  # recovery's own message
    assert await artifact_exists(env, job_id)


# ── a pool child dies in its handler; Celery redelivers at once ──────────────


async def test_a_redelivery_after_a_lost_pool_child_is_refused_and_recovery_finishes_it(
    env,
):
    job_id = await create_probe_job()
    env.set_faults(after_artifact=[job_id])

    with lease_worker(env, "worker"):
        task_id = send(env, job_id)
        held = wait_event(env, job_id, "hold:after_artifact")
        assert held["created"] is True
        killed_at = time.time()
        os.kill(held["pid"], signal.SIGKILL)  # the parent survives

        # task_reject_on_worker_lost: the same message, same task id, at once --
        # and the Job is RUNNING under the dead child's claim, so it is refused.
        refused = wait_event(env, job_id, "run_raised", after=killed_at)
        assert refused["worker_id"] == f"celery:{task_id}"
        assert "already running" in refused["error"]
        assert (await row(job_id))["status"] == "running"

        env.clear_faults()
        await wait_lease_passed(job_id)
        assert outcomes(recover(env), job_id) == ["requeued"]
        done = wait_event(env, job_id, "run_returned", after=killed_at)

    assert (done["status"], done["generation"]) == ("succeeded", 2)
    # The second execution converged on the first one's write-once artifact.
    second = [
        e
        for e in events(env, job_id)
        if e["event"] == "after_artifact" and e["generation"] == 2
    ]
    assert [e["created"] for e in second] == [False]


# ── a paused owner wakes up after a redelivery of its own message took over ──


async def test_a_paused_owner_is_fenced_although_the_new_claim_has_its_worker_id(env):
    job_id = await create_probe_job()
    env.set_faults(in_handler=[job_id])

    paused = lease_worker(env, "paused").start()
    try:
        task_id = send(env, job_id)
        wait_event(env, job_id, "hold:in_handler", worker="paused")
        paused.signal(signal.SIGSTOP)  # alive, holding its claim, not renewing
        await wait_lease_passed(job_id)

        # Recovery reclaims the Job but cannot send its message (broker down for
        # it): the Job waits QUEUED, and the broker's own redelivery of the
        # original message is what runs it again.
        dead_broker = "redis://127.0.0.1:1/0"
        assert outcomes(recover(env, broker_url=dead_broker), job_id) == [
            "dispatch_failed"
        ]
        assert (await row(job_id))["status"] == "queued"

        with lease_worker(env, "successor", LEASE_VISIBILITY_TIMEOUT="1"):
            taken = wait_event(env, job_id, "hold:in_handler", worker="successor")
            assert taken["worker_id"] == f"celery:{task_id}"  # same identity
            assert taken["generation"] == 2

            paused.signal(signal.SIGCONT)
            release(env, job_id, "paused")
            late = wait_event(env, job_id, "run_raised", worker="paused")
            assert "JobOwnershipLostError" in late["error"]
            during = await row(job_id)
            assert (during["status"], during["lease_generation"]) == ("running", 2)

            release(env, job_id, "successor")
            done = wait_event(env, job_id, "run_returned", worker="successor")
    finally:
        paused.stop()

    assert (done["status"], done["generation"]) == ("succeeded", 2)
    assert (await row(job_id))["status"] == "succeeded"


# ── a busy handler is not a dead worker ───────────────────────────────────────


async def test_a_handler_blocking_the_event_loop_keeps_its_lease(env):
    job_id = await create_probe_job()
    env.set_faults(block_loop=[job_id])

    with lease_worker(env, "busy"):
        send(env, job_id)
        wait_event(env, job_id, "block_loop")
        blocked_at = time.monotonic()
        expiries, recovery_ran = [], False
        # The handler blocks the worker's event loop for three lease durations.
        while time.monotonic() - blocked_at < 2.5 * LEASE_SECONDS:
            current = await row(job_id)
            assert current["status"] == "running"
            expiries.append(current["lease_expires_at"])
            if not recovery_ran and time.monotonic() - blocked_at > 1.5 * LEASE_SECONDS:
                assert outcomes(recover(env), job_id) == []
                recovery_ran = True
            await asyncio.sleep(0.2)
        done = wait_event(env, job_id, "run_returned", worker="busy")

    assert recovery_ran
    assert (done["status"], done["generation"]) == ("succeeded", 1)
    # Renewed from the keeper's thread while the loop could not run anything.
    assert len(set(expiries)) >= 3


# ── duplicate messages ────────────────────────────────────────────────────────


async def test_duplicate_messages_run_the_job_once(env):
    job_id = await create_probe_job()

    with lease_worker(env, "one"), lease_worker(env, "two"):
        for _ in range(4):
            send(env, job_id)

        def all_answered() -> bool:
            ends = [e for e in events(env, job_id) if e["event"].startswith("run_")]
            return len(ends) == 4

        wait_until(all_answered, timeout=90, interval=0.2)

    entered = [e for e in events(env, job_id) if e["event"] == "in_handler"]
    returned = [e for e in events(env, job_id) if e["event"] == "run_returned"]
    assert len(entered) == 1
    assert [(e["status"], e["generation"]) for e in returned] == [("succeeded", 1)]
    assert (await row(job_id))["lease_generation"] == 1
