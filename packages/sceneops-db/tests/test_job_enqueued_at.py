"""``enqueued_at`` on real PostgreSQL: the start of a Job's current wait in QUEUED.

It is set by every transition into QUEUED from another status (a dispatch from
PENDING, a retry from FAILED, lease recovery from RUNNING) and kept by the writes
that leave the Job QUEUED (a redispatch, execution recovery's resend) and by the
claim that ends the wait, while ``queued_at`` -- the last dispatch -- moves on
every send. Measured from ``queued_at``, a Job waiting behind a backlog would
never appear to wait longer than the resend threshold.

PostgreSQL's ``now()`` is the transaction's start, so the passage of time is
simulated by moving timestamps an hour into the past (``_age``).
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import text

from sceneops_core.common.schemas import ErrorInfo
from sceneops_core.jobs.schemas import JobManifest, JobStatus, JobType
from sceneops_db.postgres.jobs import PostgresJobRepository

_HOUR = timedelta(hours=1)


def _pending(job_id: str) -> JobManifest:
    return JobManifest(
        job_id=job_id, type=JobType.PROFILE_SCENE, status=JobStatus.PENDING
    )


async def _now(session):
    return (await session.execute(text("SELECT now()"))).scalar_one()


async def _age(session, job_id: str, *columns: str) -> None:
    sets = ", ".join(f"{c} = now() - interval '1 hour'" for c in columns)
    await session.execute(
        text(f"UPDATE jobs SET {sets} WHERE job_id = :j"), {"j": job_id}
    )


async def _stored(repository, job_id: str) -> JobManifest:
    job = await repository.get(job_id)
    assert job is not None
    return job


async def test_a_dispatch_from_pending_starts_the_wait(db_session, unique_id):
    repository = PostgresJobRepository(db_session)
    job = await repository.create(_pending(unique_id("job")))
    assert job.enqueued_at is None

    queued = await repository.queue_if_unchanged(job)

    now = await _now(db_session)
    assert queued.enqueued_at == now
    assert queued.queued_at == now


async def test_a_redispatch_and_a_resend_keep_the_wait_and_move_the_dispatch(
    db_session, unique_id
):
    repository = PostgresJobRepository(db_session)
    job_id = unique_id("job")
    job = await repository.create(_pending(job_id))
    await repository.queue_if_unchanged(job)
    await _age(db_session, job_id, "enqueued_at", "queued_at")
    now = await _now(db_session)

    redispatched = await repository.queue_if_unchanged(
        await _stored(repository, job_id)
    )
    assert redispatched.enqueued_at == now - _HOUR
    assert redispatched.queued_at == now

    await _age(db_session, job_id, "queued_at")
    resent = await repository.claim_redispatch(await _stored(repository, job_id))
    assert resent.enqueued_at == now - _HOUR
    assert resent.queued_at == now


async def test_the_claim_keeps_the_wait_it_ends(db_session, unique_id):
    repository = PostgresJobRepository(db_session)
    job_id = unique_id("job")
    await repository.queue_if_unchanged(await repository.create(_pending(job_id)))
    await _age(db_session, job_id, "enqueued_at")
    now = await _now(db_session)

    claimed = await repository.claim_for_run(
        job_id,
        worker_id="celery:t1",
        runnable_statuses={JobStatus.QUEUED},
        lease_seconds=60,
    )

    assert claimed.enqueued_at == now - _HOUR
    assert claimed.locked_at - claimed.enqueued_at == _HOUR


async def test_a_retry_after_failure_starts_a_new_wait(db_session, unique_id):
    repository = PostgresJobRepository(db_session)
    job_id = unique_id("job")
    await repository.create(
        JobManifest(
            job_id=job_id,
            type=JobType.PROFILE_SCENE,
            status=JobStatus.FAILED,
            max_retries=1,
        )
    )
    await db_session.execute(
        text(
            "UPDATE jobs SET enqueued_at = now() - interval '1 hour' WHERE job_id = :j"
        ),
        {"j": job_id},
    )

    retried = await repository.queue_if_unchanged(await _stored(repository, job_id))

    assert retried.retry_count == 1
    assert retried.enqueued_at == await _now(db_session)


async def test_lease_recovery_requeues_into_a_new_wait(db_session, unique_id):
    repository = PostgresJobRepository(db_session)
    job_id = unique_id("job")
    await repository.queue_if_unchanged(await repository.create(_pending(job_id)))
    await repository.claim_for_run(
        job_id,
        worker_id="celery:t1",
        runnable_statuses={JobStatus.QUEUED},
        lease_seconds=60,
    )
    await _age(db_session, job_id, "enqueued_at", "lease_expires_at")

    requeued = await repository.reclaim_expired_lease(
        job_id,
        claim_budget=3,
        error=ErrorInfo(type="JobLeaseExpired", message="budget spent"),
    )

    assert requeued.status == JobStatus.QUEUED
    assert requeued.enqueued_at == await _now(db_session)
