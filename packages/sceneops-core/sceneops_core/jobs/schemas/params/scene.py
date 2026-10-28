from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel
from sceneops_core.scenes.nuscenes_build import NuScenesSceneBuildConfig
from sceneops_core.scenes.recording_build import RecordingSceneBuildConfig

from .base import (
    RecordingBuildJobParams,
    RevisionAssessmentJobParams,
    RevisionRegistrationJobParams,
    SnapshotBuildJobParams,
)


class SceneKeyframeValidationConfig(SceneOpsBaseModel):
    """Validation options applied to each source-defined keyframe group."""

    validate_keyframes: bool = True
    block_on_keyframe_missing_channels: bool = False


class BuildRecordingScenesJobParams(RecordingBuildJobParams):
    """One registered RobotRun recording -> the complete canonical Scene set
    of its recording scope (ADR-007 §17.3, §29.10; ADR-015 §6.7).

    The recording is identified only by ``robot_run_id`` and read through
    the verified recording resolver; ``build_config`` is the producer's
    whole semantic configuration. No DatasetVersion is involved.
    """

    robot_run_id: str = Field(min_length=1)
    build_config: RecordingSceneBuildConfig

    metadata: JsonDict = Field(default_factory=dict)


class BuildSnapshotScenesJobParams(SnapshotBuildJobParams):
    """One registered SourceSnapshot -> the canonical Scenes of its content
    (ADR-015 §6.7; M3.0 D2).

    ``snapshot_id`` names the declaration an operator selects; the build
    resolves it to its content once, records both in its result as execution
    provenance, and builds from the content alone, so every snapshot of the
    same bytes yields the same manifests. ``build_config`` is the producer's
    whole semantic configuration. ``unit_keys`` limits the build to those
    native scenes (default: every scene of the content); it is execution
    scope, never configuration, so a subset yields the same revisions. No
    DatasetVersion is involved.
    """

    build_config: NuScenesSceneBuildConfig

    metadata: JsonDict = Field(default_factory=dict)


class ValidateSceneJobParams(RevisionAssessmentJobParams):
    """Validate pinned Scene revisions; each per-scene run record pins the
    revision it assessed."""

    domain = "scene"
    _unit_id_fields = ("scene_ids", "sceneIds")

    require_target_channels: list[str] = Field(default_factory=list)

    keyframe_validation: SceneKeyframeValidationConfig = Field(
        default_factory=SceneKeyframeValidationConfig
    )

    metadata: JsonDict = Field(default_factory=dict)


class ProfileSceneJobParams(RevisionAssessmentJobParams):
    """Profile pinned Scene revisions; see ``ValidateSceneJobParams``."""

    domain = "scene"
    _unit_id_fields = ("scene_ids", "sceneIds")

    metadata: JsonDict = Field(default_factory=dict)


class RegisterScenesJobParams(RevisionRegistrationJobParams):
    """Canonical Scene revision registration (ADR-015 §6.4):
    ``manifest_artifact_ids`` name SCENE_MANIFEST ArtifactRecords; the
    registrar re-reads and verifies their bytes and the objects they
    reference."""
