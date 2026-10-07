"""The durable-state queries and claims of execution recovery, on real PostgreSQL:
which Jobs and PipelineRuns wait on a message for too long, and the single-winner
claim of the right to send it again.

Time passing is simulated by moving timestamps into the past (``_age``): the
queries compare with PostgreSQL's ``now()``. Overdue lists are database-wide, so
assertions are about the test's own rows.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import text

from sceneops_core.common.time import utc_now
from sceneops_core.jobs.schemas import JobManifest, JobStatus, JobType
from sceneops_core.pipelines.schemas import (
    PipelineRunManifest,
    PipelineRunStatus,
    PipelineTaskRunManifest,
    PipelineTaskRunStatus,
    PipelineType,
)
from sceneops_db.postgres.jobs import PostgresJobRepository
from sceneops_db.postgres.pipelines import (
    PostgresPipelineRunRepository,
    PostgresPipelineTaskRunRepository,
)
from sceneops_db.session import get_async_sessionmaker

_RESEND_AFTER = 300


async def _age(session, table: str, key: str, value: str, *columns: str) -> None:
    sets = ", ".join(f"{c} = now() - interval '1 hour'" for c in columns)
    await session.execute(
        text(f"UPDATE {table} SET {sets} WHERE {key} = :v"), {"v": value}
    )


def _ids(rows, attr: str) -> set[str]:
    return {getattr(row, attr) for row in rows}


# ── Jobs waiting on run_job ──────────────────────────────────────────────────


async def _job(session, job_id: str, status: JobStatus) -> JobManifest:
    return await PostgresJobRepository(session).create(
        JobManifest(
            job_id=job_id,
            type=JobType.PROFILE_SCENE,
            status=status,
            queued_at=utc_now() if status == JobStatus.QUEUED else None,
        )
    )


async def test_only_queued_jobs_dispatched_too_long_ago_are_overdue(
    db_session, unique_id
):
    repository = PostgresJobRepository(db_session)
    old, recent = unique_id("job-old"), unique_id("job-recent")
    other = {
        s: unique_id(f"job-{s.value}")
        for s in (JobStatus.PENDING, JobStatus.RUNNING, JobStatus.SUCCEEDED)
    }
    for job_id in (old, recent):
        await _job(db_session, job_id, JobStatus.QUEUED)
    for status, job_id in other.items():
        await _job(db_session, job_id, status)
        await _age(db_session, "jobs", "job_id", job_id, "queued_at", "updated_at")
    await _age(db_session, "jobs", "job_id", old, "queued_at")

    overdue = _ids(
        await repository.list_dispatch_overdue(
            older_than_seconds=_RESEND_AFTER, limit=10_000
        ),
        "job_id",
    )

    assert old in overdue
    assert recent not in overdue
    assert overdue.isdisjoint(other.values())


async def test_a_redispatch_claim_restarts_the_wait_and_refuses_a_stale_read(
    db_session, unique_id
):
    repository = PostgresJobRepository(db_session)
    job_id = unique_id("job")
    await _job(db_session, job_id, JobStatus.QUEUED)
    await _age(db_session, "jobs", "job_id", job_id, "queued_at")
    (observed,) = [
        j
        for j in await repository.list_dispatch_overdue(
            older_than_seconds=_RESEND_AFTER, limit=10_000
        )
        if j.job_id == job_id
    ]

    claimed = await repository.claim_redispatch(observed)
    again = await repository.claim_redispatch(observed)  # the same stale read

    assert claimed is not None and claimed.queued_at > observed.queued_at
    assert again is None
    assert job_id not in _ids(
        await repository.list_dispatch_overdue(
            older_than_seconds=_RESEND_AFTER, limit=10_000
        ),
        "job_id",
    )


async def test_a_claimed_job_is_not_redispatched(db_session, unique_id):
    repository = PostgresJobRepository(db_session)
    job_id = unique_id("job")
    observed = await _job(db_session, job_id, JobStatus.QUEUED)
    await repository.claim_for_run(
        job_id,
        worker_id="celery:t1",
        runnable_statuses={JobStatus.QUEUED},
        lease_seconds=60,
    )

    assert await repository.claim_redispatch(observed) is None
    assert (await repository.get(job_id)).status == JobStatus.RUNNING


async def test_of_concurrent_redispatch_claims_exactly_one_wins(unique_id):
    job_id = unique_id("job")
    async with get_async_sessionmaker()() as session:
        await _job(session, job_id, JobStatus.QUEUED)
        await _age(session, "jobs", "job_id", job_id, "queued_at")
        await session.commit()
        observed = await PostgresJobRepository(session).get(job_id)
    ready = asyncio.Event()

    async def claim():
        async with get_async_sessionmaker()() as session:
            await session.execute(text("SELECT 1"))
            await ready.wait()
            claimed = await PostgresJobRepository(session).claim_redispatch(observed)
            await session.commit()
            return claimed

    tasks = [asyncio.create_task(claim()) for _ in range(8)]
    await asyncio.sleep(0.2)
    ready.set()
    results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=30)

    assert sum(r is not None for r in results) == 1


# ── PipelineRuns waiting on advance ─────────────────────────────────────────


async def _run(session, run_id: str, status: PipelineRunStatus) -> None:
    now = utc_now()
    await PostgresPipelineRunRepository(session).create(
        PipelineRunManifest(
            pipeline_run_id=run_id,
            type=PipelineType.EPISODE_LEARNING_DATA_BUILDING,
            status=status,
            created_at=now,
            updated_at=now,
        )
    )


async def _task(session, run_id: str, job_id: str | None, status) -> None:
    now = utc_now()
    await PostgresPipelineTaskRunRepository(session).create(
        PipelineTaskRunManifest(
            pipeline_task_run_id=f"ptr-{run_id}",
            pipeline_run_id=run_id,
            pipeline_task_id="align_episode",
            pipeline_task_name="Align episodes",
            task_order=0,
            status=status,
            job_type=JobType.ALIGN_EPISODE,
            job_id=job_id,
            created_at=now,
            updated_at=now,
        )
    )


async def _waiting_run(session, unique_id, *, job_status, job_age: bool, run_age: bool):
    run_id, job_id = unique_id("pipe"), unique_id("job")
    await _run(session, run_id, PipelineRunStatus.RUNNING)
    await PostgresJobRepository(session).create(
        JobManifest(
            job_id=job_id,
            type=JobType.ALIGN_EPISODE,
            status=job_status,
            pipeline_run_id=run_id,
            finished_at=None if job_status == JobStatus.QUEUED else utc_now(),
        )
    )
    await _task(session, run_id, job_id, PipelineTaskRunStatus.RUNNING)
    if job_age:
        await _age(session, "jobs", "job_id", job_id, "finished_at", "updated_at")
    if run_age:
        await _age(session, "pipeline_runs", "pipeline_run_id", run_id, "updated_at")
    return run_id


async def _overdue_runs(session) -> set[str]:
    return _ids(
        await PostgresPipelineRunRepository(session).list_advance_overdue(
            older_than_seconds=_RESEND_AFTER, limit=10_000
        ),
        "pipeline_run_id",
    )


async def test_runs_waiting_on_an_advance_are_overdue_and_others_are_not(
    db_session, unique_id
):
    s = db_session
    queued_old = unique_id("pipe-q-old")
    await _run(s, queued_old, PipelineRunStatus.QUEUED)
    await _age(s, "pipeline_runs", "pipeline_run_id", queued_old, "updated_at")
    queued_recent = unique_id("pipe-q-new")
    await _run(s, queued_recent, PipelineRunStatus.QUEUED)
    succeeded_job = await _waiting_run(
        s, unique_id, job_status=JobStatus.SUCCEEDED, job_age=True, run_age=True
    )
    failed_job = await _waiting_run(
        s, unique_id, job_status=JobStatus.FAILED, job_age=True, run_age=True
    )
    job_in_flight = await _waiting_run(
        s, unique_id, job_status=JobStatus.QUEUED, job_age=True, run_age=True
    )
    job_just_finished = await _waiting_run(
        s, unique_id, job_status=JobStatus.SUCCEEDED, job_age=False, run_age=True
    )
    run_just_stepped = await _waiting_run(
        s, unique_id, job_status=JobStatus.SUCCEEDED, job_age=True, run_age=False
    )
    job_missing = unique_id("pipe-missing")
    await _run(s, job_missing, PipelineRunStatus.RUNNING)
    await _task(s, job_missing, unique_id("job-gone"), PipelineTaskRunStatus.RUNNING)
    await _age(s, "pipeline_runs", "pipeline_run_id", job_missing, "updated_at")
    finished = unique_id("pipe-done")
    await _run(s, finished, PipelineRunStatus.SUCCEEDED)
    await _age(s, "pipeline_runs", "pipeline_run_id", finished, "updated_at")

    overdue = await _overdue_runs(s)

    assert {queued_old, succeeded_job, failed_job, job_missing} <= overdue
    assert overdue.isdisjoint(
        {queued_recent, job_in_flight, job_just_finished, run_just_stepped, finished}
    )


async def test_an_advance_claim_restarts_the_wait_and_refuses_a_stale_read(
    db_session, unique_id
):
    repository = PostgresPipelineRunRepository(db_session)
    run_id = await _waiting_run(
        db_session,
        unique_id,
        job_status=JobStatus.SUCCEEDED,
        job_age=True,
        run_age=True,
    )
    (observed,) = [
        r
        for r in await repository.list_advance_overdue(
            older_than_seconds=_RESEND_AFTER, limit=10_000
        )
        if r.pipeline_run_id == run_id
    ]

    claimed = await repository.claim_advance(observed)
    again = await repository.claim_advance(observed)

    assert claimed is not None and claimed.updated_at > observed.updated_at
    assert again is None
    assert run_id not in await _overdue_runs(db_session)
