"""Tests for ValidateSceneJobParams and ValidateSceneJobHandler.build_job_params."""

from __future__ import annotations

from sceneops_core.jobs.schemas.params.scene import (
    BuildScenesJobParams,
    SceneSampleValidationConfig,
    ValidateSceneJobParams,
)
from sceneops_core.pipelines.schemas import (
    DatasetInputRef,
    PipelineInputRef,
    PipelineTaskInputs,
)
from sceneops_worker.jobs.dataset.validate_scene import ValidateSceneJobHandler


class TestSceneSampleValidationConfig:
    def test_defaults(self) -> None:
        cfg = SceneSampleValidationConfig()
        # validate_samples=True by default (validation is on); blocking=False by default
        assert cfg.validate_samples is True
        assert cfg.block_on_sample_missing_channels is False

    def test_explicit_values(self) -> None:
        cfg = SceneSampleValidationConfig(
            validate_samples=True,
            block_on_sample_missing_channels=True,
        )
        assert cfg.validate_samples is True
        assert cfg.block_on_sample_missing_channels is True

    def test_camel_case_aliases(self) -> None:
        cfg = SceneSampleValidationConfig.model_validate(
            {
                "validateSamples": True,
                "blockOnSampleMissingChannels": True,
            }
        )
        assert cfg.validate_samples is True
        assert cfg.block_on_sample_missing_channels is True


class TestValidateSceneJobParams:
    def test_defaults(self) -> None:
        params = ValidateSceneJobParams()
        assert params.sample_validation.validate_samples is True
        assert params.sample_validation.block_on_sample_missing_channels is False

    def test_nested_sample_validation_snake_case(self) -> None:
        params = ValidateSceneJobParams.model_validate(
            {
                "require_target_channels": ["CAM_FRONT", "LIDAR_TOP"],
                "sample_validation": {
                    "validate_samples": True,
                    "block_on_sample_missing_channels": False,
                },
            }
        )
        assert params.sample_validation.validate_samples is True
        assert params.sample_validation.block_on_sample_missing_channels is False

    def test_nested_sample_validation_camel_case(self) -> None:
        params = ValidateSceneJobParams.model_validate(
            {
                "requireTargetChannels": ["CAM_FRONT", "LIDAR_TOP"],
                "sampleValidation": {
                    "validateSamples": True,
                    "blockOnSampleMissingChannels": False,
                },
            }
        )
        assert params.sample_validation.validate_samples is True
        assert params.sample_validation.block_on_sample_missing_channels is False

    def test_no_flat_validate_samples_field(self) -> None:
        """Flat validate_samples should not exist on ValidateSceneJobParams."""
        assert not hasattr(ValidateSceneJobParams, "validate_samples") or (
            "validate_samples" not in ValidateSceneJobParams.model_fields
        )

    def test_no_flat_block_on_sample_missing_channels_field(self) -> None:
        """Flat block_on_sample_missing_channels should not exist on ValidateSceneJobParams."""
        assert not hasattr(
            ValidateSceneJobParams, "block_on_sample_missing_channels"
        ) or (
            "block_on_sample_missing_channels"
            not in ValidateSceneJobParams.model_fields
        )

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

    def test_scene_manifest_uris_from_refs(self) -> None:
        uris = ["s3://bucket/sc-001/manifest.json"]
        inputs = _make_validate_inputs(
            required_channels=["CAM_FRONT"],
            refs={"scene_manifest_uris": uris},
        )
        result = self._handler().build_job_params(inputs)
        assert result["scene_manifest_uris"] == uris
