"""Artifact lifecycle report (ADR-008 §6, §8 step 12.5).

The report is the contract; its transport (the one-shot CLI's JSON today) is
not. It is a pure function of the durable facts *and* of the observation time
``observed_at`` that grace periods are measured against, which is part of the
report so that the same facts and the same ``observed_at`` give byte-identical
output. Nothing here acts on a classification: deletion, quarantine and repair
are outside this phase (ADR-008 §6.3).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict

from sceneops_acquisition.lifecycle.vocabulary import (
    LifecycleClass,
    OrphanReason,
    RiskTier,
)

ARTIFACT_LIFECYCLE_REPORT_SCHEMA_V1: Final = "sceneops.artifact_lifecycle_report/v1"


class EntrySubject(StrEnum):
    # An object that the store listing contains.
    OBJECT = "object"
    # An ArtifactRecord under the root whose object is absent from the store.
    ARTIFACT_RECORD = "artifact_record"
    # A RobotRunRecord none of whose objects are in the store.
    ROBOT_RUN_RECORD = "robot_run_record"


class ObjectRole(StrEnum):
    RECORDING = "recording"
    MANIFEST = "manifest"
    # Any other object: referenced by an ArtifactRecord, or unrecognized.
    OTHER = "other"


class _ReportModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LifecycleEntry(_ReportModel):
    subject: EntrySubject
    run_id: str | None
    # The object's URI, or the URI an ArtifactRecord expects. None for a
    # RobotRunRecord entry.
    uri: str | None
    role: ObjectRole | None
    # ArtifactRecords carrying exactly this URI (objects), or the record itself
    # (artifact_record entries).
    artifact_ids: tuple[str, ...]
    lifecycle_class: LifecycleClass
    # Sorted, machine-readable. Pending and incident entries always carry at
    # least one; a referenced entry has none.
    reasons: tuple[str, ...]
    orphan_reason: OrphanReason | None
    risk: RiskTier | None
    # Listed size (objects) or the size the record claims (artifact_record).
    size_bytes: int | None
    last_modified: datetime | None
    # observed_at - last_modified, floored at zero (objects only).
    age_seconds: float | None
    # The 12.3 state of the run and the reasons behind it, for context.
    acquisition_state: str | None
    acquisition_reasons: tuple[str, ...]


class UnclassifiedObject(_ReportModel):
    """An unreferenced object the 12.5 contract deliberately does not classify
    (ADR-008 §6.4: O3-O5 are not implemented). Never a candidate."""

    uri: str
    size_bytes: int
    last_modified: datetime
    age_seconds: float
    run_id: str | None
    reason: str


class ByteCount(_ReportModel):
    count: int = 0
    bytes: int = 0


class ReferencedSummary(ByteCount):
    pass


class PendingSummary(ByteCount):
    # Age of the oldest pending object; None when nothing is pending.
    oldest_age_seconds: float | None = None
    # An entry with several reasons counts under each.
    by_reason: dict[str, ByteCount] = {}


class OrphanSummary(ByteCount):
    by_reason: dict[str, ByteCount] = {}
    by_risk: dict[str, ByteCount] = {}


class IncidentSummary(_ReportModel):
    # Entries of any subject.
    count: int = 0
    # Listed bytes of OBJECT entries only; record entries have no object.
    object_bytes: int = 0
    by_subject: dict[str, int] = {}
    # An entry with several reasons counts under each.
    by_reason: dict[str, int] = {}


class LifecycleSummary(_ReportModel):
    referenced: ReferencedSummary
    pending: PendingSummary
    orphan_candidates: OrphanSummary
    integrity_incidents: IncidentSummary
    unclassified: ByteCount


class LifecyclePolicyFacts(_ReportModel):
    """The parameters the classification ran under (configuration, not a clock
    reading)."""

    pending_grace_seconds: float
    orphan_grace_seconds: float
    stall_threshold_seconds: float
    attempt_budget: int
    # False when no capture report was supplied: PN-3 cannot be evaluated, so
    # an incomplete publication is protected instead of becoming O1.
    capture_observed: bool
    # True when every registered recording was re-read and hashed; otherwise
    # registered recordings are checked by listing size only.
    registered_recordings_hashed: bool


class ArtifactLifecycleReport(_ReportModel):
    schema_version: Literal["sceneops.artifact_lifecycle_report/v1"] = (
        ARTIFACT_LIFECYCLE_REPORT_SCHEMA_V1
    )
    root_uri: str
    observed_at: datetime
    policy: LifecyclePolicyFacts
    summary: LifecycleSummary
    entries: tuple[LifecycleEntry, ...]
    unclassified: tuple[UnclassifiedObject, ...]
    # Findings dropped because the object appeared between the listing and the
    # confirming existence check: the scan raced a concurrent publication, so
    # the next report decides them. Zero on a quiescent system.
    unconfirmed_findings: int = 0

    def to_json_dict(self) -> dict:
        return self.model_dump(mode="json")
