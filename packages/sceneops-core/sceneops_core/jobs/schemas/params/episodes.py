from __future__ import annotations

from pydantic import Field, model_validator

from sceneops_core.common.schemas import JsonDict
from sceneops_core.episodes.alignment import (
    TemporalAlignmentConfig,
    TemporalSourceContext,
)
from sceneops_core.episodes.curation import CurationPolicy
from sceneops_core.episodes.schemas import EpisodeSegmentationConfig

from .base import BaseJobParams


class BuildEpisodesJobParams(BaseJobParams):
    """Robot rosbag/MCAP -> episodes.

    Mirrors ``BuildScenesJobParams`` (raw log -> scenes), but the source
    adapter is always ``RosbagAdapter`` (registered under
    ``RawLogSourceType.REAL_ROBOT_LOG`` in this job's own adapter factory,
    separate from ``build_scenes``'s) and segmentation is driven by
    ``EpisodeSegmenter`` (see ``segmentation`` below) rather than a generic
    gap/anchor scene segmenter.
    """

    dataset_id: str
    dataset_version: str

    robot_id: str
    robot_run_id: str | None = None

    # Falls back to the referenced RobotRun's mcap_uri/rosbag_uri when
    # omitted — same convention as IngestRobotStatesJobParams.
    mcap_uri: str | None = None

    raw_log_id: str | None = None

    # Default (mission_boundary, falling back to whole_run when no dated
    # Mission exists) preserves pre-Request-13 EpisodeBuilder behavior
    # exactly — see SceneOps V2 Request 13.
    segmentation: EpisodeSegmentationConfig = Field(
        default_factory=EpisodeSegmentationConfig
    )

    max_built_episodes: int | None = None

    output_episode_root_uri: str | None = None

    metadata: JsonDict = Field(default_factory=dict)


class RegisterEpisodeJobParams(BaseJobParams):
    episode_ids: list[str] = Field(default_factory=list)
    episode_manifest_uris: list[str] = Field(default_factory=list)

    dataset_id: str | None = None
    dataset_version: str | None = None

    replace_existing: bool = False

    metadata: JsonDict = Field(default_factory=dict)


class ValidateEpisodeJobParams(BaseJobParams):
    """EpisodeRecord + EpisodeManifest -> structural usability check.

    Keyed by episode_id (not manifest URI) — unlike build_episodes/
    register_episode, this runs after register_episode, so EpisodeRecord
    already exists and carries its own episode_manifest_uri; there's no need
    to thread manifest URIs through separately (see SceneOps V2 Request 17).
    """

    episode_id: str | None = None
    episode_ids: list[str] = Field(default_factory=list)

    dataset_id: str | None = None
    dataset_version: str | None = None

    metadata: JsonDict = Field(default_factory=dict)


class ProfileEpisodeJobParams(BaseJobParams):
    episode_id: str | None = None
    episode_ids: list[str] = Field(default_factory=list)

    dataset_id: str | None = None
    dataset_version: str | None = None

    metadata: JsonDict = Field(default_factory=dict)


class AlignEpisodeJobParams(BaseJobParams):
    """Episode + explicit TemporalAlignmentConfig -> AlignedEpisodeArtifact
    (SceneOps V2 Request 2.3).

    The source EPISODE_MANIFEST ArtifactRecord is resolved internally at
    execution time (latest by created_at, matching this platform's existing
    "latest wins" convention) unless source_artifact_id/
    source_manifest_sha256 are both pinned explicitly (Request 2.3 §7/§20).

    Pinning is also the only way for a specific source revision to
    participate in this Job's execution-key dedup identity (Request 2.3
    §17): the API computes the execution key from these params at
    Job-creation time, before the worker has read any source bytes, so an
    unpinned dispatch dedups at (episode_id, alignment_config,
    alignment_semantics_version) granularity only -- the same idempotency
    behavior every other job type already has, and force=true is available
    for a caller that specifically needs a fresh execution.
    """

    episode_id: str
    dataset_id: str | None = None
    dataset_version: str | None = None

    alignment_config: TemporalAlignmentConfig
    source_context: TemporalSourceContext | None = None

    source_artifact_id: str | None = None
    source_manifest_sha256: str | None = None

    metadata: JsonDict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_pin_pair(self) -> AlignEpisodeJobParams:
        pinned = (
            self.source_artifact_id is not None,
            self.source_manifest_sha256 is not None,
        )
        if any(pinned) and not all(pinned):
            raise ValueError(
                "source_artifact_id and source_manifest_sha256 must both be "
                "provided to pin a source revision, or both left unset"
            )
        return self


