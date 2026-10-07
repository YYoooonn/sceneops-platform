"""Job lease recovery: give a Job whose worker stopped renewing its lease back to
execution.

A worker holds the Job it claimed by a lease it renews while it runs (``lease``).
When the worker dies, hangs or loses PostgreSQL, the lease passes and nothing
else would ever move the RUNNING Job: a redelivered Celery message cannot claim
it. One recovery pass acts on every RUNNING Job whose lease has passed::

    claimed fewer than JOB_CLAIM_BUDGET times   -> QUEUED, then a new job message
    claim budget spent                          -> FAILED (JobLeaseExpired); a
                                                   pipeline-owned Job's run is
                                                   advanced so it observes the failure

The same Job is requeued, not replaced: its PipelineTaskRun keeps waiting on it,
and the next claim takes a new ``lease_generation``, which fences every write of
the previous one. The claim budget bounds a Job that takes its worker down every
time it runs. ``retry_count`` is untouched: it counts retries after a failure.

Failure semantics
-----------------

* **Stateless.** Every decision is recomputed from the jobs table; a pass may run
  at any frequency, concurrently with other passes and after a crash.
* **Single winner.** The reclaim is one conditional UPDATE per branch, re-evaluated
  by PostgreSQL against the row as it is now: of concurrent passes exactly one
  changes the Job, and a renewal that reached the row first keeps it with its
  worker. A pass that changed nothing reports ``lost_race``.
* **Durable state first.** The Job is committed QUEUED before its message is
  sent. If the send fails, the Job stays QUEUED (``dispatch_failed``) and no pass
  re-sends it: a QUEUED Job with a lost message is not recovered here.
* **Duplicate messages are harmless.** The original message of the dead claim may
  still be redelivered by the broker; whichever message claims the QUEUED Job
  first runs it, the other finds it claimed. The claim, not the message, decides.
* **Side effects of the old claim.** A reclaimed worker may still be running.
  Its Job writes are fenced; whatever its handler already wrote is not, and is
  safe because artifacts are write-once and content-pinned.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sceneops_core.common.ids import generate_job_event_id
from sceneops_core.common.schemas import ErrorInfo
from sceneops_core.common.time import utc_now
from sceneops_core.jobs.schemas import (
    JobEvent,
    JobEventLevel,
    JobEventType,
    JobManifest,
    JobStatus,
)
from sceneops_db.postgres import PostgresExecutionRecordRepository
from sceneops_db.postgres.jobs import PostgresJobEventRepository, PostgresJobRepository
from sceneops_worker.execution.dispatcher import ExecutionDispatcher

logger = logging.getLogger(__name__)

# Claims a Job may receive before an expired lease fails it instead of
# requeueing it.
JOB_CLAIM_BUDGET: Final = 3
JOB_LEASE_EXPIRED_ERROR_TYPE: Final = "JobLeaseExpired"
# Mutating actions one pass performs; the rest wait for the next pass.
DEFAULT_MAX_ACTIONS_PER_PASS: Final = 100


class LeaseRecoveryOutcome(StrEnum):
    REQUEUED = "requeued"
    DISPATCH_FAILED = "dispatch_failed"
    FAILED = "failed"
    LOST_RACE = "lost_race"
    ERROR = "error"


@dataclass(frozen=True)
class LeaseRecoveryAction:
    job_id: str
    outcome: LeaseRecoveryOutcome
    expired_generation: int
    expired_worker_id: str | None
    lease_expires_at: datetime | None
    pipeline_run_id: str | None = None
    error: str | None = None


async def recover_expired_leases(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    dispatcher: ExecutionDispatcher,
    claim_budget: int = JOB_CLAIM_BUDGET,
    max_actions: int = DEFAULT_MAX_ACTIONS_PER_PASS,
) -> list[LeaseRecoveryAction]:
    """One recovery pass over the RUNNING Jobs whose lease has passed."""
    async with session_factory() as session:
        expired = await PostgresJobRepository(session).list_expired_leases(
            limit=max_actions
        )

    actions: list[LeaseRecoveryAction] = []
    for observed in expired:
        try:
            action = await _recover(
                observed,
                session_factory=session_factory,
                dispatcher=dispatcher,
                claim_budget=claim_budget,
            )
        except Exception as exc:  # noqa: BLE001 - one Job must not block the others
            logger.exception("lease recovery of job %s failed", observed.job_id)
            action = _action(observed, LeaseRecoveryOutcome.ERROR, error=repr(exc))
        logger.info(
            "lease recovery: job %s claim %s of %s -> %s",
            action.job_id,
            action.expired_generation,
            action.expired_worker_id,
            action.outcome.value,
        )
        actions.append(action)
    return actions


def _action(
    job: JobManifest, outcome: LeaseRecoveryOutcome, *, error: str | None = None
) -> LeaseRecoveryAction:
    return LeaseRecoveryAction(
        job_id=job.job_id,
        outcome=outcome,
        expired_generation=job.lease_generation,
        expired_worker_id=job.worker_id,
        lease_expires_at=job.lease_expires_at,
        pipeline_run_id=job.pipeline_run_id,
        error=error,
    )


async def _recover(
    observed: JobManifest,
    *,
    session_factory: async_sessionmaker[AsyncSession],
    dispatcher: ExecutionDispatcher,
    claim_budget: int,
) -> LeaseRecoveryAction:
    error = ErrorInfo(
        type=JOB_LEASE_EXPIRED_ERROR_TYPE,
        message=(
            f"The lease of the Job's last claim expired and it has been claimed "
            f"{claim_budget} times without finishing; not requeued again"
        ),
        details={"claim_budget": claim_budget},
    )
    async with session_factory() as session:
        reclaimed = await PostgresJobRepository(session).reclaim_expired_lease(
            observed.job_id, claim_budget=claim_budget, error=error
        )
        if reclaimed is None:
            await session.rollback()
            return _action(observed, LeaseRecoveryOutcome.LOST_RACE)

        requeued = reclaimed.status == JobStatus.QUEUED
        await PostgresJobEventRepository(session).append(
            JobEvent(
                event_id=generate_job_event_id(),
                job_id=reclaimed.job_id,
                type=JobEventType.QUEUED if requeued else JobEventType.FAILED,
                level=JobEventLevel.WARNING if requeued else JobEventLevel.ERROR,
                status=reclaimed.status,
                job_type=reclaimed.type,
                pipeline_run_id=reclaimed.pipeline_run_id,
                pipeline_task_run_id=reclaimed.pipeline_task_run_id,
                pipeline_task_id=reclaimed.pipeline_task_id,
                worker_id=reclaimed.worker_id,
                attempt=reclaimed.lease_generation,
                message=(
                    "Lease expired; Job requeued by lease recovery"
                    if requeued
                    else "Lease expired; claim budget spent, Job failed"
                ),
                error=None if requeued else error,
                data={
                    "expired_generation": reclaimed.lease_generation,
                    "lease_expires_at": (
                        reclaimed.lease_expires_at.isoformat()
                        if reclaimed.lease_expires_at
                        else None
                    ),
                },
                created_at=utc_now(),
            )
        )
        await session.commit()
        recovered = _action(
            reclaimed,
            LeaseRecoveryOutcome.REQUEUED if requeued else LeaseRecoveryOutcome.FAILED,
        )

        if not requeued:
            # No worker will report this Job: the run must observe the failure.
            if reclaimed.pipeline_run_id is not None:
                dispatcher.advance_pipeline(reclaimed.pipeline_run_id)
            return recovered

        try:
            execution = dispatcher.dispatch_job(reclaimed.job_id)
        except Exception as exc:  # noqa: BLE001 - the Job stays QUEUED
            return _action(
                reclaimed, LeaseRecoveryOutcome.DISPATCH_FAILED, error=repr(exc)
            )
        await PostgresExecutionRecordRepository(session).create(execution)
        await session.commit()
        return recovered


__all__ = [
    "DEFAULT_MAX_ACTIONS_PER_PASS",
    "JOB_CLAIM_BUDGET",
    "JOB_LEASE_EXPIRED_ERROR_TYPE",
    "LeaseRecoveryAction",
    "LeaseRecoveryOutcome",
    "recover_expired_leases",
]
