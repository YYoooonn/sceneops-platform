from __future__ import annotations

from pydantic import Field, model_validator

from sceneops_core.common.schemas import JsonDict
from sceneops_core.episodes.alignment import (
    TemporalAlignmentConfig,
    TemporalSourceContext,
)
from sceneops_core.episodes.curation import CurationPolicy
from sceneops_core.episodes.recording_build import RecordingEpisodeBuildConfig

from .base import BaseJobParams, RecordingConsumerJobParams


class BuildRecordingEpisodesJobParams(RecordingConsumerJobParams):
    """One registered RobotRun recording -> the complete canonical Episode
    set of its recording scope (ADR-007 §17.4, §29.10, §31).

    The recording is identified only by ``robot_run_id`` and read through
    the verified recording resolver; ``build_config`` is the producer's
    whole semantic configuration. The DatasetVersion only scopes where
    manifests are published and does not affect their bytes.
    """

    dataset_id: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)

    robot_run_id: str = Field(min_length=1)
    build_config: RecordingEpisodeBuildConfig

    metadata: JsonDict = Field(default_factory=dict)


class RegisterEpisodesJobParams(BaseJobParams):
    """Canonical Episode registration (ADR-007 §17, §18).

    ``manifest_artifact_ids`` name EPISODE_MANIFEST ArtifactRecords; the
    registrar re-reads and verifies their bytes. The input must be the
    complete unit set of one recording scope.
    """

    dataset_id: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)

    manifest_artifact_ids: list[str] = Field(min_length=1)

    replace: bool = False

    metadata: JsonDict = Field(default_factory=dict)


class ValidateEpisodeJobParams(BaseJobParams):
    """Validate registered Episodes. Each Episode is assessed at the manifest
    revision its record points to when the job reads it, and the per-episode
    run record pins that revision."""

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

    The source revision is the EPISODE_MANIFEST the EpisodeRecord points to
    (``manifest_artifact_id``, ADR-007 §14.4) unless source_artifact_id/
    source_manifest_sha256 are both pinned explicitly. ``source_context``
    names the clock to align on; unset, it is the Episode's window clock.

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

    ``base_export_id`` (SceneOps V2 Request 5.5 §1/§6) makes this an
    *incremental* export: ``inputs`` then represents only the delta being
    added on top of ``base_export_id``'s own inputs, not the full merged
    set -- the handler resolves the base manifest, merges
    ``base.inputs + inputs``, and reuses every one of the base export's
    physical shards unchanged. ``None`` (the default) is an ordinary full
    export, unchanged from Request 2.5 -- ``inputs`` then must be the
    complete target set, exactly as before.
    """

    dataset_id: str
    dataset_version: str

    inputs: list[LearningDataExportInputParams] = Field(default_factory=list)

    # None -> build all learning tables (learning_episodes, learning_steps,
    # learning_signals), mirroring ExportAnalyticsSnapshotJobParams.tables.
    tables: list[str] | None = None

    # None -> ordinary full export. Set -> incremental export deriving from
    # that already-published export_id (SceneOps V2 Request 5.5 §1).
    base_export_id: str | None = None

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
