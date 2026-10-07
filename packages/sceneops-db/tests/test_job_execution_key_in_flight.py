"""At most one in-flight Job per execution key (``uq_jobs_execution_key_in_flight``)
and how ``PostgresJobRepository`` reports the writes it refuses. Real PostgreSQL.

The single-session tests live in the test's own rolled-back transaction. The
overlapping-transaction tests commit their own uniquely-keyed Jobs into the
disposable database of `make test-integration`.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from sceneops_core.jobs.schemas import JobManifest, JobStatus, JobType
from sceneops_db.models.jobs import JOB_IN_FLIGHT_STATUSES
from sceneops_db.postgres.jobs import PostgresJobRepository
from sceneops_db.repositories.jobs import JobExecutionKeyInFlightError
from sceneops_db.session import get_async_sessionmaker

_IN_FLIGHT = [JobStatus.PENDING, JobStatus.QUEUED, JobStatus.RUNNING]
_FINISHED = [JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED]


def _job(job_id: str, key: str | None, status: JobStatus) -> JobManifest:
    return JobManifest(
        job_id=job_id,
        type=JobType.REGISTER_ROBOT_RUN,
        status=status,
        execution_key=key,
    )


async def test_the_index_covers_exactly_the_in_flight_statuses(db_session):
    assert set(JOB_IN_FLIGHT_STATUSES) == {s.value for s in _IN_FLIGHT}
    definition = await db_session.scalar(
        text(
            "SELECT indexdef FROM pg_indexes "
            "WHERE tablename = 'jobs' AND indexname = 'uq_jobs_execution_key_in_flight'"
        )
    )
    assert definition is not None, "the migration must create the index"
    assert definition.startswith("CREATE UNIQUE INDEX")
    for status in JOB_IN_FLIGHT_STATUSES:
        assert f"'{status}'" in definition
    for status in _FINISHED:
        assert f"'{status.value}'" not in definition


@pytest.mark.parametrize("held", _IN_FLIGHT)
@pytest.mark.parametrize("new", _IN_FLIGHT)
async def test_a_second_in_flight_job_with_the_key_is_refused(
    db_session, unique_id, held, new
):
    repository = PostgresJobRepository(db_session)
    key = unique_id("key")
    await repository.create(_job(unique_id("job"), key, held))

    with pytest.raises(JobExecutionKeyInFlightError) as refused:
        await repository.create(_job(unique_id("job"), key, new))

    assert refused.value.execution_key == key
    # DO NOTHING aborts nothing: the transaction goes on.
    other = await repository.create(_job(unique_id("job"), unique_id("key"), new))
    assert other.status == new


@pytest.mark.parametrize("plan_cache_mode", ["force_custom_plan", "force_generic_plan"])
async def test_the_conflict_target_is_inferred_under_either_plan(
    db_session, unique_id, plan_cache_mode
):
    """asyncpg prepares every statement, and PostgreSQL may plan a prepared
    statement generically, without its parameter values. The arbiter index must
    still be inferred then, so its predicate cannot depend on a parameter."""
    await db_session.execute(text(f"SET LOCAL plan_cache_mode = {plan_cache_mode}"))
    repository = PostgresJobRepository(db_session)
    key = unique_id("key")
    await repository.create(_job(unique_id("job"), key, JobStatus.PENDING))

    with pytest.raises(JobExecutionKeyInFlightError):
        await repository.create(_job(unique_id("job"), key, JobStatus.PENDING))


async def test_repeated_inserts_on_one_connection_keep_working(db_session, unique_id):
    """PostgreSQL switches a prepared statement to a generic plan after five
    executions on one connection."""
    repository = PostgresJobRepository(db_session)
    for _ in range(12):
        key = unique_id("key")
        await repository.create(_job(unique_id("job"), key, JobStatus.PENDING))
        with pytest.raises(JobExecutionKeyInFlightError):
            await repository.create(_job(unique_id("job"), key, JobStatus.QUEUED))


@pytest.mark.parametrize("finished", _FINISHED)
async def test_finished_jobs_do_not_hold_the_key(db_session, unique_id, finished):
    repository = PostgresJobRepository(db_session)
    key = unique_id("key")
    await repository.create(_job(unique_id("job"), key, finished))
    await repository.create(_job(unique_id("job"), key, finished))

    created = await repository.create(_job(unique_id("job"), key, JobStatus.PENDING))

    assert created.execution_key == key


async def test_jobs_without_a_key_never_conflict(db_session, unique_id):
    """Pipeline task Jobs carry no execution key."""
    repository = PostgresJobRepository(db_session)
    for _ in range(3):
        await repository.create(_job(unique_id("job"), None, JobStatus.QUEUED))


async def test_another_conflict_is_not_reported_as_an_in_flight_key(
    db_session, unique_id
):
    repository = PostgresJobRepository(db_session)
    job_id = unique_id("job")
    await repository.create(_job(job_id, unique_id("key"), JobStatus.PENDING))

    with pytest.raises(IntegrityError):
        await repository.create(_job(job_id, unique_id("key"), JobStatus.PENDING))


@pytest.mark.parametrize("first_commits", [True, False])
async def test_an_insert_waits_for_an_uncommitted_holder_of_the_key(
    unique_id, first_commits
):
    """The second writer cannot know yet whether the first one's Job will exist, so
    it waits for that transaction: it is refused if the first commits and inserts
    its own Job if the first rolls back."""
    sessionmaker = get_async_sessionmaker()
    key = unique_id("key")
    first_inserted = asyncio.Event()
    release_first = asyncio.Event()

    async def first() -> None:
        async with sessionmaker() as session:
            await PostgresJobRepository(session).create(
                _job(unique_id("job-a"), key, JobStatus.PENDING)
            )
            first_inserted.set()
            await release_first.wait()
            if first_commits:
                await session.commit()
            else:
                await session.rollback()

    async def second() -> JobManifest | JobExecutionKeyInFlightError:
        await first_inserted.wait()
        async with sessionmaker() as session:
            try:
                created = await PostgresJobRepository(session).create(
                    _job(unique_id("job-b"), key, JobStatus.PENDING)
                )
            except JobExecutionKeyInFlightError as exc:
                return exc
            await session.commit()
            return created

    first_task = asyncio.create_task(first())
    second_task = asyncio.create_task(second())
    await first_inserted.wait()
    await asyncio.sleep(0.5)
    assert not second_task.done(), "the second insert waits for the first's outcome"

    release_first.set()
    _, outcome = await asyncio.wait_for(
        asyncio.gather(first_task, second_task), timeout=10
    )

    if first_commits:
        assert isinstance(outcome, JobExecutionKeyInFlightError)
    else:
        assert isinstance(outcome, JobManifest) and outcome.execution_key == key


async def test_putting_a_failed_job_back_in_flight_beside_another_is_refused(
    db_session, unique_id
):
    repository = PostgresJobRepository(db_session)
    key = unique_id("key")
    failed = await repository.create(_job(unique_id("job"), key, JobStatus.FAILED))
    await repository.create(_job(unique_id("job"), key, JobStatus.RUNNING))

    with pytest.raises(JobExecutionKeyInFlightError):
        await repository.update(failed.model_copy(update={"status": JobStatus.QUEUED}))
