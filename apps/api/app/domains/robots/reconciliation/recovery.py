"""Bounded, stateless recovery of the registration stage (ADR-008 §5.1-§5.3,
§8 step 12.4).

``reconcile_once`` observes and classifies; this module acts on that report, and
only on the states the ADR makes eligible::

    registration_pending                 no Job at all       -> submit REGISTER_ROBOT_RUN
    registration_failed_transient        budget left         -> submit again
    registration_stalled_candidate       budget left         -> abandon the Job
                                                                (FAILED / JobAbandoned),
                                                                then a forced replacement
    registered, permanent_conflict, integrity_incident,
    registration_failed_permanent (incl. budget spent),
    registration_active, every publication / capture state   -> never acted on

It keeps no state between passes: every decision is recomputed from the report,
which is recomputed from durable facts. Registration itself is always the
existing path -- ``RobotRunRegistrationService.submit`` creates a Job and
dispatches it; the worker handler is unchanged. This module never writes a
RobotRunRecord, an ArtifactRecord or an object (L-2).

Failure semantics
-----------------

* **Budget.** The logical registration is its execution key. Every FAILED Job of
  the key, abandoned ones included, is one attempt; replacement Jobs share it and
  a new Job row never resets it. Automatic action stops when it is spent. An
  operator's forced submission is outside the budget. The count is read when the
  pass observes; concurrent passes may overshoot it by at most the number of
  passes, never silently loop (each of their Jobs is itself counted next pass).
* **Replacement is single-winner.** Abandoning is one conditional UPDATE
  (``abandon_if_inactive``); only the pass that changed the row creates the
  replacement, so concurrent passes do not both replace one stalled Job. Plain
  submission (first Job, transient retry) can still race and create two Jobs;
  that is harmless by construction (W11): registration converges on one
  RobotRunRecord.
* **Dispatch failure.** The Job is committed before dispatch. If the broker
  refuses it, the Job is preserved as PENDING / QUEUED, the outcome is recorded
  as ``dispatch_failed`` and a later pass recovers it as a stalled Job. Nothing
  is rolled back and no success is reported.
* **Late completion.** A stalled Job that was abandoned may still finish; its
  worker overwrites the row, and the idempotent registration it ran is
  reconciled as ``registered`` whatever any Job says (W9).
* **One run never blocks another.** An action that raises is recorded as
  ``error`` and the pass continues. At most ``max_actions`` mutating actions run
  per pass; the rest wait for the next one.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sceneops_core.artifacts.contracts import ArtifactStore
from sceneops_core.common.ids import generate_job_event_id
from sceneops_core.common.schemas import ErrorInfo
from sceneops_core.common.time import utc_now
from sceneops_core.jobs.schemas import (
    JobEvent,
    JobEventLevel,
    JobEventType,
    JobManifest,
    JobStatus,
    JobType,
)
from sceneops_core.robots.capture_scan import CaptureScanReport
from sceneops_core.robots.registration_failures import (
    JOB_ABANDONED_ERROR_TYPE,
    REGISTRATION_ATTEMPT_BUDGET,
)
from sceneops_db.postgres.jobs import PostgresJobEventRepository, PostgresJobRepository

from app.domains.robots.registration import RegistrationDispatchError
from app.domains.robots.schemas import RegisterRobotRunResponse

from .classify import ClassificationPolicy, attempt_budget_spent
from .model import (
    AcquisitionState,
    ReconciliationReport,
    RecoveryAction,
    RecoveryActionKind,
    RecoveryOutcome,
    RecoveryPolicyFacts,
    RunReport,
)
from .service import RegistrationFactsScope, reconcile_once

logger = logging.getLogger(__name__)

# Mutating actions one pass may perform. After a long outage thousands of runs
# can be eligible at once; the bound keeps one pass from flooding the queue and
# the remainder is simply picked up by the next pass (stateless).
DEFAULT_MAX_ACTIONS_PER_PASS: Final = 100

_ACTIVE_STATUSES = frozenset({JobStatus.PENDING, JobStatus.QUEUED, JobStatus.RUNNING})


class RegistrationSubmitter(Protocol):
    """``RobotRunRegistrationService``: create the Job, then dispatch it. Raises
    ``RegistrationDispatchError`` (carrying the committed Job) when only the
    dispatch failed."""

    async def submit(
        self, manifest_uri: str, *, force: bool = False
    ) -> RegisterRobotRunResponse: ...


class StalledJobControl(Protocol):
    async def abandon(
        self, job_id: str, *, inactive_since: datetime, error: ErrorInfo
    ) -> JobManifest | None:
        """Fail one stalled REGISTER_ROBOT_RUN Job if, and only if, it is still
        in flight and inactive since ``inactive_since``. None: this call did not
        change it."""


@dataclass(frozen=True)
class RecoveryPolicy:
    """What the one-shot command applies. ``stall_threshold`` has no default
    here on purpose: the command owns it (ADR-008 §5.3 records its derivation)."""

    stall_threshold: timedelta
    attempt_budget: int = REGISTRATION_ATTEMPT_BUDGET
    max_actions: int = DEFAULT_MAX_ACTIONS_PER_PASS

    def __post_init__(self) -> None:
        if self.stall_threshold <= timedelta(0):
            raise ValueError("stall_threshold must be positive")
        if self.attempt_budget < 1:
            raise ValueError("attempt_budget must be at least 1")
        if self.max_actions < 1:
            raise ValueError("max_actions must be at least 1")

    def classification(self) -> ClassificationPolicy:
        return ClassificationPolicy(
            stall_candidate_after=self.stall_threshold,
            attempt_budget=self.attempt_budget,
        )

    def facts(self) -> RecoveryPolicyFacts:
        return RecoveryPolicyFacts(
            stall_threshold_seconds=self.stall_threshold.total_seconds(),
            attempt_budget=self.attempt_budget,
            max_actions=self.max_actions,
        )


class PostgresStalledJobControl:
    """Abandons through the Job repository in a short transaction of its own,
    and records one FAILED event so the Job's history says why."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def abandon(
        self, job_id: str, *, inactive_since: datetime, error: ErrorInfo
    ) -> JobManifest | None:
        async with self._session_factory() as session:
            abandoned = await PostgresJobRepository(session).abandon_if_inactive(
                job_id,
                type=JobType.REGISTER_ROBOT_RUN,
                inactive_since=inactive_since,
                error=error,
            )
            if abandoned is None:
                await session.rollback()
                return None
            await PostgresJobEventRepository(session).append(
                JobEvent(
                    event_id=generate_job_event_id(),
                    job_id=abandoned.job_id,
                    type=JobEventType.FAILED,
                    level=JobEventLevel.ERROR,
                    status=JobStatus.FAILED,
                    job_type=abandoned.type,
                    message="Job abandoned by reconciliation: no progress",
                    error=error,
                    created_at=utc_now(),
                )
            )
            await session.commit()
            return abandoned


