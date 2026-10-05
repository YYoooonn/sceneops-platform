"""Tests for ValidateSceneJobParams and ValidateSceneJobHandler.build_job_params."""

from __future__ import annotations

from sceneops_core.jobs.schemas.params.scene import (
    SceneKeyframeValidationConfig,
    ValidateSceneJobParams,
)
from sceneops_core.pipelines.schemas import (
    DatasetInputRef,
    PipelineInputRef,
    PipelineTaskInputs,
)
from sceneops_worker.jobs.dataset.validate_scene import ValidateSceneJobHandler


class TestSceneKeyframeValidationConfig:
    def test_defaults(self) -> None:
        cfg = SceneKeyframeValidationConfig()
        assert cfg.validate_keyframes is True
        assert cfg.block_on_keyframe_missing_channels is False

    def test_camel_case_aliases(self) -> None:
        cfg = SceneKeyframeValidationConfig.model_validate(
            {"validateKeyframes": False, "blockOnKeyframeMissingChannels": True}
        )
        assert cfg.validate_keyframes is False
        assert cfg.block_on_keyframe_missing_channels is True


class TestValidateSceneJobParams:
    def test_defaults(self) -> None:
        params = ValidateSceneJobParams()
        assert params.scene_ids == []
        assert params.keyframe_validation.validate_keyframes is True

    def test_scenes_are_named_by_id_not_manifest_uri(self) -> None:
        for removed in (
            "scene_manifest_uri",
            "scene_manifest_uris",
            "sample_validation",
        ):
            assert removed not in ValidateSceneJobParams.model_fields

    def test_require_target_channels(self) -> None:
        params = ValidateSceneJobParams(
            require_target_channels=["CAM_FRONT", "LIDAR_TOP"]
        )
        assert params.require_target_channels == ["CAM_FRONT", "LIDAR_TOP"]


# ── ValidateSceneJobHandler.build_job_params ─────────────────────────────────


def _make_validate_inputs(
    *,
    params: dict | None = None,
    refs: dict | None = None,
) -> PipelineTaskInputs:
    return PipelineTaskInputs(
        pipeline=PipelineInputRef(
            pipeline_run_id="pr-001",
            pipeline_type="recording_scene_building",
            task_id="validate_scene",
            pipeline_task_id="validate_scene",
            pipeline_task_run_id="ptr-002",
        ),
        dataset=DatasetInputRef(dataset_id="ds-001", dataset_version="v1"),
        params=params or {},
        refs=refs or {},
    )


class TestValidateSceneJobHandlerBuildParams:
    def _handler(self) -> ValidateSceneJobHandler:
        return ValidateSceneJobHandler()

    def test_the_channel_requirement_is_only_the_explicit_param(self) -> None:
        inputs = _make_validate_inputs(
            params={"require_target_channels": ["CAM_BACK"]},
        )
        result = self._handler().build_job_params(inputs)
        assert result["require_target_channels"] == ["CAM_BACK"]

    def test_no_channel_requirement_is_defaulted(self) -> None:
        result = self._handler().build_job_params(_make_validate_inputs())
        assert not result.get("require_target_channels")

    def test_scene_ids_from_refs(self) -> None:
        inputs = _make_validate_inputs(refs={"scene_ids": ["scene-a", "scene-b"]})
        result = self._handler().build_job_params(inputs)
        assert result["scene_ids"] == ["scene-a", "scene-b"]
