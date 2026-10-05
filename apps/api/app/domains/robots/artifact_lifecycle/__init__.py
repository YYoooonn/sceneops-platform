"""Read-only artifact lifecycle classification of the RobotRun root (ADR-008 §6,
§8 step 12.5): referenced / pending / orphan candidate / integrity incident.
Classification only; nothing here deletes, quarantines or repairs."""

from .classify import (
    ArtifactLifecyclePolicy,
    ClassifiedArtifacts,
    classify_artifact_lifecycle,
    summarize,
)
from .model import (
    ARTIFACT_LIFECYCLE_REPORT_SCHEMA_V1,
    ArtifactLifecycleReport,
    EntrySubject,
    LifecycleEntry,
    LifecycleSummary,
    ObjectRole,
    UnclassifiedObject,
)
from .service import artifact_lifecycle_once

__all__ = [
    "ARTIFACT_LIFECYCLE_REPORT_SCHEMA_V1",
    "ArtifactLifecyclePolicy",
    "ArtifactLifecycleReport",
    "ClassifiedArtifacts",
    "EntrySubject",
    "LifecycleEntry",
    "LifecycleSummary",
    "ObjectRole",
    "UnclassifiedObject",
    "artifact_lifecycle_once",
    "classify_artifact_lifecycle",
    "summarize",
]
