from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel

from .sharding import LEARNING_DATA_LAYOUT_VERSION_SINGLE_FILE, LearningDataShardIndex

# Distinct from ALIGNED_EPISODE_ARTIFACT_SCHEMA_VERSION, ALIGNMENT_SEMANTICS_VERSION,
# ALIGNED_EPISODE_VALIDATION_SEMANTICS_VERSION, and ALIGNED_EPISODE_PROFILE_SEMANTICS_VERSION
# -- this is the *columnar table layout's own* version (SceneOps V2 Request
# 2.5 §6). Bump it when learning_episodes/learning_steps/learning_signals'
# column set or semantics changes, independent of any upstream version.
LEARNING_DATA_SCHEMA_VERSION = "v1"


class AlignedArtifactRevision(SceneOpsBaseModel):
    """One pinned input to a learning-data export (SceneOps V2 Request 2.5
    §3). Always explicit -- an export never infers "latest alignment per
    Episode"; one Episode may have multiple aligned revisions/configs, and
    the caller decides exactly which ones go into a given export."""

    episode_id: str
    aligned_artifact_id: str
    aligned_artifact_checksum: str


class LearningDataExportConfig(SceneOpsBaseModel):
    """The only export-time knob in v1: which tables to build. No
    transformation choices exist at this layer -- the columnar tables are a
    direct, lossless flattening of already-frozen AlignedEpisodeArtifacts,
    never a re-alignment (Request 2.5 §1)."""

    tables: list[str] | None = None


class LearningDataExportManifest(SceneOpsBaseModel):
    """Index for one complete columnar snapshot (SceneOps V2 Request 2.5
    §6). Multiple snapshots for the same DatasetVersion coexist -- each
    export gets its own export_id-scoped path, never overwriting another
    export's tables the way EXPORT_ANALYTICS_SNAPSHOT's Scene tables do."""

    schema_version: str = LEARNING_DATA_SCHEMA_VERSION
    export_id: str

    dataset_id: str
    dataset_version: str

    # Sorted by aligned_artifact_checksum -- see learning_data_export_id()
    # for why order must never affect identity.
    inputs: list[AlignedArtifactRevision] = Field(default_factory=list)
    export_config: LearningDataExportConfig = Field(
        default_factory=LearningDataExportConfig
    )

    # learning_episodes is always a single file, referenced here regardless
    # of layout_version. learning_steps/learning_signals are single-file
    # (keyed here) under LEARNING_DATA_LAYOUT_VERSION_SINGLE_FILE, or
    # sharded (see shard_index, table_uris/table_checksums omit them)
    # under LEARNING_DATA_LAYOUT_VERSION_SHARDED (SceneOps V2 Request 5.2).
    table_uris: dict[str, str] = Field(default_factory=dict)
    table_checksums: dict[str, str] = Field(default_factory=dict)
    # Total row count per table regardless of layout -- for a sharded
    # table this is the sum across every shard, a summary figure only
    # (never itself a source of physical-layout truth; see shard_index).
    row_counts: dict[str, int] = Field(default_factory=dict)

    # Physical-layout version tag (SceneOps V2 Request 5.2) -- informational
    # only; readers must dispatch on shard_index's presence, never on this
    # string (see sharding.py's module docstring).
    layout_version: str = LEARNING_DATA_LAYOUT_VERSION_SINGLE_FILE
    shard_index: LearningDataShardIndex | None = None

    episode_count: int = 0
    metadata: JsonDict = Field(default_factory=dict)
