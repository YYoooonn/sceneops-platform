"""Reconciliation report model (ADR-008 §5.1, §7.4).

The report is the contract; its transport (the one-shot CLI's JSON today, an
API endpoint later) is not. It is a pure function of durable facts: it holds
no wall-clock reading, so reconciling an unchanged system twice yields
byte-identical reports. Ages are not stored; the durable timestamps they would
be derived from are (``created_at``, ``heartbeat_at``, ``last_modified``,
``registered_at``).

Observation and classification are separate layers. This module only defines
the shapes: ``classify.py`` turns observed facts into a state, and nothing here
or there acts on a state (ADR-008 L-10). ``recovery.py`` acts, and records what
it did as ``RecoveryAction`` entries beside -- never inside -- the observed
states, so a run's state is always what was observed before any action.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict

from sceneops_core.jobs.schemas import JobStatus
from sceneops_core.robots.capture_scan import CaptureObservation
from sceneops_core.robots.published_scan import (
    PublicationAssessment,
    PublishedRunObservation,
    UnrecognizedObject,
)
from sceneops_core.robots.registration_failures import RegistrationFailureClass

RECONCILIATION_REPORT_SCHEMA_V1: Final = "sceneops.reconciliation_report/v1"


class AcquisitionState(StrEnum):
    """The lifecycle state of one ``run_id``, derived only from durable facts
    (ADR-008 L-1). Names map to ADR-008 §5.1 as noted.

    The order is the lifecycle order; a run is in exactly one state.
    """

    # <root>/.partial/<run_id>/ exists, no finalized bag. (capture_unfinished)
    CAPTURE_UNFINISHED = "capture_unfinished"
    # Finalized bag without a receipt: publishable only with explicit inputs.
    # (finalized_no_receipt)
    FINALIZED_NO_RECEIPT = "finalized_no_receipt"
    # Finalized bag with a valid receipt and nothing in the object store.
    # (finalized_unpublished)
    PUBLISH_PENDING = "publish_pending"
    # Some publication objects exist but the valid manifest marker and its
    # recording do not both. (publishing_incomplete,
    # unpublished_recording_no_source, malformed manifest)
    PUBLICATION_INCOMPLETE = "publication_incomplete"
    # Published, no RobotRunRecord, and no registration Job in flight.
    # (published_unregistered)
    REGISTRATION_PENDING = "registration_pending"
    # A REGISTER_ROBOT_RUN Job is PENDING / QUEUED / RUNNING. (registration_pending)
    REGISTRATION_ACTIVE = "registration_active"
    # Every in-flight Job is older than the stall threshold. Reachable only when
    # the caller supplies a threshold (the one-shot command always does).
    # (registration_stalled)
    REGISTRATION_STALLED_CANDIDATE = "registration_stalled_candidate"
    # The newest Job failed with a transient error class.
    # (registration_failed_transient)
    REGISTRATION_FAILED_TRANSIENT = "registration_failed_transient"
    # The newest Job failed with a permanent error class, or the logical
    # registration spent its attempt budget (reason ``attempt_budget_exhausted``).
    # (registration_failed_permanent)
    REGISTRATION_FAILED_PERMANENT = "registration_failed_permanent"
    # RobotRunRecord exists and its manifest_checksum equals the manifest in
    # the store; takes precedence over any Job state. (registered)
    REGISTERED = "registered"
    # RobotRunRecord exists with a different manifest_checksum. (registered_conflict)
    PERMANENT_CONFLICT = "permanent_conflict"
    # Durable facts contradict each other: corrupt or mismatched bytes,
    # ArtifactRecords without a RobotRunRecord, a record whose objects are
    # gone, an unusable receipt. Reported, never repaired (L-9). (inconsistent)
    INTEGRITY_INCIDENT = "integrity_incident"


class _ReportModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RobotRunFacts(_ReportModel):
    robot_id: str
    manifest_checksum: str
    recording_artifact_id: str
    manifest_artifact_id: str
    registered_at: datetime | None


class ArtifactRecordFacts(_ReportModel):
    artifact_id: str
    uri: str
    size_bytes: int | None
    checksum: str | None


class JobFacts(_ReportModel):
    job_id: str
    status: JobStatus
    created_at: datetime | None
    queued_at: datetime | None
    started_at: datetime | None
    heartbeat_at: datetime | None
    finished_at: datetime | None
    error_type: str | None
    failure_class: RegistrationFailureClass | None


class RegistrationEvidence(_ReportModel):
    """What PostgreSQL knows about one run. ``robot_run`` is the only fact that
    means "registered"; Jobs are evidence of attempts, never of success."""

    robot_run: RobotRunFacts | None
    recording_artifact: ArtifactRecordFacts | None
    manifest_artifact: ArtifactRecordFacts | None
    # Execution key of REGISTER_ROBOT_RUN(manifest_uri); None without a
    # manifest object (nothing could have been submitted).
    execution_key: str | None
    # Every REGISTER_ROBOT_RUN Job of that key, oldest first.
    jobs: tuple[JobFacts, ...]
    # Attempts without success consumed by this logical registration (ADR-008
    # §5.2): every FAILED Job of the execution key, abandoned ones included.
    # Replacement Jobs share it; a new Job row never resets it.
    failed_job_count: int
    # Of those, the Jobs the reconciler abandoned (error type ``JobAbandoned``).
    abandoned_job_count: int = 0
    # The attempt budget in force when the report was made; None when the
    # caller supplied none (classification then enforces no budget).
    attempt_budget: int | None = None
    # ``attempt_budget - failed_job_count``, floored at zero.
    attempts_remaining: int | None = None


class RunReport(_ReportModel):
    run_id: str
    state: AcquisitionState
    # Sorted machine-readable codes explaining the state; may be empty.
    reasons: tuple[str, ...]
    capture: CaptureObservation | None
    publication: PublishedRunObservation | None
    publication_assessment: PublicationAssessment | None
    registration: RegistrationEvidence


class RecoveryActionKind(StrEnum):
    # registration_pending without any Job: submit REGISTER_ROBOT_RUN.
    SUBMIT_REGISTRATION = "submit_registration"
    # Transient failed registration with budget left: submit again.
    RETRY_REGISTRATION = "retry_registration"
    # Stalled Job abandoned (FAILED / JobAbandoned) and replaced by a forced Job.
    REPLACE_STALLED_JOB = "replace_stalled_job"
    # Stalled Job abandoned; the attempt it consumed spent the budget, so no
    # replacement follows.
    ABANDON_STALLED_JOB = "abandon_stalled_job"
    # An eligible state the reconciler deliberately did not act on.
    NONE = "none"


class RecoveryOutcome(StrEnum):
    # A Job was created and handed to the broker.
    SUBMITTED = "submitted"
    # An equivalent Job already existed (a concurrent submission); none created.
    DEDUPLICATED = "deduplicated"
    # The Job was created and committed but the broker refused the dispatch.
    # The Job is preserved (PENDING / QUEUED) and a later pass recovers it as
    # a stalled Job; nothing was rolled back and nothing is reported as done.
    DISPATCH_FAILED = "dispatch_failed"
    # The stalled Job was abandoned, but no replacement follows (budget spent).
    ABANDONED = "abandoned"
    # Another reconciler (or the worker) changed the Job first; this pass did
    # not act on it.
    LOST_RACE = "lost_race"
    # Deliberately not acted on; ``reason`` says why.
    SKIPPED = "skipped"
    # The action raised; the pass continued with the next run.
    ERROR = "error"


class RecoveryAction(_ReportModel):
    run_id: str
    kind: RecoveryActionKind
    outcome: RecoveryOutcome
    # Stable machine-readable code for SKIPPED / ERROR / DISPATCH_FAILED.
    reason: str | None = None
    # The Job this action created (or deduplicated onto).
    job_id: str | None = None
    # Jobs this action moved to FAILED / JobAbandoned.
    abandoned_job_ids: tuple[str, ...] = ()
    # failed_job_count / budget as observed before the action.
    attempts_used: int | None = None
    attempt_budget: int | None = None
    error: str | None = None
    # The run's state as observed before the action (always set), and as
    # observed again after the pass's mutating actions (None when no second
    # observation was made, e.g. a skip). Evidence of what the action did, not
    # a promise of what the run is now.
    state_before: AcquisitionState | None = None
    state_after: AcquisitionState | None = None
    # Wall time of the action itself; zero for a skip.
    duration_ms: int | None = None


class RecoveryPolicyFacts(_ReportModel):
    """The recovery parameters the report was made under (configuration, not
    a clock reading)."""

    stall_threshold_seconds: float | None
    attempt_budget: int | None
    max_actions: int | None = None


class ReconciliationReport(_ReportModel):
    schema_version: Literal["sceneops.reconciliation_report/v1"] = (
        RECONCILIATION_REPORT_SCHEMA_V1
    )
    root_uri: str
    # False when no capture report was supplied: capture states are then
    # unobservable, not absent.
    capture_observed: bool
    runs: tuple[RunReport, ...]
    # Objects under the scan root that belong to no run (reported only).
    unrecognized_objects: tuple[UnrecognizedObject, ...]
    # Capture-root entries that are not run directories (reported only).
    capture_unrecognized_entries: tuple[str, ...]
    # state value -> number of runs; keys sorted.
    counts: dict[str, int]
    # "observe" (the default: nothing was written) or "apply" (bounded recovery
    # ran). States above are always the facts observed before any action.
    mode: Literal["observe", "apply"] = "observe"
    policy: RecoveryPolicyFacts | None = None
    actions: tuple[RecoveryAction, ...] = ()
    # Eligible actions left for the next pass because the per-pass bound was hit.
    actions_deferred: int = 0

    def to_json_dict(self) -> dict:
        return self.model_dump(mode="json")
