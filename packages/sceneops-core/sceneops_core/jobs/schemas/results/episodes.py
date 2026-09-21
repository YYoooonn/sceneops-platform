from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict

from .base import BaseJobResult


class BuildEpisodesJobResult(BaseJobResult):
    raw_log_id: str | None = None

    episode_ids: list[str] = Field(default_factory=list)
    episode_manifest_uris: list[str] = Field(default_factory=list)

    episode_count: int = 0
    observation_frame_count: int = 0
    action_frame_count: int = 0

    segmentation_strategy: str | None = None

    channels: list[str] = Field(default_factory=list)

    metadata: JsonDict = Field(default_factory=dict)


class RegisterEpisodeJobResult(BaseJobResult):
    episode_ids: list[str] = Field(default_factory=list)
    episode_manifest_uris: list[str] = Field(default_factory=list)
    registered_episode_count: int = 0

    registered: bool = True

    metadata: JsonDict = Field(default_factory=dict)


class ValidateEpisodeJobResult(BaseJobResult):
    status: str = "ready"
    should_block_pipeline: bool = False

    checked_episode_count: int = 0
    issue_count: int = 0

    validation_run_id: str | None = None
    report_uri: str | None = None

    metadata: JsonDict = Field(default_factory=dict)


class ProfileEpisodeJobResult(BaseJobResult):
    checked_episode_count: int = 0
    frame_count: int = 0
    observation_count: int = 0
    action_count: int = 0

    observed_observation_channels: list[str] = Field(default_factory=list)
    observed_action_channels: list[str] = Field(default_factory=list)

    profile_run_id: str | None = None
    report_uri: str | None = None

    metadata: JsonDict = Field(default_factory=dict)


class AlignEpisodeJobResult(BaseJobResult):
    """Summary/reference metadata only -- the full AlignedEpisode (with its
    per-step signal list) is not returned inline, matching how other jobs
    return references rather than full artifact bodies (SceneOps V2 Request
    2.3 §30)."""

    episode_id: str

    aligned_artifact_id: str | None = None
    aligned_artifact_uri: str | None = None
    # Added alongside Request 2.4 so a caller dispatching
    # VALIDATE_ALIGNED_EPISODE/PROFILE_ALIGNED_EPISODE next can pin this
    # aligned artifact's checksum without a separate lookup.
    aligned_artifact_checksum: str | None = None

    source_artifact_id: str | None = None
    source_manifest_sha256: str | None = None
    # False for a legacy EPISODE_MANIFEST ArtifactRecord with no populated
    # checksum -- content-verified-at-read-time but producer-revision-
    # unverified (SceneOps V2 Request 2.3 §9).
    source_checksum_verified: bool = False

    alignment_semantics_version: str | None = None
    alignment_config_hash: str | None = None

    step_count: int = 0
    target_frequency_hz: float | None = None
    achieved_frequency_hz: float | None = None
    duplicate_discarded_count: int = 0

    metadata: JsonDict = Field(default_factory=dict)


class ValidateAlignedEpisodeJobResult(BaseJobResult):
    """Summary only -- the full AlignedEpisodeValidationReport (with its
    per-issue list) is persisted as an ArtifactRecord, not returned inline
    (SceneOps V2 Request 2.4, mirroring AlignEpisodeJobResult's shape)."""

    episode_id: str
    aligned_artifact_id: str
    aligned_artifact_checksum: str | None = None

    validation_semantics_version: str | None = None
    valid: bool = False
    issue_count: int = 0

    report_artifact_id: str | None = None
    report_uri: str | None = None

    metadata: JsonDict = Field(default_factory=dict)


class ExportLearningDataJobResult(BaseJobResult):
    """Summary/reference metadata only -- the full columnar tables are
    Parquet artifacts, not returned inline (SceneOps V2 Request 2.5)."""

    dataset_id: str
    dataset_version: str

    export_id: str
    episode_count: int = 0

    table_uris: dict[str, str] = Field(default_factory=dict)
    row_counts: dict[str, int] = Field(default_factory=dict)

    manifest_artifact_id: str | None = None
    manifest_uri: str | None = None

    metadata: JsonDict = Field(default_factory=dict)


class ProfileAlignedEpisodeJobResult(BaseJobResult):
    """Summary only -- the full AlignedEpisodeProfile (with per-channel
    coverage) is persisted as an ArtifactRecord, not returned inline."""

    episode_id: str
    aligned_artifact_id: str
    aligned_artifact_checksum: str | None = None

    profile_semantics_version: str | None = None
    step_count: int = 0
    observation_channel_count: int = 0
    action_channel_count: int = 0
    overall_missing_ratio: float | None = None
    max_channel_missing_ratio: float | None = None

    report_artifact_id: str | None = None
    report_uri: str | None = None

    metadata: JsonDict = Field(default_factory=dict)


class CurateEpisodesJobResult(BaseJobResult):
    """Summary/reference metadata only -- the full EpisodeCurationManifest
    (with its per-candidate decisions/reasons) is persisted as an
    ArtifactRecord, not returned inline (SceneOps V2 Request 2.6, mirroring
    ExportLearningDataJobResult's shape)."""

    dataset_id: str
    dataset_version: str

    curation_id: str

    candidate_count: int = 0
    selected_count: int = 0
    rejected_count: int = 0

    manifest_artifact_id: str | None = None
    manifest_uri: str | None = None

    metadata: JsonDict = Field(default_factory=dict)
