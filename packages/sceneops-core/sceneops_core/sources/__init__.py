"""Dataset Integration output contracts: source preservation records
(ADR-015 §5).

SourceContent preserves the original bytes of an external dataset;
SourceSnapshot preserves one upstream declaration of where they came from.
Neither interprets anything, and neither knows about RobotRuns, canonical
units or DatasetVersions: canonicalization reads registered content through
``sceneops_core.provenance`` and never the other way round.
"""

from .content import (
    CONTENT_ID_PATTERN,
    CONTENT_ID_SCHEMA_V1,
    SOURCE_CONTENT_INVENTORY_SCHEMA_V1,
    SourceContentError,
    SourceContentInventory,
    SourceFileEntry,
    content_checksum_for,
    content_id_for,
    load_canonical_source_content_inventory,
    validate_content_id,
    validate_source_path,
)
from .selection import (
    SourceSelectionError,
    include_pattern_matches,
    select_paths,
    validate_include_pattern,
)
from .snapshot import (
    SNAPSHOT_ID_SCHEMA_V1,
    SOURCE_SNAPSHOT_DESCRIPTOR_SCHEMA_V1,
    SnapshotDeclaration,
    SnapshotSelection,
    SourceSnapshotDescriptor,
    SourceSnapshotError,
    load_canonical_source_snapshot_descriptor,
    snapshot_id_for,
)

__all__ = [
    "CONTENT_ID_PATTERN",
    "CONTENT_ID_SCHEMA_V1",
    "SNAPSHOT_ID_SCHEMA_V1",
    "SOURCE_CONTENT_INVENTORY_SCHEMA_V1",
    "SOURCE_SNAPSHOT_DESCRIPTOR_SCHEMA_V1",
    "SnapshotDeclaration",
    "SnapshotSelection",
    "SourceContentError",
    "SourceContentInventory",
    "SourceFileEntry",
    "SourceSelectionError",
    "SourceSnapshotDescriptor",
    "SourceSnapshotError",
    "content_checksum_for",
    "content_id_for",
    "include_pattern_matches",
    "load_canonical_source_content_inventory",
    "load_canonical_source_snapshot_descriptor",
    "select_paths",
    "snapshot_id_for",
    "validate_content_id",
    "validate_include_pattern",
    "validate_source_path",
]
