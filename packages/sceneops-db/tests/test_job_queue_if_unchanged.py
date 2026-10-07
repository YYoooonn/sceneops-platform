"""``PostgresJobRepository.queue_if_unchanged``: the conditional QUEUED transition of a
dispatch. The Job is queued only from the status and retry_count it was read with,
and only the queueing columns are written. Real PostgreSQL, in the test's own
rolled-back transaction.
"""

from __future__ import annotations

import pytest

from sceneops_core.common.schemas import ErrorInfo
from sceneops_core.jobs.schemas import JobManifest, JobStatus, JobType
from sceneops_db.postgres.jobs import PostgresJobRepository


def _job(job_id: str, status: JobStatus, **fields) -> JobManifest:
    return JobManifest(
        job_id=job_id, type=JobType.REGISTER_ROBOT_RUN, status=status, **fields
    )


@pytest.mark.parametrize("status", [JobStatus.PENDING, JobStatus.QUEUED])
async def test_a_dispatchable_job_is_queued(db_session, unique_id, status):
    repository = PostgresJobRepository(db_session)
    read = await repository.create(_job(unique_id("job"), status))

    queued = await repository.queue_if_unchanged(read)

    assert queued is not None
    assert queued.status == JobStatus.QUEUED
    assert queued.retry_count == 0
    assert queued.queued_at is not None


async def test_queueing_a_failed_job_is_a_retry(db_session, unique_id):
    repository = PostgresJobRepository(db_session)
    read = await repository.create(
        _job(unique_id("job"), JobStatus.FAILED, retry_count=1, max_retries=3)
    )

    queued = await repository.queue_if_unchanged(read)

    assert queued is not None
    assert queued.status == JobStatus.QUEUED and queued.retry_count == 2


@pytest.mark.parametrize(
    "now",
    [
        {"status": JobStatus.RUNNING, "worker_id": "worker-1"},
        {"status": JobStatus.SUCCEEDED},
        {
            "status": JobStatus.FAILED,
            "error": ErrorInfo(type="JobAbandoned", message="no progress"),
        },
    ],
    ids=["claimed", "succeeded", "abandoned"],
)
async def test_a_job_that_moved_on_since_it_was_read_is_not_written(
    db_session, unique_id, now
):
    repository = PostgresJobRepository(db_session)
    read = await repository.create(_job(unique_id("job"), JobStatus.PENDING))
    current = await repository.update(read.model_copy(update=now))

    assert await repository.queue_if_unchanged(read) is None
    assert await repository.get(read.job_id) == current


async def test_a_failure_after_a_spent_retry_is_not_retried_again(
    db_session, unique_id
):
    """FAILED -> QUEUED -> RUNNING -> FAILED: the status recurs, its retry_count
    does not, so a retry decided on the first failure does not apply to the
    second."""
    repository = PostgresJobRepository(db_session)
    first_failure = await repository.create(
        _job(unique_id("job"), JobStatus.FAILED, max_retries=3)
    )
    retried = await repository.queue_if_unchanged(first_failure)
    await repository.update(retried.model_copy(update={"status": JobStatus.FAILED}))

    assert await repository.queue_if_unchanged(first_failure) is None
    stored = await repository.get(first_failure.job_id)
    assert stored.status == JobStatus.FAILED and stored.retry_count == 1


@pytest.mark.parametrize(
    "status", [JobStatus.RUNNING, JobStatus.SUCCEEDED, JobStatus.CANCELLED]
)
async def test_a_job_that_is_not_dispatchable_is_never_queued(
    db_session, unique_id, status
):
    """Even a caller whose read is current cannot queue a running or finished Job."""
    repository = PostgresJobRepository(db_session)
    read = await repository.create(_job(unique_id("job"), status))

    assert await repository.queue_if_unchanged(read) is None
    assert (await repository.get(read.job_id)).status == status


async def test_an_unknown_job_is_not_queued(db_session, unique_id):
    assert (
        await PostgresJobRepository(db_session).queue_if_unchanged(
            _job(unique_id("job"), JobStatus.PENDING)
        )
        is None
    )
