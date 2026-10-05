"""Reconciliation report model (ADR-008 §5.1, §7.4).

The report is the contract; its transport (the one-shot CLI's JSON today, an
API endpoint later) is not. It is a pure function of durable facts: it holds
no wall-clock reading, so reconciling an unchanged system twice yields
byte-identical reports. Ages are not stored; the durable timestamps they would
be derived from are (``created_at``, ``heartbeat_at``, ``last_modified``,
``registered_at``).

Observation and classification are separate layers. This module only defines
the shapes: ``classify.py`` turns observed facts into a state, and nothing here
or there acts on a state (ADR-008 L-10).
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
    # Every in-flight Job is older than the caller-supplied stall threshold.
    # Reachable only when a threshold is supplied: the platform fixes none yet.
    # (registration_stalled)
    REGISTRATION_STALLED_CANDIDATE = "registration_stalled_candidate"
    # The newest Job failed with a transient error class.
    # (registration_failed_transient)
    REGISTRATION_FAILED_TRANSIENT = "registration_failed_transient"
    # The newest Job failed with a permanent error class.
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
    failed_job_count: int


class RunReport(_ReportModel):
    run_id: str
    state: AcquisitionState
    # Sorted machine-readable codes explaining the state; may be empty.
    reasons: tuple[str, ...]
    capture: CaptureObservation | None
    publication: PublishedRunObservation | None
    publication_assessment: PublicationAssessment | None
    registration: RegistrationEvidence


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

    def to_json_dict(self) -> dict:
        return self.model_dump(mode="json")