def _log(action: RecoveryAction) -> None:
    # One structured line per resumer action (ADR-008 §7.3).
    logger.info(
        "acquisition_recovery %s",
        json.dumps(
            {
                "run_id": action.run_id,
                "stage": "register",
                "action": action.kind.value,
                "outcome": action.outcome.value,
                "reason": action.reason,
                "job_id": action.job_id,
                "abandoned_job_ids": list(action.abandoned_job_ids),
                "attempt": (action.attempts_used or 0) + 1,
                "error_type": action.error.split(":", 1)[0] if action.error else None,
            },
            sort_keys=True,
        ),
    )


def _error_text(error: BaseException) -> str:
    message = " ".join(str(error).split())
    return f"{type(error).__name__}: {message[:300]}"


def _manifest_uri(run: RunReport) -> str | None:
    publication = run.publication
    if publication is None or publication.manifest_object is None:
        return None
    return publication.manifest_object.uri


def plan_recovery(
    run: RunReport, policy: RecoveryPolicy
) -> tuple[RecoveryActionKind, str | None] | None:
    """The action the ADR makes eligible for one run, or None. A returned
    ``NONE`` kind is a deliberate, reported refusal with its reason. Pure."""
    registration = run.registration
    jobs = registration.jobs

    if run.state == AcquisitionState.REGISTRATION_PENDING:
        if not jobs:
            return RecoveryActionKind.SUBMIT_REGISTRATION, None
        # Pending with Jobs means the newest Job is neither failed nor in
        # flight. A success without a RobotRunRecord contradicts the durable
        # facts (L-9) and a cancellation is an operator's decision: neither is
        # retried automatically, and a plain submission would only dedup onto
        # the success anyway.
        latest = jobs[-1].status
        reason = (
            "succeeded_job_without_robot_run"
            if latest == JobStatus.SUCCEEDED
            else f"latest_job_{latest.value}"
        )
        return RecoveryActionKind.NONE, reason

    if run.state == AcquisitionState.REGISTRATION_FAILED_TRANSIENT:
        if attempt_budget_spent(registration, policy.attempt_budget):
            return None  # classification already reports this as permanent
        return RecoveryActionKind.RETRY_REGISTRATION, None

    if run.state == AcquisitionState.REGISTRATION_STALLED_CANDIDATE:
        if attempt_budget_spent(registration, policy.attempt_budget):
            return RecoveryActionKind.NONE, "attempt_budget_exhausted"
        return RecoveryActionKind.REPLACE_STALLED_JOB, None

    return None


