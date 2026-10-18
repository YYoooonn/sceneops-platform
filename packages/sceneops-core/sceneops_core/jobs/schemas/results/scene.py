from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict
from sceneops_core.provenance import RevisionPin

from .base import BaseJobResult


class BuildRecordingScenesJobResult(BaseJobResult):
    """The complete Scene set built from one RobotRun recording.

    ``manifest_artifact_ids`` are the SCENE_MANIFEST ArtifactRecords of
    every Scene of the recording scope, in unit-key order, for
    REGISTER_SCENES. Each id is ``manifest_artifact_id_for(unit_id,
    manifest_checksum)``: it names one write-once manifest object and its
    exact bytes, and the registrar re-derives it from the bytes it reads. ``payload_artifact_count`` counts the distinct
    OBSERVATION_PAYLOAD artifacts they reference; ``created_payload_count``
    those this execution registered (the rest already existed with
    identical bytes)."""

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
    """The Scene revisions of one build, each as its complete pin, in unit
    id order; ``scene_ids`` are their units in the same order. Every
    revision is registered after commit: the ``new_unit`` / ``new_revision``
    lists name the units whose revision this job inserted, and
    ``existing_revision`` those whose revision was already registered (a
    retry, or a rebuild of identical bytes)."""

    robot_run_id: str
    producer_fingerprint: str

    revisions: list[RevisionPin] = Field(default_factory=list)
    scene_ids: list[str] = Field(default_factory=list)

    new_unit_scene_ids: list[str] = Field(default_factory=list)
    new_revision_scene_ids: list[str] = Field(default_factory=list)
    existing_revision_scene_ids: list[str] = Field(default_factory=list)

    revision_count: int = 0

    metadata: JsonDict = Field(default_factory=dict)
