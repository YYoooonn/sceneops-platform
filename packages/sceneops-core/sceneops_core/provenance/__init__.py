"""Domain-neutral source and producer provenance primitives (ADR-007 §27).

Platform primitives shared by the Scene and Episode domains, which compose
them into their own lineage types. Nothing here is a Scene/Episode model.
"""

from .producer import (
    EXECUTION_SCOPED_CONFIG_KEYS,
    PRODUCER_FINGERPRINT_SCHEMA_V1,
    ProducerFingerprintMismatchError,
    ProducerInfo,
    compute_producer_fingerprint,
    normalize_build_config,
)
from .source_time import INT64_MAX, SourceTimestampNs, SourceTimeUnit, promote_to_ns
from .sources import (
    SHA256_CHECKSUM_PATTERN,
    UNIT_KEY_MAX_LENGTH,
    ExternalSourceRevision,
    ExternalUnitSource,
    RecordingSegmentSource,
    RecordingSourceRevision,
    SourceRevision,
    UnitSource,
)

__all__ = [
    "EXECUTION_SCOPED_CONFIG_KEYS",
    "INT64_MAX",
    "PRODUCER_FINGERPRINT_SCHEMA_V1",
    "SHA256_CHECKSUM_PATTERN",
    "UNIT_KEY_MAX_LENGTH",
    "ExternalSourceRevision",
    "ExternalUnitSource",
    "ProducerFingerprintMismatchError",
    "ProducerInfo",
    "RecordingSegmentSource",
    "RecordingSourceRevision",
    "SourceRevision",
    "SourceTimeUnit",
    "SourceTimestampNs",
    "UnitSource",
    "compute_producer_fingerprint",
    "normalize_build_config",
    "promote_to_ns",
]
