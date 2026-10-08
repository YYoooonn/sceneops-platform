"""Execution recovery: re-send the wake-ups that durable state is waiting for.

Every dispatch commits PostgreSQL state first and sends its Celery message
second, so a message never names an uncommitted row; the price is a window in
which the commit succeeds and the send does not (broker down, process killed,
an accepted message lost by the broker). Nothing in the message is information
of its own: ``run_job(job_id)`` and ``advance(pipeline_run_id)`` carry an
identifier and tell a worker to act on state PostgreSQL already holds. A lost
message therefore loses only the wake-up, and which wake-up a row waits for
follows from the row:

    Job QUEUED, last dispatched (queued_at) longer ago than resend_after
        -> run_job(job_id)
    PipelineRun QUEUED, last dispatched (updated_at) longer ago than resend_after
        -> advance(pipeline_run_id)
    PipelineRun RUNNING whose RUNNING task's Job finished (or vanished) longer
    ago than resend_after, not stepped or re-sent since
        -> advance(pipeline_run_id)

Those are all the states in which progress needs a message: every dispatch path
(API, orchestrator, lease recovery) leaves its Job QUEUED, and after every
committed orchestration step a run is terminal or waits on exactly one Job.
A RUNNING Job waits on its worker, not on a message; lease recovery turns a lost
worker into a QUEUED Job. One pass therefore runs lease recovery, then the job
sweep, then the run sweep.

Failure semantics
-----------------

* **At least once, never "exactly".** The sweep cannot see the broker: a QUEUED
  Job whose message is still waiting behind a backlog looks like one whose
  message was lost. It is sent again once per ``resend_after``; the consumer
  makes that harmless (one claim per Job, one row-locked step per run).
* **Single sender per resend.** Before sending, a pass moves ``queued_at`` /
  ``updated_at`` to now with a conditional UPDATE on the value it read, and
  commits. Of concurrent passes exactly one wins; the timestamp is also when
  the next resend becomes due. No lock is held while the message is sent.
* **Crash anywhere is a later resend.** Crash after the timestamp commit and
  before the send: due again after ``resend_after``. Send failure: the same.
  Crash after the send: at worst one more message later. A dispatch request is
  complete when the state stops waiting (the Job is claimed, the run steps), not
  when a message was sent, so there is no "delivered" marker to get wrong.
* **Stateless.** Every decision is recomputed from PostgreSQL; a pass may run at
  any frequency, concurrently and after any crash.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sceneops_core.common.ids import generate_job_event_id
from sceneops_core.common.time import utc_now
from sceneops_core.jobs.schemas import (
    JobEvent,
    JobEventLevel,
    JobEventType,
    JobManifest,
    JobStatus,
)
from sceneops_core.pipelines.schemas import PipelineRunManifest
from sceneops_db.postgres import PostgresExecutionRecordRepository
from sceneops_db.postgres.jobs import PostgresJobEventRepository, PostgresJobRepository
from sceneops_db.postgres.pipelines import PostgresPipelineRunRepository
from sceneops_execution.executions.dispatcher import ExecutionDispatcher
from sceneops_execution.jobs.lease_recovery import (
    DEFAULT_MAX_ACTIONS_PER_PASS,
    LeaseRecoveryAction,
    recover_expired_leases,
)

logger = logging.getLogger(__name__)

# How long durable state may wait on a message before it is sent again. Longer
# than a healthy queue wait, so a backlog causes few duplicates; it is also the
# recovery latency of a lost message (plus the pass interval).
DEFAULT_RESEND_AFTER_SECONDS: Final = 300.0


class ResendOutcome(StrEnum):
    RESENT = "resent"
    SEND_FAILED = "send_failed"
    LOST_RACE = "lost_race"
    ERROR = "error"


@dataclass(frozen=True)
class ResendAction:
    message: str  # "run_job" or "advance"
    resource_id: str
    outcome: ResendOutcome
    waiting_since: str | None
    error: str | None = None


@dataclass(frozen=True)
class ExecutionRecoveryReport:
    leases: list[LeaseRecoveryAction]
    dispatches: list[ResendAction]
    advances: list[ResendAction]


async def recover_execution(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    dispatcher: ExecutionDispatcher,
    resend_after_seconds: float = DEFAULT_RESEND_AFTER_SECONDS,
    max_actions: int = DEFAULT_MAX_ACTIONS_PER_PASS,
) -> ExecutionRecoveryReport:
    """One execution recovery pass: expired leases, then QUEUED Jobs and
    PipelineRuns waiting on a message for longer than ``resend_after_seconds``."""
    leases = await recover_expired_leases(
        session_factory=session_factory,
        dispatcher=dispatcher,
        max_actions=max_actions,
    )
    dispatches = await resend_overdue_dispatches(
        session_factory=session_factory,
        dispatcher=dispatcher,
        resend_after_seconds=resend_after_seconds,
        max_actions=max_actions,
    )
    advances = await resend_overdue_advances(
        session_factory=session_factory,
        dispatcher=dispatcher,
        resend_after_seconds=resend_after_seconds,
        max_actions=max_actions,
    )
    return ExecutionRecoveryReport(
        leases=leases, dispatches=dispatches, advances=advances
    )


async def resend_overdue_dispatches(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    dispatcher: ExecutionDispatcher,
    resend_after_seconds: float = DEFAULT_RESEND_AFTER_SECONDS,
    max_actions: int = DEFAULT_MAX_ACTIONS_PER_PASS,
) -> list[ResendAction]:
    """Send ``run_job`` again for every QUEUED Job dispatched longer ago than
    ``resend_after_seconds``."""
    async with session_factory() as session:
        overdue = await PostgresJobRepository(session).list_dispatch_overdue(
            older_than_seconds=resend_after_seconds, limit=max_actions
        )
    return [
        await _guarded(
            "run_job",
            job.job_id,
            _waiting_since(job.queued_at),
            _resend_job(job, session_factory=session_factory, dispatcher=dispatcher),
        )
        for job in overdue
    ]


async def resend_overdue_advances(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    dispatcher: ExecutionDispatcher,
    resend_after_seconds: float = DEFAULT_RESEND_AFTER_SECONDS,
    max_actions: int = DEFAULT_MAX_ACTIONS_PER_PASS,
) -> list[ResendAction]:
    """Send ``advance`` again for every PipelineRun waiting on one for longer
    than ``resend_after_seconds``."""
    async with session_factory() as session:
        overdue = await PostgresPipelineRunRepository(session).list_advance_overdue(
            older_than_seconds=resend_after_seconds, limit=max_actions
        )
    return [
        await _guarded(
            "advance",
            run.pipeline_run_id,
            _waiting_since(run.updated_at),
            _resend_advance(
                run, session_factory=session_factory, dispatcher=dispatcher
            ),
        )
        for run in overdue
    ]


def _waiting_since(stamp) -> str | None:
    return stamp.isoformat() if stamp is not None else None


async def _guarded(message, resource_id, waiting_since, attempt) -> ResendAction:
    try:
        outcome, error = await attempt
    except Exception as exc:  # noqa: BLE001 - one resource must not block the others
        logger.exception("execution recovery: %s %s failed", message, resource_id)
        outcome, error = ResendOutcome.ERROR, repr(exc)
    logger.info("execution recovery: %s %s -> %s", message, resource_id, outcome.value)
    return ResendAction(
        message=message,
        resource_id=resource_id,
        outcome=outcome,
        waiting_since=waiting_since,
        error=error,
    )


async def _resend_job(
    observed: JobManifest,
    *,
    session_factory: async_sessionmaker[AsyncSession],
    dispatcher: ExecutionDispatcher,
) -> tuple[ResendOutcome, str | None]:
    async with session_factory() as session:
        claimed = await PostgresJobRepository(session).claim_redispatch(observed)
        if claimed is None:
            await session.rollback()
            return ResendOutcome.LOST_RACE, None
        await PostgresJobEventRepository(session).append(
            JobEvent(
                event_id=generate_job_event_id(),
                job_id=claimed.job_id,
                type=JobEventType.QUEUED,
                level=JobEventLevel.WARNING,
                status=JobStatus.QUEUED,
                job_type=claimed.type,
                pipeline_run_id=claimed.pipeline_run_id,
                pipeline_task_run_id=claimed.pipeline_task_run_id,
                pipeline_task_id=claimed.pipeline_task_id,
                message="Job message re-sent by execution recovery",
                data={"previous_queued_at": _waiting_since(observed.queued_at)},
                created_at=utc_now(),
            )
        )
        await session.commit()

        try:
            execution = dispatcher.dispatch_job(claimed.job_id)
        except Exception as exc:  # noqa: BLE001 - due again after resend_after
            return ResendOutcome.SEND_FAILED, repr(exc)
        await PostgresExecutionRecordRepository(session).create(execution)
        await session.commit()
    return ResendOutcome.RESENT, None


async def _resend_advance(
    observed: PipelineRunManifest,
    *,
    session_factory: async_sessionmaker[AsyncSession],
    dispatcher: ExecutionDispatcher,
) -> tuple[ResendOutcome, str | None]:
    async with session_factory() as session:
        claimed = await PostgresPipelineRunRepository(session).claim_advance(observed)
        if claimed is None:
            await session.rollback()
            return ResendOutcome.LOST_RACE, None
        await session.commit()

    try:
        dispatcher.advance_pipeline(claimed.pipeline_run_id)
    except Exception as exc:  # noqa: BLE001 - due again after resend_after
        return ResendOutcome.SEND_FAILED, repr(exc)
    return ResendOutcome.RESENT, None


__all__ = [
    "DEFAULT_RESEND_AFTER_SECONDS",
    "ExecutionRecoveryReport",
    "ResendAction",
    "ResendOutcome",
    "recover_execution",
    "resend_overdue_advances",
    "resend_overdue_dispatches",
]
