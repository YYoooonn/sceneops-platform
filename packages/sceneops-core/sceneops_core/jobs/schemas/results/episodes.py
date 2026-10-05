from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel

from .base import BaseJobResult


class BuildRecordingEpisodesJobResult(BaseJobResult):
    """The complete Episode set built from one RobotRun recording.

    ``manifest_artifact_ids`` are the EPISODE_MANIFEST ArtifactRecords of
    every Episode of the recording scope, in unit-key order, for
    REGISTER_EPISODES. ``payload_artifact_count`` counts the distinct
    OBSERVATION_PAYLOAD artifacts they reference; ``created_payload_count``
    those this execution registered (the rest already existed with
    identical bytes, e.g. extracted by a Scene build of the same RobotRun)."""

    dataset_id: str
    dataset_version: str
    robot_run_id: str
    recording_checksum: str
    producer_fingerprint: str

    unit_keys: list[str] = Field(default_factory=list)
    manifest_artifact_ids: list[str] = Field(default_factory=list)

    episode_count: int = 0
    observation_count: int = 0
    state_count: int = 0
    action_count: int = 0
    event_count: int = 0
    payload_artifact_count: int = 0
    created_payload_count: int = 0
    topics: list[str] = Field(default_factory=list)

    metadata: JsonDict = Field(default_factory=dict)


class RegisterEpisodesJobResult(BaseJobResult):
    """``episode_ids`` / ``manifest_artifact_ids`` are the canonical members
    of the registered recording scope after commit, each at its current
    revision. ``removed_episode_ids`` are records of a replaced scope that
    the new set no longer contains."""

    dataset_id: str
    dataset_version: str

    episode_ids: list[str] = Field(default_factory=list)
    manifest_artifact_ids: list[str] = Field(default_factory=list)

    created_episode_ids: list[str] = Field(default_factory=list)
    replaced_episode_ids: list[str] = Field(default_factory=list)
    unchanged_episode_ids: list[str] = Field(default_factory=list)
    removed_episode_ids: list[str] = Field(default_factory=list)

    registered_episode_count: int = 0

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
    observation_count: int = 0
    state_count: int = 0
    action_count: int = 0
    event_count: int = 0

    observation_topics: list[str] = Field(default_factory=list)
    state_topics: list[str] = Field(default_factory=list)
    action_topics: list[str] = Field(default_factory=list)
    event_topics: list[str] = Field(default_factory=list)

    profile_run_id: str | None = None
    report_uri: str | None = None

    metadata: JsonDict = Field(default_factory=dict)


class AlignedEpisodeRef(SceneOpsBaseModel):
    """One aligned revision, with the pin a downstream stage needs.

    ``episode_id`` / ``aligned_artifact_id`` / ``aligned_artifact_checksum``
    are exactly the fields of an EXPORT_LEARNING_DATA input.
    """

    episode_id: str
    aligned_artifact_id: str
    aligned_artifact_uri: str
    aligned_artifact_checksum: str

    source_artifact_id: str
    source_manifest_sha256: str
    source_checksum_verified: bool = False

    step_count: int = 0
    achieved_frequency_hz: float | None = None
    duplicate_discarded_count: int = 0


class AlignEpisodeJobResult(BaseJobResult):
    """Summary/reference metadata only -- the full AlignedEpisode (with its
    per-step signal list) is not returned inline, matching how other jobs
    return references rather than full artifact bodies."""

    aligned: list[AlignedEpisodeRef] = Field(default_factory=list)

    # The export inputs of ``aligned``, in the exact shape
    # EXPORT_LEARNING_DATA consumes (the pipeline hand-off).
    export_inputs: list[JsonDict] = Field(default_factory=list)

    alignment_semantics_version: str | None = None
    alignment_config_hash: str | None = None
    target_frequency_hz: float | None = None

    episode_count: int = 0
    step_count: int = 0

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

    # learning_episodes only -- learning_steps/learning_signals are sharded
    # (SceneOps V2 Request 5.2), so they have no single URI; see
    # shard_counts for their physical file count instead.
    table_uris: dict[str, str] = Field(default_factory=dict)
    row_counts: dict[str, int] = Field(default_factory=dict)
    shard_counts: dict[str, int] = Field(default_factory=dict)

    # None for an ordinary full export. Set to the base's export_id for an
    # incremental export (SceneOps V2 Request 5.5 §1) -- reused_shard_counts/
    # new_shard_counts then partition shard_counts's totals into shards
    # carried over from base_export_id untouched vs. newly written for this
    # export's delta.
    base_export_id: str | None = None
    reused_shard_counts: dict[str, int] = Field(default_factory=dict)
    new_shard_counts: dict[str, int] = Field(default_factory=dict)

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
