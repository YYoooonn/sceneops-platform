"""``PostgresJobRepository.abandon_if_inactive`` (ADR-008 §5.3): the conditional
UPDATE that makes abandoning a stalled Job single-winner. Real PostgreSQL.

The single-session tests live in the test's own rolled-back transaction. The
concurrency test needs two connections that each see the other's commit, so it
commits its own uniquely-identified row into the disposable database of
`make test-integration`.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from sceneops_core.common.schemas import ErrorInfo
from sceneops_core.jobs.schemas import JobManifest, JobStatus, JobType
from sceneops_db.postgres.jobs import PostgresJobRepository
from sceneops_db.session import get_async_sessionmaker

_NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
_CUTOFF = _NOW - timedelta(minutes=15)
_STALE = _NOW - timedelta(hours=1)
_ERROR = ErrorInfo(type="JobAbandoned", message="no progress")


def _job(job_id: str, status: JobStatus, **kw) -> JobManifest:
    created = kw.pop("created_at", _STALE)
    return JobManifest(
        job_id=job_id,
        type=kw.pop("type", JobType.REGISTER_ROBOT_RUN),
        status=status,
        execution_key=kw.pop("execution_key", f"key-{job_id}"),
        created_at=created,
        queued_at=kw.pop("queued_at", created),
        **kw,
    )


@pytest.mark.parametrize(
    "status", [JobStatus.PENDING, JobStatus.QUEUED, JobStatus.RUNNING]
)
async def test_an_inactive_in_flight_job_is_failed_with_the_given_error(
    db_session, unique_id, status
):
    repository = PostgresJobRepository(db_session)
    job_id = unique_id("job")
    await repository.create(_job(job_id, status, started_at=_STALE))

    abandoned = await repository.abandon_if_inactive(
        job_id,
        type=JobType.REGISTER_ROBOT_RUN,
        inactive_since=_CUTOFF,
        error=_ERROR,
    )

    assert abandoned is not None
    assert abandoned.status == JobStatus.FAILED
    assert abandoned.error is not None and abandoned.error.type == "JobAbandoned"
    assert abandoned.finished_at is not None
    stored = await repository.get(job_id)
    assert stored.status == JobStatus.FAILED
    assert stored.error.message == "no progress"


async def test_activity_after_the_cutoff_protects_the_job(db_session, unique_id):
    repository = PostgresJobRepository(db_session)
    heartbeat = unique_id("job-hb")
    claimed = unique_id("job-started")
    queued = unique_id("job-queued")
    await repository.create(
        _job(heartbeat, JobStatus.RUNNING, started_at=_STALE, heartbeat_at=_NOW)
    )
    await repository.create(_job(claimed, JobStatus.RUNNING, started_at=_NOW))
    await repository.create(_job(queued, JobStatus.QUEUED, queued_at=_NOW))

    for job_id in (heartbeat, claimed, queued):
        assert (
            await repository.abandon_if_inactive(
                job_id,
                type=JobType.REGISTER_ROBOT_RUN,
                inactive_since=_CUTOFF,
                error=_ERROR,
            )
            is None
        )
        assert (await repository.get(job_id)).status != JobStatus.FAILED


@pytest.mark.parametrize(
    "status", [JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED]
)
async def test_a_terminal_job_is_never_changed(db_session, unique_id, status):
    repository = PostgresJobRepository(db_session)
    job_id = unique_id("job")
    await repository.create(_job(job_id, status))

    assert (
        await repository.abandon_if_inactive(
            job_id,
            type=JobType.REGISTER_ROBOT_RUN,
            inactive_since=_CUTOFF,
            error=_ERROR,
        )
        is None
    )
    assert (await repository.get(job_id)).status == status


async def test_only_the_requested_job_type_can_be_abandoned(db_session, unique_id):
    repository = PostgresJobRepository(db_session)
    job_id = unique_id("job")
    await repository.create(
        _job(job_id, JobStatus.RUNNING, type=JobType.INGEST_ROBOT_STATES)
    )

    assert (
        await repository.abandon_if_inactive(
            job_id,
            type=JobType.REGISTER_ROBOT_RUN,
            inactive_since=_CUTOFF,
            error=_ERROR,
        )
        is None
    )
    assert (await repository.get(job_id)).status == JobStatus.RUNNING


async def test_an_unknown_job_is_not_an_error(db_session):
    assert (
        await PostgresJobRepository(db_session).abandon_if_inactive(
            "no-such-job",
            type=JobType.REGISTER_ROBOT_RUN,
            inactive_since=_CUTOFF,
            error=_ERROR,
        )
        is None
    )


async def test_concurrent_abandoners_have_exactly_one_winner(unique_id):
    """Ten connections race to abandon one stalled Job: PostgreSQL re-evaluates
    the predicate on the locked row, so exactly one of them changes it."""
    sessionmaker = get_async_sessionmaker()
    job_id = unique_id("job-race")
    async with sessionmaker() as session:
        await PostgresJobRepository(session).create(
            _job(job_id, JobStatus.RUNNING, started_at=_STALE)
        )
        await session.commit()

    async def attempt() -> bool:
        async with sessionmaker() as session:
            won = await PostgresJobRepository(session).abandon_if_inactive(
                job_id,
                type=JobType.REGISTER_ROBOT_RUN,
                inactive_since=_CUTOFF,
                error=_ERROR,
            )
            await session.commit()
            return won is not None

    results = await asyncio.gather(*(attempt() for _ in range(10)))
    assert results.count(True) == 1
    async with sessionmaker() as session:
        stored = await PostgresJobRepository(session).get(job_id)
    assert stored.status == JobStatus.FAILED
