"""Domain-neutral source and producer provenance primitives (ADR-007 §27,
ADR-015 §4, §6).

Platform primitives shared by the Scene and Episode domains, which compose
them into their own lineage types: source bindings and revisions, the
pinned sources builders read, unit identity and the producer fingerprint.
Nothing here is a Scene/Episode model.
"""

from .producer import (
    EXECUTION_SCOPED_CONFIG_KEYS,
    PRODUCER_FINGERPRINT_SCHEMA_V1,
    ProducerFingerprintMismatchError,
    ProducerInfo,
    compute_producer_fingerprint,
    normalize_build_config,
)
from .identity import UNIT_ID_SCHEMA_V1, CanonicalDomain, canonical_unit_id
from .pinned import PinnedContent, PinnedRecording
from .source_time import INT64_MAX, SourceTimestampNs, SourceTimeUnit, promote_to_ns
from .sources import (
    SHA256_CHECKSUM_PATTERN,
    UNIT_KEY_MAX_LENGTH,
    ContentSourceRevision,
    ContentUnitSource,
    RecordingSegmentSource,
    RecordingSourceRevision,
    SourceRevision,
)

__all__ = [
    "EXECUTION_SCOPED_CONFIG_KEYS",
    "INT64_MAX",
    "PRODUCER_FINGERPRINT_SCHEMA_V1",
    "SHA256_CHECKSUM_PATTERN",
    "UNIT_ID_SCHEMA_V1",
    "UNIT_KEY_MAX_LENGTH",
    "CanonicalDomain",
    "ContentSourceRevision",
    "ContentUnitSource",
    "PinnedContent",
    "PinnedRecording",
    "ProducerFingerprintMismatchError",
    "ProducerInfo",
    "RecordingSegmentSource",
    "RecordingSourceRevision",
    "SourceRevision",
    "SourceTimeUnit",
    "SourceTimestampNs",
    "canonical_unit_id",
    "compute_producer_fingerprint",
    "normalize_build_config",
    "promote_to_ns",
]
