"""Pure derivation of ``AcquisitionStatus`` and the operational report (ADR-008
§7.1, §7.2).

Input is facts already observed -- the reconciliation report (one ``RunReport``
per run), the artifact lifecycle report -- and the observation time. Nothing
here reads a store, a database or a clock, and nothing acts (L-10). The view
adds no lifecycle semantics: ``classification`` is the reconciler's state,
``stage`` follows from which durable facts exist (L-1), and ``health`` and
``operator_required`` are the fixed table below.

::

    classification                      health        operator_required
    capture_unfinished                  pending       no  (age is reported)
    finalized_no_receipt                pending       yes (explicit-input publish only)
    publish_pending                     pending       no  (publish-pending)
    publication_incomplete
      recording only, resumable         pending       no
      recording only, no capture source pending       yes
      malformed / contradicted /
        manifest without recording      inconsistent  yes
    registration_pending                pending       no  (reconcile --apply submits)
      latest Job cancelled              pending       yes (an operator's decision)
      a success without a RobotRun      inconsistent  yes (L-9)
    registration_active                 pending       no
    registration_stalled_candidate      stalled       no, unless the budget is spent
    registration_failed_transient       pending       no  (retried within the budget)
    registration_failed_permanent       failed        yes
    registered                          ok            no
    permanent_conflict                  inconsistent  yes
    integrity_incident                  inconsistent  yes

A lifecycle entry that is an integrity incident raises any other health to
``inconsistent``: the artifact classification sees contradictions (a registered
run whose objects the records do not reference) that the acquisition state does
not.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from datetime import datetime

from sceneops_core.jobs.schemas import JobStatus
from sceneops_acquisition.lifecycle.vocabulary import LifecycleClass
from sceneops_recording.published_scan import PublicationClass
from sceneops_acquisition.registration_failures import RegistrationFailureClass

from sceneops_acquisition.lifecycle.model import (
    ArtifactLifecycleReport,
    LifecycleEntry,
)
from sceneops_acquisition.reconciliation.model import (
    AcquisitionState,
    JobFacts,
    ReconciliationReport,
    RunReport,
)

from .model import (
    AcquisitionHealth,
    AcquisitionOperationalReport,
    AcquisitionStage,
    AcquisitionStatus,
    ArtifactFinding,
    ArtifactSummary,
    AttentionItem,
    CaptureStatus,
    Durations,
    FailureFacts,
    FailureStage,
    IncidentAggregate,
    JobSummary,
    OldestWork,
    PublicationStatus,
    RecordingAggregate,
    RegistrationAggregate,
    RegistrationStatus,
    Timestamps,
)

S = AcquisitionState
H = AcquisitionHealth

_ACTIVE = frozenset({JobStatus.PENDING, JobStatus.QUEUED, JobStatus.RUNNING})

# States whose work is not finished, in lifecycle order; the operational report
# names the oldest run of each.
NON_TERMINAL_STATES: tuple[AcquisitionState, ...] = (
    S.CAPTURE_UNFINISHED,
    S.FINALIZED_NO_RECEIPT,
    S.PUBLISH_PENDING,
    S.PUBLICATION_INCOMPLETE,
    S.REGISTRATION_PENDING,
    S.REGISTRATION_ACTIVE,
    S.REGISTRATION_STALLED_CANDIDATE,
    S.REGISTRATION_FAILED_TRANSIENT,
)


def derive_stage(run: RunReport) -> AcquisitionStage:
    """The furthest stage whose durable fact exists (ADR-008 L-1)."""
    if run.registration.robot_run is not None:
        return AcquisitionStage.REGISTERED
    assessment = run.publication_assessment
    if (
        assessment is not None
        and assessment.classification == PublicationClass.PUBLISHED
    ):
        return AcquisitionStage.PUBLISHED
    capture = run.capture
    if run.publication is not None or (capture is not None and capture.final_present):
        return AcquisitionStage.FINALIZED
    return AcquisitionStage.CAPTURE_UNFINISHED


def derive_health(run: RunReport) -> tuple[AcquisitionHealth, bool]:
    """``(health, operator_required)`` from the state and its reasons."""
    state, reasons = run.state, set(run.reasons)
    if state == S.REGISTERED:
        return H.OK, False
    if state in (S.PERMANENT_CONFLICT, S.INTEGRITY_INCIDENT):
        return H.INCONSISTENT, True
    if state == S.REGISTRATION_FAILED_PERMANENT:
        return H.FAILED, True
    if state == S.REGISTRATION_STALLED_CANDIDATE:
        return H.STALLED, "attempt_budget_exhausted" in reasons
    if state == S.FINALIZED_NO_RECEIPT:
        return H.PENDING, True
    if state == S.PUBLICATION_INCOMPLETE:
        if PublicationClass.RECORDING_WITHOUT_MANIFEST.value in reasons:
            return H.PENDING, "resumable_from_capture" not in reasons
        return H.INCONSISTENT, True
    if state == S.REGISTRATION_PENDING:
        if "succeeded_job_without_robot_run" in reasons:
            return H.INCONSISTENT, True
        return H.PENDING, "latest_job_cancelled" in reasons
    return H.PENDING, False


def _latest_activity(job: JobFacts) -> Iterable[datetime | None]:
    return (
        job.created_at,
        job.queued_at,
        job.started_at,
        job.heartbeat_at,
        job.finished_at,
    )


def _last_activity(run: RunReport) -> datetime | None:
    stamps: list[datetime | None] = []
    if run.capture is not None:
        stamps.append(run.capture.modified_at)
    if run.publication is not None:
        for item in (
            run.publication.recording,
            run.publication.manifest_object,
            *run.publication.unexpected_objects,
        ):
            if item is not None:
                stamps.append(item.last_modified)
    for job in run.registration.jobs:
        stamps.extend(_latest_activity(job))
    if run.registration.robot_run is not None:
        stamps.append(run.registration.robot_run.registered_at)
    known = [stamp for stamp in stamps if stamp is not None]
    return max(known) if known else None


def _seconds(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return (end - start).total_seconds()


def _failure(run: RunReport) -> FailureFacts | None:
    """The failure that is still unresolved. A registered run's failed Jobs
    (an abandoned stalled Job, a transient error before the retry that worked)
    are history, not a failure: the RobotRunRecord ended the logical
    registration."""
    if run.registration.robot_run is not None:
        return None
    failed = [job for job in run.registration.jobs if job.status == JobStatus.FAILED]
    if not failed:
        return None
    latest = failed[-1]
    registration = run.registration
    return FailureFacts(
        stage=FailureStage.REGISTER,
        failure_class=latest.failure_class or RegistrationFailureClass.TRANSIENT,
        error_type=latest.error_type,
        job_id=latest.job_id,
        attempts=registration.failed_job_count,
        attempt_budget=registration.attempt_budget,
        attempts_remaining=registration.attempts_remaining,
    )


def _robot_id(run: RunReport) -> str | None:
    if run.registration.robot_run is not None:
        return run.registration.robot_run.robot_id
    if run.publication is not None and run.publication.manifest is not None:
        return run.publication.manifest.robot_id
    if run.capture is not None and run.capture.receipt is not None:
        return run.capture.receipt.robot_id
    return None


def _artifact_summary(entries: Sequence[LifecycleEntry]) -> ArtifactSummary:
    def of(kind: LifecycleClass) -> list[LifecycleEntry]:
        return [entry for entry in entries if entry.lifecycle_class == kind]

    def size(items: list[LifecycleEntry]) -> int:
        return sum(entry.size_bytes or 0 for entry in items)

    referenced = of(LifecycleClass.REFERENCED)
    pending = of(LifecycleClass.PENDING)
    candidates = of(LifecycleClass.ORPHAN_CANDIDATE)
    return ArtifactSummary(
        referenced_objects=len(referenced),
        pending_objects=len(pending),
        orphan_candidate_objects=len(candidates),
        integrity_incident_entries=len(of(LifecycleClass.INTEGRITY_INCIDENT)),
        referenced_bytes=size(referenced),
        pending_bytes=size(pending),
        orphan_candidate_bytes=size(candidates),
    )


def derive_status(
    run: RunReport,
    *,
    observed_at: datetime,
    entries: Sequence[LifecycleEntry] = (),
) -> AcquisitionStatus:
    """The ``AcquisitionStatus`` of one run. ``entries`` are the artifact
    lifecycle entries of that run."""
    health, operator_required = derive_health(run)
    if health != H.INCONSISTENT and any(
        entry.lifecycle_class == LifecycleClass.INTEGRITY_INCIDENT for entry in entries
    ):
        health, operator_required = H.INCONSISTENT, True

    capture, publication = run.capture, run.publication
    registration = run.registration
    record = registration.robot_run
    receipt = capture.receipt if capture is not None else None
    manifest = publication.manifest if publication is not None else None
    valid_marker = (
        run.publication_assessment is not None
        and run.publication_assessment.classification == PublicationClass.PUBLISHED
    )

    finalized_at = receipt.finalized_at if receipt is not None else None
    published_at = (
        publication.manifest_object.last_modified
        if valid_marker and publication.manifest_object is not None
        else None
    )
    registered_at = record.registered_at if record is not None else None
    last_activity_at = _last_activity(run)

    if manifest is not None:
        recording_bytes: int | None = manifest.recording_size_bytes
    elif publication is not None and publication.recording is not None:
        recording_bytes = publication.recording.size_bytes
    elif receipt is not None:
        recording_bytes = receipt.recording_size_bytes
    else:
        recording_bytes = None

    if manifest is not None:
        message_count: int | None = manifest.message_count
    elif receipt is not None:
        message_count = receipt.message_count
    else:
        message_count = None

    jobs = registration.jobs
    latest = jobs[-1] if jobs else None
    return AcquisitionStatus(
        run_id=run.run_id,
        robot_id=_robot_id(run),
        stage=derive_stage(run),
        health=health,
        classification=run.state,
        reasons=run.reasons,
        operator_required=operator_required,
        failure=_failure(run),
        capture=(
            CaptureStatus(
                classification=capture.classification.value,
                robot_id=receipt.robot_id if receipt is not None else None,
                finalization_reason=(
                    receipt.finalization_reason if receipt is not None else None
                ),
                finalized_at=finalized_at,
                recording_bytes=(
                    receipt.recording_size_bytes if receipt is not None else None
                ),
                message_count=receipt.message_count if receipt is not None else None,
                modified_at=capture.modified_at,
            )
            if capture is not None
            else None
        ),
        publication=(
            PublicationStatus(
                classification=run.publication_assessment.classification,
                manifest_uri=(
                    publication.manifest_object.uri
                    if publication.manifest_object is not None
                    else None
                ),
                manifest_checksum=publication.manifest_checksum,
                published_at=published_at,
                recording_bytes=recording_bytes,
                message_count=manifest.message_count if manifest is not None else None,
                channel_count=manifest.channel_count if manifest is not None else None,
                source_clock=manifest.source_clock if manifest is not None else None,
            )
            if publication is not None and run.publication_assessment is not None
            else None
        ),
        registration=RegistrationStatus(
            registered_at=registered_at,
            execution_key=registration.execution_key,
            job_count=len(jobs),
            active_job_ids=tuple(job.job_id for job in jobs if job.status in _ACTIVE),
            failed_job_count=registration.failed_job_count,
            abandoned_job_count=registration.abandoned_job_count,
            attempt_budget=registration.attempt_budget,
            attempts_remaining=registration.attempts_remaining,
            latest_job=(
                JobSummary(
                    job_id=latest.job_id,
                    status=latest.status,
                    created_at=latest.created_at,
                    error_type=latest.error_type,
                )
                if latest is not None
                else None
            ),
        ),
        timestamps=Timestamps(
            finalized_at=finalized_at,
            published_at=published_at,
            registered_at=registered_at,
            last_activity_at=last_activity_at,
        ),
        durations=Durations(
            finalize_to_publish_seconds=_seconds(finalized_at, published_at),
            publish_to_register_seconds=_seconds(published_at, registered_at),
            finalize_to_register_seconds=_seconds(finalized_at, registered_at),
        ),
        recording_bytes=recording_bytes,
        message_count=message_count,
        channel_count=manifest.channel_count if manifest is not None else None,
        finalization_reason=(
            receipt.finalization_reason if receipt is not None else None
        ),
        age_seconds=(
            max((observed_at - last_activity_at).total_seconds(), 0.0)
            if last_activity_at is not None
            else None
        ),
        artifacts=_artifact_summary(entries),
        findings=tuple(
            ArtifactFinding(
                lifecycle_class=entry.lifecycle_class,
                role=entry.role,
                uri=entry.uri,
                reasons=entry.reasons,
                orphan_reason=entry.orphan_reason,
                risk=entry.risk,
                size_bytes=entry.size_bytes,
            )
            for entry in entries
            if entry.lifecycle_class != LifecycleClass.REFERENCED
        ),
    )


def _sorted_counts(counter: Counter[str]) -> dict[str, int]:
    return dict(sorted(counter.items()))


def build_operational_report(
    *,
    reconciliation: ReconciliationReport,
    lifecycle: ArtifactLifecycleReport,
    observed_at: datetime,
    include_runs: bool = True,
) -> AcquisitionOperationalReport:
    """The aggregates of ADR-008 §7.2 over every run. ``include_runs=False``
    drops the per-run statuses from the output (the aggregates and the
    attention list still cover every run)."""
    by_run: dict[str, list[LifecycleEntry]] = {}
    for entry in lifecycle.entries:
        if entry.run_id is not None:
            by_run.setdefault(entry.run_id, []).append(entry)

    statuses = [
        derive_status(run, observed_at=observed_at, entries=by_run.get(run.run_id, ()))
        for run in reconciliation.runs
    ]

    unregistered = [
        status for status in statuses if status.stage != AcquisitionStage.REGISTERED
    ]
    attempts = Counter(
        str(status.registration.failed_job_count)
        for status in unregistered
        if status.registration.execution_key is not None
    )
    attempts_sorted = dict(sorted(attempts.items(), key=lambda item: int(item[0])))

    def count(state: AcquisitionState) -> int:
        return sum(1 for status in statuses if status.classification == state)

    registration = RegistrationAggregate(
        pending=count(S.REGISTRATION_PENDING),
        active=count(S.REGISTRATION_ACTIVE),
        stalled_candidates=count(S.REGISTRATION_STALLED_CANDIDATE),
        failed_transient=count(S.REGISTRATION_FAILED_TRANSIENT),
        failed_permanent=count(S.REGISTRATION_FAILED_PERMANENT),
        budget_exhausted=sum(
            1 for status in unregistered if status.registration.attempts_remaining == 0
        ),
        failed_jobs=sum(
            status.registration.failed_job_count for status in unregistered
        ),
        abandoned_jobs=sum(
            status.registration.abandoned_job_count for status in unregistered
        ),
        attempts_used=attempts_sorted,
    )

    oldest: dict[str, OldestWork] = {}
    for state in NON_TERMINAL_STATES:
        aged = [
            status
            for status in statuses
            if status.classification == state
            and status.age_seconds is not None
            and status.timestamps.last_activity_at is not None
        ]
        if aged:
            # Ties break on run_id so the report is a function of the facts.
            winner = max(aged, key=lambda status: (status.age_seconds, status.run_id))
            assert winner.age_seconds is not None
            assert winner.timestamps.last_activity_at is not None
            oldest[state.value] = OldestWork(
                run_id=winner.run_id,
                age_seconds=winner.age_seconds,
                last_activity_at=winner.timestamps.last_activity_at,
            )

    incident_runs = [
        status
        for status in statuses
        if status.classification in (S.INTEGRITY_INCIDENT, S.PERMANENT_CONFLICT)
    ]
    reasons: Counter[str] = Counter()
    for status in incident_runs:
        reasons.update(status.reasons)

    known_bytes = [s.recording_bytes for s in statuses if s.recording_bytes is not None]
    known_messages = [s.message_count for s in statuses if s.message_count is not None]

    attention = tuple(
        AttentionItem(
            run_id=status.run_id,
            classification=status.classification,
            health=status.health,
            reasons=status.reasons,
            operator_required=status.operator_required,
            age_seconds=status.age_seconds,
        )
        for status in statuses
        if status.health in (H.STALLED, H.FAILED, H.INCONSISTENT)
        or status.operator_required
    )

    return AcquisitionOperationalReport(
        root_uri=reconciliation.root_uri,
        observed_at=observed_at,
        policy=lifecycle.policy,
        runs_total=len(statuses),
        by_stage=_sorted_counts(Counter(s.stage.value for s in statuses)),
        by_health=_sorted_counts(Counter(s.health.value for s in statuses)),
        by_classification=_sorted_counts(
            Counter(s.classification.value for s in statuses)
        ),
        operator_required=sum(1 for s in statuses if s.operator_required),
        registration=registration,
        oldest=oldest,
        incidents=IncidentAggregate(
            runs=len(incident_runs),
            by_reason=_sorted_counts(reasons),
            artifact_entries=lifecycle.summary.integrity_incidents.count,
        ),
        artifacts=lifecycle.summary,
        unconfirmed_findings=lifecycle.unconfirmed_findings,
        recordings=RecordingAggregate(
            runs_with_known_bytes=len(known_bytes),
            total_bytes=sum(known_bytes),
            runs_with_known_message_count=len(known_messages),
            total_message_count=sum(known_messages),
        ),
        attention=attention,
        runs_included=include_runs,
        runs=tuple(statuses) if include_runs else (),
    )


def summary_record(report: AcquisitionOperationalReport) -> dict:
    """The aggregates as one flat-enough dict for a structured log line: no
    per-run statuses, only the facts a dashboard or an alert would read."""
    return {
        "root_uri": report.root_uri,
        "observed_at": report.observed_at.isoformat(),
        "runs": report.runs_total,
        "by_stage": report.by_stage,
        "by_health": report.by_health,
        "by_classification": report.by_classification,
        "operator_required": report.operator_required,
        "registration": report.registration.model_dump(mode="json"),
        "oldest": {
            state: {"run_id": work.run_id, "age_seconds": work.age_seconds}
            for state, work in report.oldest.items()
        },
        "incident_runs": report.incidents.runs,
        "artifacts": {
            "referenced": report.artifacts.referenced.model_dump(mode="json"),
            "pending": {
                "count": report.artifacts.pending.count,
                "bytes": report.artifacts.pending.bytes,
            },
            "orphan_candidates": {
                "count": report.artifacts.orphan_candidates.count,
                "bytes": report.artifacts.orphan_candidates.bytes,
            },
            "integrity_incidents": report.artifacts.integrity_incidents.count,
        },
        "recordings": report.recordings.model_dump(mode="json"),
        "attention": [item.run_id for item in report.attention],
    }


__all__ = [
    "NON_TERMINAL_STATES",
    "build_operational_report",
    "derive_health",
    "derive_stage",
    "derive_status",
    "summary_record",
]
