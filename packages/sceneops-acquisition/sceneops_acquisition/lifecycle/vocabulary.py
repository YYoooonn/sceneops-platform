"""Vocabulary of the artifact lifecycle classification (ADR-008 §6).

Classification is read-only: these names describe what an object under the
RobotRun root *is* with respect to durable platform records. They never
authorize an action. Deletion, quarantine and repair are not part of this
contract (ADR-008 §6.3).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

# An unreferenced object younger than this may belong to an in-flight write
# that has not reached its registration step yet (PN-1). Initial values are the
# ADR-008 §6.2 proposal; they are defaults of a configurable policy, not a
# measured property of the pipeline.
DEFAULT_PENDING_GRACE_SECONDS: Final = 24 * 3600.0

# An unreferenced, unprotected object must also be at least this old to be an
# orphan candidate. Between the two graces it stays pending
# (``within_orphan_grace``).
DEFAULT_ORPHAN_GRACE_SECONDS: Final = 7 * 24 * 3600.0


class LifecycleClass(StrEnum):
    # An ArtifactRecord references the object's URI and nothing contradicts it.
    REFERENCED = "referenced"
    # Unreferenced but protected (PN-1..PN-4, or inside the orphan grace).
    PENDING = "pending"
    # Unreferenced, unprotected and old enough. Not garbage: a reason and a
    # risk tier accompany it and a human decides (ADR-008 §6.2).
    ORPHAN_CANDIDATE = "orphan_candidate"
    # Durable facts contradict each other. Never a candidate, never repaired
    # (ADR-008 L-9).
    INTEGRITY_INCIDENT = "integrity_incident"


class OrphanReason(StrEnum):
    # O1: recording present, manifest absent, no capture source anywhere.
    RECORDING_WITHOUT_MANIFEST = "recording_without_manifest"
    # O2: a consistent publication whose registration failed permanently
    # (including a spent attempt budget).
    RECORDING_MANIFEST_PERMANENTLY_FAILED = "recording_manifest_permanently_failed"


class RiskTier(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# O1 and O2 may be the only copy of a recording (ADR-008 §6.2).
ORPHAN_RISK: Final[dict[OrphanReason, RiskTier]] = {
    OrphanReason.RECORDING_WITHOUT_MANIFEST: RiskTier.HIGH,
    OrphanReason.RECORDING_MANIFEST_PERMANENTLY_FAILED: RiskTier.HIGH,
}

# Pending reason codes (ADR-008 §6.2).
PN1_WITHIN_PENDING_GRACE: Final = "pn1_within_pending_grace"
PN2_REGISTRATION_UNFINISHED: Final = "pn2_registration_unfinished"
PN3_CAPTURE_SOURCE_PRESENT: Final = "pn3_capture_source_present"
PN3_CAPTURE_SOURCE_UNOBSERVED: Final = "pn3_capture_source_unobserved"
PN4_ACTIVE_REGISTRATION_JOB: Final = "pn4_active_registration_job"
WITHIN_ORPHAN_GRACE: Final = "within_orphan_grace"

# Reverse-integrity reason codes (ADR-008 §6.1).
DANGLING_REFERENCE: Final = "dangling_reference"
REFERENCED_CORRUPT: Final = "referenced_corrupt"
REFERENCED_SIZE_MISMATCH: Final = "referenced_size_mismatch"
REGISTERED_OBJECT_UNREFERENCED: Final = "registered_object_unreferenced"
UNCLASSIFIABLE_STATE: Final = "unclassifiable_state"
ACTIVE_REGISTRATION_JOB: Final = "active_registration_job"


__all__ = [
    "ACTIVE_REGISTRATION_JOB",
    "DANGLING_REFERENCE",
    "DEFAULT_ORPHAN_GRACE_SECONDS",
    "DEFAULT_PENDING_GRACE_SECONDS",
    "LifecycleClass",
    "ORPHAN_RISK",
    "OrphanReason",
    "PN1_WITHIN_PENDING_GRACE",
    "PN2_REGISTRATION_UNFINISHED",
    "PN3_CAPTURE_SOURCE_PRESENT",
    "PN3_CAPTURE_SOURCE_UNOBSERVED",
    "PN4_ACTIVE_REGISTRATION_JOB",
    "REFERENCED_CORRUPT",
    "REFERENCED_SIZE_MISMATCH",
    "REGISTERED_OBJECT_UNREFERENCED",
    "RiskTier",
    "UNCLASSIFIABLE_STATE",
    "WITHIN_ORPHAN_GRACE",
]