class _PinnedAlignedArtifactJobParams(BaseJobParams):
    """Shared shape for VALIDATE_ALIGNED_EPISODE/PROFILE_ALIGNED_EPISODE
    (SceneOps V2 Request 2.4 §33/§34).

    Unlike ALIGN_EPISODE's source resolution, this is always explicitly
    pinned -- one Episode can legitimately have many ALIGNED_EPISODE_MANIFEST
    artifacts (different source revisions, different configs, different
    semantics versions), so there is no sensible "current aligned artifact"
    to resolve unpinned. aligned_artifact_id is required; its checksum is
    resolved by a simple 1:1 ArtifactRecord.get() lookup (not a "latest"
    selection) before execution-key computation, reusing the Request 2.3A
    lesson without reintroducing its ambiguity.
    """

    episode_id: str
    dataset_id: str | None = None
    dataset_version: str | None = None

    aligned_artifact_id: str
    aligned_artifact_checksum: str | None = None

    metadata: JsonDict = Field(default_factory=dict)


class ValidateAlignedEpisodeJobParams(_PinnedAlignedArtifactJobParams):
    """AlignedEpisodeArtifact -> structural ValidationReport (SceneOps V2
    Request 2.4)."""


class ProfileAlignedEpisodeJobParams(_PinnedAlignedArtifactJobParams):
    """AlignedEpisodeArtifact -> descriptive AlignedEpisodeProfile (SceneOps
    V2 Request 2.4)."""


class LearningDataExportInputParams(BaseJobParams):
    """One pinned export input, as supplied by the caller. Mirrors
    AlignedArtifactRevision's identity fields, but with
    aligned_artifact_checksum optional here -- resolved by the API at
    Job-creation time (SceneOps V2 Request 2.5 §3/§7), same as
    _PinnedAlignedArtifactJobParams. The manifest's own
    AlignedArtifactRevision always has the checksum populated; this is the
    request-side, pre-resolution shape only."""

    episode_id: str
    aligned_artifact_id: str
    aligned_artifact_checksum: str | None = None


class ExportLearningDataJobParams(BaseJobParams):
    """Explicit, revision-pinned AlignedEpisodeArtifacts -> columnar Parquet
    export (SceneOps V2 Request 2.5).

    Never infers "latest alignment per Episode" -- ``inputs`` is always an
    explicit list; one Episode may appear multiple times across different
    exports (or even within the same export, under different aligned
    revisions) if the caller wants that. Each input's checksum is resolved
    at Job-creation time exactly like VALIDATE_ALIGNED_EPISODE/
    PROFILE_ALIGNED_EPISODE (Request 2.4), before execution-key computation.
    """

    dataset_id: str
    dataset_version: str

    inputs: list[LearningDataExportInputParams] = Field(default_factory=list)

    # None -> build all learning tables (learning_episodes, learning_steps,
    # learning_signals), mirroring ExportAnalyticsSnapshotJobParams.tables.
    tables: list[str] | None = None

    metadata: JsonDict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_non_empty_inputs(self) -> ExportLearningDataJobParams:
        if not self.inputs:
            raise ValueError("export_learning_data requires at least one input")
        return self


class CurateEpisodesJobParams(BaseJobParams):
    """Explicit, revision-pinned LearningDataExportManifest -> selected/
    rejected AlignedEpisode revisions (SceneOps V2 Request 2.6).

    Always pinned by learning_data_export_manifest_artifact_id -- there is
    no "latest export" resolution, mirroring
    _PinnedAlignedArtifactJobParams's reasoning (Request 2.4 §33/§34)
    applied one layer up. learning_data_export_manifest_checksum is
    resolved by the API at Job-creation time exactly like
    aligned_artifact_checksum (Request 2.4) / per-item checksums (Request
    2.5), before execution-key computation (Request 2.6 §11).
    """

    dataset_id: str
    dataset_version: str

    learning_data_export_manifest_artifact_id: str
    learning_data_export_manifest_checksum: str | None = None

    policy: CurationPolicy

    metadata: JsonDict = Field(default_factory=dict)
