"""``PostgresJobRepository.update_owned_run``: a RUNNING Job is written only under the
claim (lease generation) that holds it, and a Job leaves RUNNING exactly once per
claim. Real PostgreSQL.

Every test runs in the test's own rolled-back transaction.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sceneops_core.common.schemas import ErrorInfo
from sceneops_core.jobs.schemas import JobManifest, JobStatus, JobType
from sceneops_db.postgres.jobs import PostgresJobRepository

_NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
_STALE = _NOW - timedelta(hours=1)
_WORKER = "celery:owner"


def _job(job_id: str, status: JobStatus, **kw) -> JobManifest:
    return JobManifest(
        job_id=job_id,
        type=JobType.PROFILE_SCENE,
        status=status,
        created_at=_STALE,
        updated_at=_STALE,
        **kw,
    )


async def _running(repository: PostgresJobRepository, job_id: str) -> JobManifest:
    await repository.create(_job(job_id, JobStatus.QUEUED, queued_at=_STALE))
    claimed = await repository.claim_for_run(
        job_id,
        worker_id=_WORKER,
        runnable_statuses={JobStatus.QUEUED},
        lease_seconds=60,
    )
    assert claimed is not None and claimed.status == JobStatus.RUNNING
    return claimed


def _finished(job: JobManifest, status: JobStatus, **kw) -> JobManifest:
    return job.model_copy(
        update={"status": status, "finished_at": _NOW, "updated_at": _NOW, **kw}
    )


async def test_the_owner_writes_the_terminal_state(db_session, unique_id):
    repository = PostgresJobRepository(db_session)
    running = await _running(repository, unique_id("job"))

    stored = await repository.update_owned_run(
        _finished(running, JobStatus.SUCCEEDED, result={"value": "done"}),
        lease_generation=running.lease_generation,
    )

    assert stored is not None
    assert stored.status == JobStatus.SUCCEEDED
    assert stored.result == {"value": "done"}
    assert stored.finished_at is not None
    assert (await repository.get(running.job_id)).status == JobStatus.SUCCEEDED


async def test_a_terminal_job_is_never_rewritten(db_session, unique_id):
    repository = PostgresJobRepository(db_session)
    running = await _running(repository, unique_id("job"))
    await repository.update_owned_run(
        _finished(running, JobStatus.SUCCEEDED, result={"value": "done"}),
        lease_generation=running.lease_generation,
    )

    # RUNNING -> SUCCEEDED -> FAILED by the same worker is refused.
    rewritten = await repository.update_owned_run(
        _finished(
            running,
            JobStatus.FAILED,
            error=ErrorInfo(type="RuntimeError", message="late failure"),
        ),
        lease_generation=running.lease_generation,
    )

    assert rewritten is None
    stored = await repository.get(running.job_id)
    assert stored.status == JobStatus.SUCCEEDED
    assert stored.result == {"value": "done"}
    assert stored.error is None


async def test_another_claim_cannot_write_the_job(db_session, unique_id):
    repository = PostgresJobRepository(db_session)
    running = await _running(repository, unique_id("job"))

    written = await repository.update_owned_run(
        _finished(running, JobStatus.SUCCEEDED),
        lease_generation=running.lease_generation - 1,
    )

    assert written is None
    assert (await repository.get(running.job_id)).status == JobStatus.RUNNING


async def test_a_job_that_is_not_running_is_not_written(db_session, unique_id):
    repository = PostgresJobRepository(db_session)
    job_id = unique_id("job")
    queued = await repository.create(_job(job_id, JobStatus.QUEUED, queued_at=_STALE))

    written = await repository.update_owned_run(
        _finished(
            queued.model_copy(update={"worker_id": _WORKER}), JobStatus.SUCCEEDED
        ),
        lease_generation=queued.lease_generation,
    )

    assert written is None
    assert (await repository.get(job_id)).status == JobStatus.QUEUED


async def test_a_late_worker_cannot_overwrite_an_abandoned_job(db_session, unique_id):
    repository = PostgresJobRepository(db_session)
    running = await _running(repository, unique_id("job"))
    # Make the claim look stale so recovery's conditional abandon takes the Job.
    stale = running.model_copy(
        update={"started_at": _STALE, "heartbeat_at": _STALE, "locked_at": _STALE}
    )
    await repository.update_owned_run(stale, lease_generation=running.lease_generation)
    abandoned = await repository.abandon_if_inactive(
        running.job_id,
        type=JobType.PROFILE_SCENE,
        inactive_since=_NOW,
        error=ErrorInfo(type="JobAbandoned", message="no progress"),
    )
    assert abandoned is not None and abandoned.status == JobStatus.FAILED

    late = await repository.update_owned_run(
        _finished(running, JobStatus.SUCCEEDED, result={"value": "late"}),
        lease_generation=running.lease_generation,
    )

    assert late is None
    stored = await repository.get(running.job_id)
    assert stored.status == JobStatus.FAILED
    assert stored.error.type == "JobAbandoned"
    assert stored.result is None
