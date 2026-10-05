from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict

from .base import BaseJobResult


class BuildRecordingScenesJobResult(BaseJobResult):
    """The complete Scene set built from one RobotRun recording.

    ``manifest_artifact_ids`` are the SCENE_MANIFEST ArtifactRecords of
    every Scene of the recording scope, in unit-key order, for
    REGISTER_SCENES. ``payload_artifact_count`` counts the distinct
    OBSERVATION_PAYLOAD artifacts they reference; ``created_payload_count``
    those this execution registered (the rest already existed with
    identical bytes)."""

    dataset_id: str
    dataset_version: str
    robot_run_id: str
    recording_checksum: str
    producer_fingerprint: str

    unit_keys: list[str] = Field(default_factory=list)
    manifest_artifact_ids: list[str] = Field(default_factory=list)

    scene_count: int = 0
    observation_count: int = 0
    pose_count: int = 0
    payload_artifact_count: int = 0
    created_payload_count: int = 0
    channels: list[str] = Field(default_factory=list)

    metadata: JsonDict = Field(default_factory=dict)


class ValidateSceneJobResult(BaseJobResult):
    status: str = "ready"
    should_block_pipeline: bool = False

    checked_scene_count: int = 0
    issue_count: int = 0

    validation_run_id: str | None = None
    report_uri: str | None = None

    metadata: JsonDict = Field(default_factory=dict)


class ProfileSceneJobResult(BaseJobResult):
    scene_count: int = 0
    keyframe_count: int = 0
    observation_count: int = 0

    observed_channels: list[str] = Field(default_factory=list)

    profile_run_id: str | None = None
    report_uri: str | None = None

    metadata: JsonDict = Field(default_factory=dict)


class RegisterScenesJobResult(BaseJobResult):
    """``scene_ids`` / ``manifest_artifact_ids`` are the canonical members
    of the registered recording scope after commit, each at its current
    revision (when the fingerprint is unchanged, the already-registered
    set). ``removed_scene_ids`` are records of a replaced scope that the new
    set no longer contains."""

    dataset_id: str
    dataset_version: str

    scene_ids: list[str] = Field(default_factory=list)
    manifest_artifact_ids: list[str] = Field(default_factory=list)

    created_scene_ids: list[str] = Field(default_factory=list)
    replaced_scene_ids: list[str] = Field(default_factory=list)
    unchanged_scene_ids: list[str] = Field(default_factory=list)
    removed_scene_ids: list[str] = Field(default_factory=list)

    registered_scene_count: int = 0

    metadata: JsonDict = Field(default_factory=dict)
