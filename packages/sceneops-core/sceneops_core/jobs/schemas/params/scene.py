from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel
from sceneops_core.scenes.recording_build import RecordingSceneBuildConfig

from .base import BaseJobParams, RecordingConsumerJobParams


class SceneKeyframeValidationConfig(SceneOpsBaseModel):
    """Validation options applied to each source-defined keyframe group."""

    validate_keyframes: bool = True
    block_on_keyframe_missing_channels: bool = False


class BuildRecordingScenesJobParams(RecordingConsumerJobParams):
    """One registered RobotRun recording -> the complete canonical Scene set
    of its recording scope (ADR-007 §17.3, §29.10).

    The recording is identified only by ``robot_run_id`` and read through
    the verified recording resolver; ``build_config`` is the producer's
    whole semantic configuration. The DatasetVersion only scopes where
    manifests are published and does not affect their bytes.
    """

    dataset_id: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)

    robot_run_id: str = Field(min_length=1)
    build_config: RecordingSceneBuildConfig

    metadata: JsonDict = Field(default_factory=dict)


class ValidateSceneJobParams(BaseJobParams):
    """Validate registered Scenes. Each Scene is assessed at the manifest
    revision its record points to when the job reads it, and the per-scene
    run record pins that revision."""

    dataset_id: str | None = None
    dataset_version: str | None = None

    scene_ids: list[str] = Field(default_factory=list)

    require_target_channels: list[str] = Field(default_factory=list)

    keyframe_validation: SceneKeyframeValidationConfig = Field(
        default_factory=SceneKeyframeValidationConfig
    )

    metadata: JsonDict = Field(default_factory=dict)


class ProfileSceneJobParams(BaseJobParams):
    """Profile registered Scenes at their current revision; see
    ``ValidateSceneJobParams``."""

    dataset_id: str | None = None
    dataset_version: str | None = None

    scene_ids: list[str] = Field(default_factory=list)

    metadata: JsonDict = Field(default_factory=dict)


class RegisterScenesJobParams(BaseJobParams):
    """Canonical Scene registration (ADR-007 §17, §18).

    ``manifest_artifact_ids`` name SCENE_MANIFEST ArtifactRecords; the
    registrar re-reads and verifies their bytes. A recording-derived input
    must be the complete unit set of one recording scope.
    """

    dataset_id: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)

    manifest_artifact_ids: list[str] = Field(min_length=1)

    replace: bool = False

    metadata: JsonDict = Field(default_factory=dict)
