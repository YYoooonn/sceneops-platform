"""Tests for ValidateSceneJobParams and ValidateSceneJobHandler.build_job_params."""

from __future__ import annotations

from sceneops_core.jobs.schemas.params.scene import (
    BuildScenesJobParams,
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


class TestBuildScenesJobParams:
    def test_max_source_sequences(self) -> None:
        params = BuildScenesJobParams(max_source_sequences=5)
        assert params.max_source_sequences == 5

    def test_max_built_scenes(self) -> None:
        params = BuildScenesJobParams(max_built_scenes=10)
        assert params.max_built_scenes == 10

    def test_no_max_scenes_field(self) -> None:
        """max_scenes must not exist on BuildScenesJobParams."""
        assert "max_scenes" not in BuildScenesJobParams.model_fields

    def test_camel_case_max_source_sequences(self) -> None:
        params = BuildScenesJobParams.model_validate({"maxSourceSequences": 3})
        assert params.max_source_sequences == 3

    def test_camel_case_max_built_scenes(self) -> None:
        params = BuildScenesJobParams.model_validate({"maxBuiltScenes": 7})
        assert params.max_built_scenes == 7

    def test_defaults_are_none(self) -> None:
        params = BuildScenesJobParams()
        assert params.max_source_sequences is None
        assert params.max_built_scenes is None


# ── ValidateSceneJobHandler.build_job_params: dataset channel injection ────────


def _make_validate_inputs(
    *,
    required_channels: list[str] | None = None,
    params: dict | None = None,
    refs: dict | None = None,
) -> PipelineTaskInputs:
    return PipelineTaskInputs(
        pipeline=PipelineInputRef(
            pipeline_run_id="pr-001",
            pipeline_type="raw_log_scene_building",
            task_id="validate_scene",
            pipeline_task_id="validate_scene",
            pipeline_task_run_id="ptr-002",
        ),
        dataset=DatasetInputRef(
            dataset_id="ds-001",
            dataset_version="v1",
            required_channels=required_channels or [],
        ),
        params=params or {},
        refs=refs or {},
    )


class TestValidateSceneJobHandlerBuildParams:
    def _handler(self) -> ValidateSceneJobHandler:
        return ValidateSceneJobHandler()

    def test_dataset_required_channels_injected_as_require_target_channels(
        self,
    ) -> None:
        inputs = _make_validate_inputs(required_channels=["CAM_FRONT", "LIDAR_TOP"])
        result = self._handler().build_job_params(inputs)
        assert result["require_target_channels"] == ["CAM_FRONT", "LIDAR_TOP"]

    def test_explicit_require_target_channels_not_overridden(self) -> None:
        inputs = _make_validate_inputs(
            required_channels=["CAM_FRONT", "LIDAR_TOP"],
            params={"require_target_channels": ["CAM_BACK"]},
        )
        result = self._handler().build_job_params(inputs)
        assert result["require_target_channels"] == ["CAM_BACK"]

    def test_no_injection_when_dataset_has_no_required_channels(self) -> None:
        inputs = _make_validate_inputs(required_channels=[])
        result = self._handler().build_job_params(inputs)
        assert not result.get("require_target_channels")

    def test_scene_ids_from_refs(self) -> None:
        inputs = _make_validate_inputs(
            required_channels=["CAM_FRONT"],
            refs={"scene_ids": ["scene-a", "scene-b"]},
        )
        result = self._handler().build_job_params(inputs)
        assert result["scene_ids"] == ["scene-a", "scene-b"]
