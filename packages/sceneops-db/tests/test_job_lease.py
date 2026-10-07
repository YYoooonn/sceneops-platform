"""The ownership lease of a claimed Job on real PostgreSQL: ``claim_for_run`` takes
a new lease generation, ``renew_lease`` extends the claim that holds the Job,
``reclaim_expired_lease`` ends a claim whose lease has passed, and every write of
a claim is fenced by its generation.

The passage of time is simulated by moving ``lease_expires_at`` into the past
(``_expire``): PostgreSQL's ``now()`` is the transaction's start time, so a test
cannot wait for a lease to pass inside one transaction, and sleeping would only
make the tests slower without testing anything else.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from sqlalchemy import text

from sceneops_core.common.schemas import ErrorInfo
from sceneops_core.common.time import utc_now
from sceneops_core.jobs.schemas import JobManifest, JobStatus, JobType
from sceneops_db.postgres.jobs import PostgresJobRepository
from sceneops_db.session import get_async_sessionmaker

_RUNNABLE = {JobStatus.PENDING, JobStatus.QUEUED}
_BUDGET = 3
_ERROR = ErrorInfo(type="JobLeaseExpired", message="claim budget spent")


def _job(job_id: str, **kw) -> JobManifest:
    return JobManifest(
        job_id=job_id, type=JobType.PROFILE_SCENE, status=JobStatus.QUEUED, **kw
    )


async def _claim(
    repository: PostgresJobRepository,
    job_id: str,
    worker_id: str = "celery:t1",
    lease_seconds: float = 60,
) -> JobManifest | None:
    return await repository.claim_for_run(
        job_id,
        worker_id=worker_id,
        runnable_statuses=_RUNNABLE,
        lease_seconds=lease_seconds,
    )


async def _expire(session, job_id: str) -> None:
    await session.execute(
        text(
            "UPDATE jobs SET lease_expires_at = now() - interval '1 second' "
            "WHERE job_id = :job_id"
        ),
        {"job_id": job_id},
    )


def _finished(job: JobManifest, status: JobStatus) -> JobManifest:
    return job.model_copy(update={"status": status, "finished_at": utc_now()})


# ── one actor at a time (the test's own rolled-back transaction) ─────────────


async def test_a_claim_takes_a_new_generation_and_a_lease_on_the_database_clock(
    db_session, unique_id
):
    repository = PostgresJobRepository(db_session)
    job_id = unique_id("job-lease")
    await repository.create(_job(job_id))

    claimed = await _claim(repository, job_id, lease_seconds=30)

    assert claimed is not None and claimed.status == JobStatus.RUNNING
    assert claimed.lease_generation == 1
    assert claimed.lease_expires_at - claimed.locked_at == timedelta(seconds=30)


async def test_the_holding_claim_renews_and_an_older_one_cannot(db_session, unique_id):
    repository = PostgresJobRepository(db_session)
    job_id = unique_id("job-lease")
    await repository.create(_job(job_id))
    claimed = await _claim(repository, job_id, lease_seconds=30)

    renewed = await repository.renew_lease(
        job_id, lease_generation=claimed.lease_generation, lease_seconds=120
    )
    stale = await repository.renew_lease(
        job_id, lease_generation=claimed.lease_generation - 1, lease_seconds=999
    )

    assert renewed is not None
    assert stale is None
    stored = await repository.get(job_id)
    assert stored.lease_expires_at == renewed
    assert stored.lease_expires_at - stored.heartbeat_at == timedelta(seconds=120)


async def test_a_held_lease_is_not_reclaimed(db_session, unique_id):
    repository = PostgresJobRepository(db_session)
    job_id = unique_id("job-lease")
    await repository.create(_job(job_id))
    await _claim(repository, job_id)

    reclaimed = await repository.reclaim_expired_lease(
        job_id, claim_budget=_BUDGET, error=_ERROR
    )

    assert reclaimed is None
    assert (await repository.get(job_id)).status == JobStatus.RUNNING
    assert job_id not in {
        j.job_id for j in await repository.list_expired_leases(limit=1000)
    }


async def test_an_expired_lease_requeues_the_same_job(db_session, unique_id):
    repository = PostgresJobRepository(db_session)
    job_id = unique_id("job-lease")
    await repository.create(_job(job_id))
    await _claim(repository, job_id)
    await _expire(db_session, job_id)
    assert job_id in {
        j.job_id for j in await repository.list_expired_leases(limit=1000)
    }

    reclaimed = await repository.reclaim_expired_lease(
        job_id, claim_budget=_BUDGET, error=_ERROR
    )

    assert reclaimed is not None
    assert reclaimed.status == JobStatus.QUEUED
    assert reclaimed.lease_generation == 1  # the next claim takes 2
    assert reclaimed.retry_count == 0  # a lost worker is not a failure retry
    assert reclaimed.error is None
    again = await _claim(repository, job_id, worker_id="celery:t2")
    assert again.lease_generation == 2


async def test_an_expired_lease_past_the_claim_budget_fails_the_job(
    db_session, unique_id
):
    repository = PostgresJobRepository(db_session)
    job_id = unique_id("job-lease")
    await repository.create(_job(job_id))
    for _ in range(_BUDGET):
        assert await _claim(repository, job_id) is not None
        await _expire(db_session, job_id)
        reclaimed = await repository.reclaim_expired_lease(
            job_id, claim_budget=_BUDGET, error=_ERROR
        )

    assert reclaimed.status == JobStatus.FAILED
    assert reclaimed.lease_generation == _BUDGET
    assert reclaimed.error.type == "JobLeaseExpired"
    assert reclaimed.finished_at is not None
    assert await _claim(repository, job_id) is None  # FAILED is not runnable


async def test_a_redelivered_message_with_the_same_worker_id_does_not_unfence_the_old_claim(
    db_session, unique_id
):
    """Regression: ownership fenced by worker_id let the original owner write over
    the claim of a redelivery of its own message (both are celery:<task id>)."""
    repository = PostgresJobRepository(db_session)
    job_id = unique_id("job-lease")
    await repository.create(_job(job_id))
    old = await _claim(repository, job_id, worker_id="celery:t1")
    await _expire(db_session, job_id)
    await repository.reclaim_expired_lease(job_id, claim_budget=_BUDGET, error=_ERROR)
    new = await _claim(repository, job_id, worker_id="celery:t1")  # redelivery
    assert new.worker_id == old.worker_id

    late = await repository.update_owned_run(
        _finished(old, JobStatus.SUCCEEDED), lease_generation=old.lease_generation
    )
    late_renewal = await repository.renew_lease(
        job_id, lease_generation=old.lease_generation, lease_seconds=60
    )
    owner = await repository.update_owned_run(
        _finished(new, JobStatus.SUCCEEDED), lease_generation=new.lease_generation
    )

    assert late is None
    assert late_renewal is None
    assert owner is not None and owner.status == JobStatus.SUCCEEDED
    assert owner.lease_generation == new.lease_generation


async def test_a_stale_dispatch_read_cannot_requeue_across_a_reclaim(
    db_session, unique_id
):
    """RUNNING -> QUEUED is a way back to an earlier status that does not touch
    retry_count; the generation keeps (status, retry_count, generation) unique."""
    repository = PostgresJobRepository(db_session)
    job_id = unique_id("job-lease")
    read_by_dispatcher = await repository.create(_job(job_id))
    await _claim(repository, job_id)
    await _expire(db_session, job_id)
    await repository.reclaim_expired_lease(job_id, claim_budget=_BUDGET, error=_ERROR)
    current = await repository.get(job_id)
    assert (current.status, current.retry_count) == (
        read_by_dispatcher.status,
        read_by_dispatcher.retry_count,
    )

    assert await repository.queue_if_unchanged(read_by_dispatcher) is None
    assert await repository.queue_if_unchanged(current) is not None


# ── concurrent actors (committed sessions of their own) ──────────────────────


async def _committed_running_job(job_id: str) -> JobManifest:
    async with get_async_sessionmaker()() as session:
        repository = PostgresJobRepository(session)
        await repository.create(_job(job_id))
        claimed = await _claim(repository, job_id)
        await _expire(session, job_id)
        await session.commit()
        return claimed


async def test_of_concurrent_recovery_passes_exactly_one_reclaims_the_job(unique_id):
    job_id = unique_id("job-lease")
    await _committed_running_job(job_id)
    ready = asyncio.Event()

    async def reclaim() -> JobManifest | None:
        async with get_async_sessionmaker()() as session:
            await session.execute(text("SELECT 1"))
            await ready.wait()
            reclaimed = await PostgresJobRepository(session).reclaim_expired_lease(
                job_id, claim_budget=_BUDGET, error=_ERROR
            )
            await session.commit()
            return reclaimed

    tasks = [asyncio.create_task(reclaim()) for _ in range(8)]
    await asyncio.sleep(0.2)
    ready.set()
    results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=30)

    assert sum(result is not None for result in results) == 1
    async with get_async_sessionmaker()() as session:
        stored = await PostgresJobRepository(session).get(job_id)
    assert stored.status == JobStatus.QUEUED


async def _hold_then_commit(
    statement, first_done: asyncio.Event, release: asyncio.Event
):
    async with get_async_sessionmaker()() as session:
        result = await statement(PostgresJobRepository(session))
        first_done.set()
        await release.wait()
        await session.commit()
        return result


async def test_a_renewal_that_reaches_the_row_first_keeps_the_job(unique_id):
    job_id = unique_id("job-lease")
    claimed = await _committed_running_job(job_id)
    renewed, release = asyncio.Event(), asyncio.Event()

    renewal = asyncio.create_task(
        _hold_then_commit(
            lambda r: r.renew_lease(
                job_id, lease_generation=claimed.lease_generation, lease_seconds=60
            ),
            renewed,
            release,
        )
    )
    await renewed.wait()  # the renewal holds the row lock, uncommitted

    async def reclaim():
        async with get_async_sessionmaker()() as session:
            result = await PostgresJobRepository(session).reclaim_expired_lease(
                job_id, claim_budget=_BUDGET, error=_ERROR
            )
            await session.commit()
            return result

    recovery = asyncio.create_task(reclaim())
    await asyncio.sleep(0.3)
    assert not recovery.done()  # waits for the renewal's row lock
    release.set()

    assert await renewal is not None
    # PostgreSQL re-evaluates the reclaim's WHERE on the renewed row: the lease
    # is held again.
    assert await asyncio.wait_for(recovery, timeout=10) is None
    async with get_async_sessionmaker()() as session:
        stored = await PostgresJobRepository(session).get(job_id)
    assert stored.status == JobStatus.RUNNING
    assert stored.lease_generation == claimed.lease_generation


async def test_a_renewal_after_the_reclaim_learns_the_claim_is_gone(unique_id):
    job_id = unique_id("job-lease")
    claimed = await _committed_running_job(job_id)
    reclaimed, release = asyncio.Event(), asyncio.Event()

    recovery = asyncio.create_task(
        _hold_then_commit(
            lambda r: r.reclaim_expired_lease(
                job_id, claim_budget=_BUDGET, error=_ERROR
            ),
            reclaimed,
            release,
        )
    )
    await reclaimed.wait()

    async def renew():
        async with get_async_sessionmaker()() as session:
            result = await PostgresJobRepository(session).renew_lease(
                job_id, lease_generation=claimed.lease_generation, lease_seconds=60
            )
            await session.commit()
            return result

    renewal = asyncio.create_task(renew())
    await asyncio.sleep(0.3)
    assert not renewal.done()
    release.set()

    assert await recovery is not None
    assert await asyncio.wait_for(renewal, timeout=10) is None
    async with get_async_sessionmaker()() as session:
        stored = await PostgresJobRepository(session).get(job_id)
    assert stored.status == JobStatus.QUEUED
