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
