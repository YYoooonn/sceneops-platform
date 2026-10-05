"""Pure classification of one ``run_id`` (ADR-008 §5.1, L-1, L-10).

Input is observed facts -- the capture observation, the published-object
observation and its assessment, and PostgreSQL registration evidence. Output is
one :class:`AcquisitionState` plus machine-readable reasons. Nothing here reads
a store, a database or a clock, and nothing acts: classification and action are
separate steps (12.4 owns action).

Precedence, first match wins:

1. A RobotRunRecord exists (the only fact that means "registered"):
   a different ``manifest_checksum`` is a permanent conflict; any contradiction
   between the record, its ArtifactRecords and the objects is an integrity
   incident; otherwise ``registered`` -- whatever any Job says (W9).
2. ArtifactRecords without a RobotRunRecord: integrity incident (L-9).
3. Publication objects: contradicted manifest -> integrity incident; missing
   marker or recording -> publication incomplete; a valid manifest with its
   recording -> the Job-derived registration states.
4. No publication objects: the capture state.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sceneops_core.jobs.schemas import JobStatus
from sceneops_core.robots.capture_scan import CaptureClass, CaptureObservation
from sceneops_core.robots.published_scan import (
    PublicationAssessment,
    PublicationClass,
    PublishedRunObservation,
)
from sceneops_core.robots.registration_failures import RegistrationFailureClass

from .model import AcquisitionState, JobFacts, RegistrationEvidence

_ACTIVE_STATUSES = frozenset({JobStatus.PENDING, JobStatus.QUEUED, JobStatus.RUNNING})


@dataclass(frozen=True)
class ClassificationPolicy:
    """Thresholds that turn durable timestamps into a state. Every field
    defaults to ``None``: with no threshold, a state is derived from facts
    alone and no age ever promotes it.

    ``stall_candidate_after`` is deliberately unset in the platform: the value
    must exceed worst-case registration time and is decided from measured
    latency (ADR-008 §5.3, B4). This is the seam that decision plugs into, not a
    decision.
    """

    stall_candidate_after: timedelta | None = None


def _last_activity(job: JobFacts) -> datetime | None:
    stamps = [
        stamp
        for stamp in (job.created_at, job.queued_at, job.started_at, job.heartbeat_at)
        if stamp is not None
    ]
    return max(stamps) if stamps else None


def _capture_reasons(
    capture: CaptureObservation | None, *, capture_observed: bool
) -> list[str]:
    """What the capture volume says about a publication that is not complete."""
    if capture is None:
        return ["no_capture_source"] if capture_observed else []
    return {
        CaptureClass.FINALIZED_WITH_RECEIPT: ["resumable_from_capture"],
        CaptureClass.FINALIZED_NO_RECEIPT: ["capture_without_receipt"],
        CaptureClass.FINALIZED_RECEIPT_INVALID: ["capture_receipt_invalid"],
        CaptureClass.CAPTURE_UNFINISHED: ["capture_unfinished"],
    }[capture.classification]


def _capture_manifest_mismatches(
    capture: CaptureObservation | None, publication: PublishedRunObservation
) -> list[str]:
    """The receipt describes the bag that was published (L-6, L-7): a valid
    receipt that names different bytes or another robot contradicts the
    manifest."""
    if capture is None or capture.receipt is None or publication.manifest is None:
        return []
    reasons: list[str] = []
    if capture.receipt.recording_checksum != publication.manifest.recording_checksum:
        reasons.append("capture_receipt_recording_mismatch")
    if capture.receipt.robot_id != publication.manifest.robot_id:
        reasons.append("capture_receipt_robot_mismatch")
    return reasons


def _classify_registered(
    *,
    capture: CaptureObservation | None,
    publication: PublishedRunObservation | None,
    assessment: PublicationAssessment | None,
    registration: RegistrationEvidence,
) -> tuple[AcquisitionState, tuple[str, ...]]:
    record = registration.robot_run
    assert record is not None

    if publication is None or publication.manifest_object is None:
        return AcquisitionState.INTEGRITY_INCIDENT, ("registered_manifest_missing",)
    if publication.manifest is None:
        return AcquisitionState.INTEGRITY_INCIDENT, ("registered_manifest_malformed",)
    if publication.manifest_checksum != record.manifest_checksum:
        return AcquisitionState.PERMANENT_CONFLICT, ("manifest_checksum_differs",)

    reasons: list[str] = []
    assert assessment is not None
    if assessment.classification == PublicationClass.INTEGRITY_CONFLICT:
        reasons.extend(assessment.reasons)
    if assessment.classification == PublicationClass.MANIFEST_WITHOUT_RECORDING:
        reasons.append("registered_recording_object_missing")

    manifest_artifact = registration.manifest_artifact
    recording_artifact = registration.recording_artifact
    if manifest_artifact is None or recording_artifact is None:
        reasons.append("registered_artifact_record_missing")
    else:
        if manifest_artifact.checksum != record.manifest_checksum:
            reasons.append("artifact_record_manifest_checksum_mismatch")
        manifest = publication.manifest
        if (
            recording_artifact.checksum != manifest.recording_checksum
            or recording_artifact.size_bytes != manifest.recording_size_bytes
        ):
            reasons.append("artifact_record_recording_mismatch")
        if recording_artifact.uri != manifest.recording_uri:
            reasons.append("artifact_record_recording_uri_mismatch")

    reasons.extend(_capture_manifest_mismatches(capture, publication))
    if reasons:
        return AcquisitionState.INTEGRITY_INCIDENT, tuple(sorted(set(reasons)))
    return AcquisitionState.REGISTERED, ()


def _classify_registration(
    registration: RegistrationEvidence,
    *,
    policy: ClassificationPolicy,
    now: datetime | None,
) -> tuple[AcquisitionState, tuple[str, ...]]:
    jobs = registration.jobs
    # A success without a RobotRunRecord is reported, never believed.
    notes = (
        ["succeeded_job_without_robot_run"]
        if any(job.status == JobStatus.SUCCEEDED for job in jobs)
        else []
    )

    active = [job for job in jobs if job.status in _ACTIVE_STATUSES]
    if active:
        if policy.stall_candidate_after is not None:
            if now is None:
                raise ValueError("a stall threshold needs the observation time")
            threshold = policy.stall_candidate_after
            if all(
                (last := _last_activity(job)) is not None and now - last > threshold
                for job in active
            ):
                return (
                    AcquisitionState.REGISTRATION_STALLED_CANDIDATE,
                    tuple(sorted(["job_inactive_beyond_threshold", *notes])),
                )
        return (
            AcquisitionState.REGISTRATION_ACTIVE,
            tuple(sorted([f"job_{active[-1].status.value}", *notes])),
        )

    if not jobs:
        return AcquisitionState.REGISTRATION_PENDING, ("no_registration_job",)

    latest = jobs[-1]
    if latest.status == JobStatus.FAILED:
        permanent = latest.failure_class == RegistrationFailureClass.PERMANENT
        state = (
            AcquisitionState.REGISTRATION_FAILED_PERMANENT
            if permanent
            else AcquisitionState.REGISTRATION_FAILED_TRANSIENT
        )
        return state, tuple(
            sorted([f"error_type:{latest.error_type or 'unknown'}", *notes])
        )
    return (
        AcquisitionState.REGISTRATION_PENDING,
        tuple(sorted([f"latest_job_{latest.status.value}", *notes])),
    )


def classify_run(
    *,
    capture: CaptureObservation | None,
    capture_observed: bool,
    publication: PublishedRunObservation | None,
    assessment: PublicationAssessment | None,
    registration: RegistrationEvidence,
    policy: ClassificationPolicy | None = None,
    now: datetime | None = None,
) -> tuple[AcquisitionState, tuple[str, ...]]:
    """The lifecycle state of one run and the reasons for it. ``capture`` and
    ``publication`` may each be absent, never both."""
    if capture is None and publication is None and registration.robot_run is None:
        raise ValueError("a run needs at least one observed fact")
    policy = policy or ClassificationPolicy()

    if registration.robot_run is not None:
        return _classify_registered(
            capture=capture,
            publication=publication,
            assessment=assessment,
            registration=registration,
        )

    if registration.recording_artifact or registration.manifest_artifact:
        return (
            AcquisitionState.INTEGRITY_INCIDENT,
            ("artifact_record_without_robot_run",),
        )

    if publication is None:
        assert capture is not None
        return {
            CaptureClass.CAPTURE_UNFINISHED: (AcquisitionState.CAPTURE_UNFINISHED, ()),
            CaptureClass.FINALIZED_NO_RECEIPT: (
                AcquisitionState.FINALIZED_NO_RECEIPT,
                (),
            ),
            CaptureClass.FINALIZED_WITH_RECEIPT: (
                AcquisitionState.PUBLISH_PENDING,
                (),
            ),
            CaptureClass.FINALIZED_RECEIPT_INVALID: (
                AcquisitionState.INTEGRITY_INCIDENT,
                ("capture_receipt_invalid",),
            ),
        }[capture.classification]

    assert assessment is not None
    if assessment.classification == PublicationClass.INTEGRITY_CONFLICT:
        return AcquisitionState.INTEGRITY_INCIDENT, assessment.reasons

    if assessment.classification != PublicationClass.PUBLISHED:
        reasons = [
            assessment.classification.value,
            *assessment.reasons,
            *_capture_reasons(capture, capture_observed=capture_observed),
        ]
        return AcquisitionState.PUBLICATION_INCOMPLETE, tuple(sorted(set(reasons)))

    mismatches = _capture_manifest_mismatches(capture, publication)
    if mismatches:
        return AcquisitionState.INTEGRITY_INCIDENT, tuple(sorted(mismatches))
    return _classify_registration(registration, policy=policy, now=now)


__all__ = ["ClassificationPolicy", "classify_run"]
