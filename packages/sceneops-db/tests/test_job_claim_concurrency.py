"""``PostgresJobRepository.claim_for_run`` under concurrency: of any number of workers
claiming one runnable Job exactly one becomes its owner. Real PostgreSQL.

The claim is one conditional ``UPDATE ... WHERE status IN (PENDING, QUEUED)``, so
the check and the write cannot be separated: a second claimer blocks on the row
lock and PostgreSQL re-evaluates its predicate against the row the first one
committed. The last test writes the same claim as a separate read and write, only
to show what that separation costs.

Ownership is only observable across connections, so the tests commit their own
uniquely-identified Jobs into the disposable database of `make test-integration`.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import text

from sceneops_core.jobs.schemas import JobManifest, JobStatus, JobType
from sceneops_db.postgres.jobs import PostgresJobRepository
from sceneops_db.session import get_async_sessionmaker

WORKERS = 10
_RUNNABLE = {JobStatus.PENDING, JobStatus.QUEUED}


async def _committed_job(job_id: str, status: JobStatus = JobStatus.QUEUED) -> None:
    async with get_async_sessionmaker()() as session:
        await PostgresJobRepository(session).create(
            JobManifest(job_id=job_id, type=JobType.REGISTER_ROBOT_RUN, status=status)
        )
        await session.commit()


async def _stored(job_id: str) -> JobManifest:
    async with get_async_sessionmaker()() as session:
        job = await PostgresJobRepository(session).get(job_id)
    assert job is not None
    return job


async def test_concurrent_claimers_have_exactly_one_owner(unique_id):
    """Every worker issues its claim only once all of them are ready."""
    job_id = unique_id("job-claim")
    await _committed_job(job_id)
    ready = asyncio.Barrier(WORKERS)

    async def claim(worker_id: str) -> str | None:
        async with get_async_sessionmaker()() as session:
            await ready.wait()
            claimed = await PostgresJobRepository(session).claim_for_run(
                job_id,
                worker_id=worker_id,
                runnable_statuses=_RUNNABLE,
                lease_seconds=60,
            )
            await session.commit()
            return claimed.worker_id if claimed is not None else None

    owners = await asyncio.wait_for(
        asyncio.gather(*(claim(f"worker-{i}") for i in range(WORKERS))), timeout=30
    )

    winners = [owner for owner in owners if owner is not None]
    assert len(winners) == 1, owners
    stored = await _stored(job_id)
    assert stored.status == JobStatus.RUNNING
    assert stored.worker_id == winners[0]


async def test_a_second_claim_waits_for_the_first_and_rechecks_the_row(unique_id):
    job_id = unique_id("job-claim")
    await _committed_job(job_id)
    first_claimed = asyncio.Event()
    release_first = asyncio.Event()

    async def first() -> JobManifest | None:
        async with get_async_sessionmaker()() as session:
            claimed = await PostgresJobRepository(session).claim_for_run(
                job_id,
                worker_id="worker-a",
                runnable_statuses=_RUNNABLE,
                lease_seconds=60,
            )
            first_claimed.set()
            await release_first.wait()
            await session.commit()
            return claimed

    async def second() -> JobManifest | None:
        await first_claimed.wait()
        async with get_async_sessionmaker()() as session:
            claimed = await PostgresJobRepository(session).claim_for_run(
                job_id,
                worker_id="worker-b",
                runnable_statuses=_RUNNABLE,
                lease_seconds=60,
            )
            await session.commit()
            return claimed

    first_task = asyncio.create_task(first())
    second_task = asyncio.create_task(second())
    await first_claimed.wait()
    await asyncio.sleep(0.5)
    assert not second_task.done(), "the second claim waits on the first's row lock"

    release_first.set()
    won, lost = await asyncio.wait_for(
        asyncio.gather(first_task, second_task), timeout=10
    )

    assert won is not None and won.worker_id == "worker-a"
    assert lost is None, "the predicate is re-evaluated against the committed row"
    assert (await _stored(job_id)).worker_id == "worker-a"


async def test_a_claim_split_into_read_then_write_gives_every_worker_the_job(
    unique_id,
):
    """Contrast only, not a SceneOps code path: the same claim as a status read
    followed by an unconditional write. Every worker reads a runnable Job, so every
    worker writes and believes it owns the Job; the row keeps the last writer and
    the other claims are lost updates."""
    job_id = unique_id("job-claim")
    await _committed_job(job_id)
    everyone_has_read = asyncio.Barrier(WORKERS)

    async def claim(worker_id: str) -> bool:
        async with get_async_sessionmaker()() as session:
            status = await session.scalar(
                text("SELECT status FROM jobs WHERE job_id = :id"), {"id": job_id}
            )
            await everyone_has_read.wait()
            if status not in {s.value for s in _RUNNABLE}:
                return False
            await session.execute(
                text(
                    "UPDATE jobs SET status = 'running', worker_id = :worker "
                    "WHERE job_id = :id"
                ),
                {"worker": worker_id, "id": job_id},
            )
            await session.commit()
            return True

    believed = await asyncio.wait_for(
        asyncio.gather(*(claim(f"worker-{i}") for i in range(WORKERS))), timeout=30
    )

    assert believed.count(True) == WORKERS
    stored = await _stored(job_id)
    assert stored.status == JobStatus.RUNNING
    assert stored.worker_id in {f"worker-{i}" for i in range(WORKERS)}
