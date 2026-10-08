"""Acquisition status and operational report model (ADR-008 §7).

Nothing here is stored. ``AcquisitionStatus`` is the per-run view of ADR-008
§7.1 and ``AcquisitionOperationalReport`` is the aggregate of §7.2; both are
pure functions of the reconciliation report, the artifact lifecycle report and
the observation time ``observed_at``. There is no status table, no lifecycle
column and no cache: the same durable facts and the same ``observed_at`` give
byte-identical JSON, and a different ``observed_at`` changes only the ages.

The view adds no lifecycle semantics. ``classification`` is the reconciler's
state, ``stage`` is the furthest durable fact (ADR-008 L-1), and ``health`` and
``operator_required`` are fixed functions of the classification and its reasons
(``derive.py``). Timestamps keep the clock they were written on: ``finalized_at``
is the capture host's, ``published_at`` the object store's and ``registered_at``
PostgreSQL's, so a duration between two of them is indicative across hosts.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict

from sceneops_core.jobs.schemas import JobStatus
from sceneops_acquisition.lifecycle.vocabulary import (
    LifecycleClass,
    OrphanReason,
    RiskTier,
)
from sceneops_recording.published_scan import PublicationClass
from sceneops_acquisition.registration_failures import RegistrationFailureClass

from sceneops_acquisition.lifecycle.model import (
    LifecyclePolicyFacts,
    LifecycleSummary,
    ObjectRole,
)
from sceneops_acquisition.reconciliation.model import AcquisitionState

ACQUISITION_OPERATIONAL_REPORT_SCHEMA_V1: Final = (
    "sceneops.acquisition_operational_report/v1"
)


class AcquisitionStage(StrEnum):
    """The furthest stage whose durable fact holds (ADR-008 §3.1)."""

    CAPTURE_UNFINISHED = "capture_unfinished"
    FINALIZED = "finalized"
    PUBLISHED = "published"
    REGISTERED = "registered"


class AcquisitionHealth(StrEnum):
    # The run is complete and consistent.
    OK = "ok"
    # Work is expected to progress by itself (in flight, queued for the next
    # pass, or waiting for a capture or publication to finish).
    PENDING = "pending"
    # A registration Job shows no activity beyond the stall threshold.
    STALLED = "stalled"
    # Automatic recovery is over: a permanent failure or a spent budget.
    FAILED = "failed"
    # Durable facts contradict each other. Reported, never repaired (L-9).
    INCONSISTENT = "inconsistent"


class FailureStage(StrEnum):
    CAPTURE = "capture"
    PUBLISH = "publish"
    REGISTER = "register"


class _StatusModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FailureFacts(_StatusModel):
    """The newest failed registration Job of the run's execution key. Only the
    register stage leaves a durable failure (a failed publish is the
    Publisher's report, not a fact the platform can read back)."""

    stage: FailureStage
    failure_class: RegistrationFailureClass
    error_type: str | None
    job_id: str
    # Attempts without success this logical registration has consumed.
    attempts: int
    attempt_budget: int | None
    attempts_remaining: int | None


class CaptureStatus(_StatusModel):
    """What the capture report says; present only when one was supplied."""

    classification: str
    robot_id: str | None
    finalization_reason: str | None
    finalized_at: datetime | None
    recording_bytes: int | None
    message_count: int | None
    # Newest mtime of the capture directory; the capture host's clock.
    modified_at: datetime | None


class PublicationStatus(_StatusModel):
    classification: PublicationClass
    manifest_uri: str | None
    manifest_checksum: str | None
    # The manifest object's modification time: the store's clock.
    published_at: datetime | None
    recording_bytes: int | None
    message_count: int | None
    channel_count: int | None
    source_clock: str | None


class JobSummary(_StatusModel):
    job_id: str
    status: JobStatus
    created_at: datetime | None
    error_type: str | None


class RegistrationStatus(_StatusModel):
    registered_at: datetime | None
    execution_key: str | None
    job_count: int
    active_job_ids: tuple[str, ...]
    failed_job_count: int
    abandoned_job_count: int
    attempt_budget: int | None
    attempts_remaining: int | None
    latest_job: JobSummary | None


class Timestamps(_StatusModel):
    finalized_at: datetime | None
    published_at: datetime | None
    registered_at: datetime | None
    # The newest durable activity of any stage (capture directory, publication
    # objects, registration Jobs, the registration itself).
    last_activity_at: datetime | None


class Durations(_StatusModel):
    """Seconds between two stage timestamps, when both are known. Each end is
    on its own host's clock (see module docstring), so a value can be slightly
    negative; it is reported as observed, never clamped."""

    finalize_to_publish_seconds: float | None
    publish_to_register_seconds: float | None
    finalize_to_register_seconds: float | None


class ArtifactSummary(_StatusModel):
    referenced_objects: int
    pending_objects: int
    orphan_candidate_objects: int
    integrity_incident_entries: int
    referenced_bytes: int
    pending_bytes: int
    orphan_candidate_bytes: int


class ArtifactFinding(_StatusModel):
    """A lifecycle entry of this run that is not simply ``referenced``."""

    lifecycle_class: LifecycleClass
    role: ObjectRole | None
    uri: str | None
    reasons: tuple[str, ...]
    orphan_reason: OrphanReason | None
    risk: RiskTier | None
    size_bytes: int | None


class AcquisitionStatus(_StatusModel):
    run_id: str
    robot_id: str | None
    stage: AcquisitionStage
    health: AcquisitionHealth
    # The reconciler's state for the run and the reasons behind it.
    classification: AcquisitionState
    reasons: tuple[str, ...]
    # True when nothing automatic will move the run on and a person must
    # decide (a permanent failure, a conflict, an incident, a capture without
    # a receipt, a recording with neither a manifest nor a capture to resume).
    operator_required: bool
    failure: FailureFacts | None
    capture: CaptureStatus | None
    publication: PublicationStatus | None
    registration: RegistrationStatus
    timestamps: Timestamps
    durations: Durations
    # Known from the manifest, else the receipt; None when neither is observed.
    recording_bytes: int | None
    message_count: int | None
    channel_count: int | None
    finalization_reason: str | None
    # observed_at - timestamps.last_activity_at, floored at zero.
    age_seconds: float | None
    artifacts: ArtifactSummary
    findings: tuple[ArtifactFinding, ...]


class OldestWork(_StatusModel):
    run_id: str
    age_seconds: float
    last_activity_at: datetime


class RegistrationAggregate(_StatusModel):
    # Not registered, by what the registration is doing.
    pending: int
    active: int
    stalled_candidates: int
    failed_transient: int
    failed_permanent: int
    # Of the unregistered runs, those whose attempt budget is spent.
    budget_exhausted: int
    # Attempts consumed by runs that are not (yet) registered, and how many of
    # those attempts were stalled Jobs the reconciler abandoned.
    failed_jobs: int
    abandoned_jobs: int
    # failed-Job count -> number of unregistered runs with that many attempts
    # consumed (keys sorted numerically as strings).
    attempts_used: dict[str, int]


class IncidentAggregate(_StatusModel):
    # Runs whose state is integrity_incident or permanent_conflict.
    runs: int
    # Run-level reasons (a run with several counts under each).
    by_reason: dict[str, int]
    # Lifecycle entries that are incidents (a dangling record and the run whose
    # objects it names are separate entries): ``artifacts.integrity_incidents``
    # of the lifecycle summary, repeated here so one block answers "is anything
    # damaged".
    artifact_entries: int


class RecordingAggregate(_StatusModel):
    runs_with_known_bytes: int
    total_bytes: int
    runs_with_known_message_count: int
    total_message_count: int


class AttentionItem(_StatusModel):
    run_id: str
    classification: AcquisitionState
    health: AcquisitionHealth
    reasons: tuple[str, ...]
    operator_required: bool
    age_seconds: float | None


class AcquisitionOperationalReport(_StatusModel):
    schema_version: Literal["sceneops.acquisition_operational_report/v1"] = (
        ACQUISITION_OPERATIONAL_REPORT_SCHEMA_V1
    )
    root_uri: str
    observed_at: datetime
    policy: LifecyclePolicyFacts
    runs_total: int
    by_stage: dict[str, int]
    by_health: dict[str, int]
    by_classification: dict[str, int]
    operator_required: int
    registration: RegistrationAggregate
    # Per non-terminal classification, the run whose newest durable activity is
    # oldest. A run that is merely slow and one that is lost look alike here;
    # ``age_seconds`` is time since last activity, not a judgement.
    oldest: dict[str, OldestWork]
    incidents: IncidentAggregate
    # Referenced / pending / orphan-candidate / incident objects and bytes.
    artifacts: LifecycleSummary
    unconfirmed_findings: int
    recordings: RecordingAggregate
    # Every run that is stalled, failed, inconsistent or needs an operator.
    attention: tuple[AttentionItem, ...]
    # False when ``runs`` was left out (summary mode); the aggregates above are
    # always computed from every run.
    runs_included: bool
    runs: tuple[AcquisitionStatus, ...]

    def to_json_dict(self) -> dict:
        return self.model_dump(mode="json")