async def _submit(
    submitter: RegistrationSubmitter,
    run: RunReport,
    kind: RecoveryActionKind,
    *,
    manifest_uri: str,
    force: bool,
    abandoned: tuple[str, ...] = (),
    policy: RecoveryPolicy,
) -> RecoveryAction:
    base = {
        "run_id": run.run_id,
        "kind": kind,
        "abandoned_job_ids": abandoned,
        "attempts_used": run.registration.failed_job_count,
        "attempt_budget": policy.attempt_budget,
    }
    try:
        response = await submitter.submit(manifest_uri, force=force)
    except RegistrationDispatchError as exc:
        return RecoveryAction(
            **base,
            outcome=RecoveryOutcome.DISPATCH_FAILED,
            reason="dispatch_failed",
            job_id=exc.job.job_id,
            error=_error_text(exc.__cause__ or exc),
        )
    return RecoveryAction(
        **base,
        outcome=(
            RecoveryOutcome.SUBMITTED
            if response.execution is not None
            else RecoveryOutcome.DEDUPLICATED
        ),
        job_id=response.job.job_id,
    )


async def _replace_stalled(
    run: RunReport,
    *,
    submitter: RegistrationSubmitter,
    jobs: StalledJobControl,
    policy: RecoveryPolicy,
    now: datetime,
    manifest_uri: str,
) -> RecoveryAction:
    inactive_since = now - policy.stall_threshold
    error = ErrorInfo(
        type=JOB_ABANDONED_ERROR_TYPE,
        message=(
            f"No progress for more than {policy.stall_threshold.total_seconds():g}s; "
            "abandoned by reconciliation"
        ),
        details={
            "stall_threshold_seconds": policy.stall_threshold.total_seconds(),
            "run_id": run.run_id,
        },
    )
    abandoned: list[str] = []
    for job in run.registration.jobs:
        if job.status not in _ACTIVE_STATUSES:
            continue
        won = await jobs.abandon(job.job_id, inactive_since=inactive_since, error=error)
        if won is not None:
            abandoned.append(won.job_id)

    used = run.registration.failed_job_count
    base = {
        "run_id": run.run_id,
        "attempts_used": used,
        "attempt_budget": policy.attempt_budget,
        "abandoned_job_ids": tuple(abandoned),
    }
    if not abandoned:
        # Someone else changed every stalled Job first (a concurrent pass, or
        # the worker made progress): they own the follow-up.
        return RecoveryAction(
            **base,
            kind=RecoveryActionKind.REPLACE_STALLED_JOB,
            outcome=RecoveryOutcome.LOST_RACE,
            reason="job_changed_since_observed",
        )
    if used + len(abandoned) >= policy.attempt_budget:
        # The abandoned Job was the last attempt the budget allowed.
        return RecoveryAction(
            **base,
            kind=RecoveryActionKind.ABANDON_STALLED_JOB,
            outcome=RecoveryOutcome.ABANDONED,
            reason="attempt_budget_exhausted",
        )
    return await _submit(
        submitter,
        run,
        RecoveryActionKind.REPLACE_STALLED_JOB,
        manifest_uri=manifest_uri,
        force=True,
        abandoned=tuple(abandoned),
        policy=policy,
    )


