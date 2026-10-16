"""Domain-neutral source and producer provenance primitives (ADR-007 §27,
ADR-015 §3, §4, §6).

Platform primitives shared by the Scene and Episode domains, which compose
them into their own lineage and record types: source bindings and
revisions, the pinned sources builders read, unit identity, revision
identity and the producer fingerprint. Nothing here is a Scene/Episode
model.
"""

from .producer import (
    EXECUTION_SCOPED_CONFIG_KEYS,
    PRODUCER_FINGERPRINT_SCHEMA_V1,
    ProducerFingerprintMismatchError,
    ProducerInfo,
    compute_producer_fingerprint,
    normalize_build_config,
)
from .identity import (
    CANONICAL_DOMAINS,
    UNIT_ID_SCHEMA_V2,
    CanonicalDomain,
    canonical_unit_id,
    unit_id_domain,
)
from .pinned import PinnedContent, PinnedRecording
from .revisions import (
    MANIFEST_ARTIFACT_ID_SCHEMA_V1,
    InconsistentRevisionsError,
    NonDeterministicProducerError,
    RevisionConflictError,
    RevisionDecision,
    RevisionPinError,
    UnitRevisionFacts,
    check_revision_pin,
    decide_revision_registration,
    manifest_artifact_id_for,
)
from .source_time import INT64_MAX, SourceTimestampNs, SourceTimeUnit, promote_to_ns
from .sources import (
    SHA256_CHECKSUM_PATTERN,
    UNIT_KEY_MAX_LENGTH,
    ContentSourceRevision,
    ContentUnitSource,
    RecordingSegmentSource,
    RecordingSourceRevision,
    SourceBinding,
    SourceRevision,
)

__all__ = [
    "CANONICAL_DOMAINS",
    "EXECUTION_SCOPED_CONFIG_KEYS",
    "INT64_MAX",
    "MANIFEST_ARTIFACT_ID_SCHEMA_V1",
    "PRODUCER_FINGERPRINT_SCHEMA_V1",
    "SHA256_CHECKSUM_PATTERN",
    "UNIT_ID_SCHEMA_V2",
    "UNIT_KEY_MAX_LENGTH",
    "CanonicalDomain",
    "ContentSourceRevision",
    "ContentUnitSource",
    "InconsistentRevisionsError",
    "NonDeterministicProducerError",
    "PinnedContent",
    "PinnedRecording",
    "ProducerFingerprintMismatchError",
    "ProducerInfo",
    "RecordingSegmentSource",
    "RecordingSourceRevision",
    "RevisionConflictError",
    "RevisionDecision",
    "RevisionPinError",
    "SourceBinding",
    "SourceRevision",
    "SourceTimeUnit",
    "SourceTimestampNs",
    "UnitRevisionFacts",
    "canonical_unit_id",
    "check_revision_pin",
    "compute_producer_fingerprint",
    "decide_revision_registration",
    "manifest_artifact_id_for",
    "normalize_build_config",
    "promote_to_ns",
    "unit_id_domain",
]