async def recover(
    report: ReconciliationReport,
    *,
    submitter: RegistrationSubmitter,
    jobs: StalledJobControl,
    policy: RecoveryPolicy,
    now: datetime,
) -> tuple[tuple[RecoveryAction, ...], int]:
    """Act on an observed report. Returns the actions taken (skips included)
    and how many eligible actions the per-pass bound left for the next pass."""
    actions: list[RecoveryAction] = []
    performed = 0
    deferred = 0

    for run in report.runs:
        plan = plan_recovery(run, policy)
        if plan is None:
            continue
        kind, reason = plan
        base = {
            "attempts_used": run.registration.failed_job_count,
            "attempt_budget": policy.attempt_budget,
        }
        if kind == RecoveryActionKind.NONE:
            action = RecoveryAction(
                run_id=run.run_id,
                kind=kind,
                outcome=RecoveryOutcome.SKIPPED,
                reason=reason,
                **base,
            )
            actions.append(action)
            _log(action)
            continue

        manifest_uri = _manifest_uri(run)
        if manifest_uri is None:
            continue  # unreachable for a registration state; never guess a URI
        if performed >= policy.max_actions:
            deferred += 1
            continue
        performed += 1
        try:
            if kind == RecoveryActionKind.REPLACE_STALLED_JOB:
                action = await _replace_stalled(
                    run,
                    submitter=submitter,
                    jobs=jobs,
                    policy=policy,
                    now=now,
                    manifest_uri=manifest_uri,
                )
            else:
                action = await _submit(
                    submitter,
                    run,
                    kind,
                    manifest_uri=manifest_uri,
                    force=False,
                    policy=policy,
                )
        except Exception as exc:  # noqa: BLE001 - one run must not block the others
            action = RecoveryAction(
                run_id=run.run_id,
                kind=kind,
                outcome=RecoveryOutcome.ERROR,
                reason="action_raised",
                error=_error_text(exc),
                **base,
            )
        actions.append(action)
        _log(action)
    return tuple(actions), deferred


async def reconcile_and_recover(
    *,
    artifact_store: ArtifactStore,
    root_uri: str,
    registration_facts: RegistrationFactsScope,
    submitter: RegistrationSubmitter,
    jobs: StalledJobControl,
    policy: RecoveryPolicy,
    capture_report: CaptureScanReport | None = None,
    now: datetime | None = None,
) -> ReconciliationReport:
    """``reconcile --once --apply``: observe, classify, then act on the
    eligible registration states. Classification needs only the ArtifactStore and
    PostgreSQL; the broker is touched only by submission (ADR-008 §3.2)."""
    now = now or datetime.now(UTC)
    report = await reconcile_once(
        artifact_store=artifact_store,
        root_uri=root_uri,
        registration_facts=registration_facts,
        capture_report=capture_report,
        policy=policy.classification(),
        now=now,
    )
    actions, deferred = await recover(
        report, submitter=submitter, jobs=jobs, policy=policy, now=now
    )
    return report.model_copy(
        update={
            "mode": "apply",
            "policy": policy.facts(),
            "actions": actions,
            "actions_deferred": deferred,
        }
    )


__all__ = [
    "DEFAULT_MAX_ACTIONS_PER_PASS",
    "PostgresStalledJobControl",
    "RecoveryPolicy",
    "RegistrationSubmitter",
    "StalledJobControl",
    "plan_recovery",
    "reconcile_and_recover",
    "recover",
]
