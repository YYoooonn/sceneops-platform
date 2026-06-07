"""Tests for ValidateSceneJobParams nested sample_validation config."""

from __future__ import annotations

from sceneops_core.jobs.schemas.params.scene import (
    BuildScenesJobParams,
    SceneSampleValidationConfig,
    ValidateSceneJobParams,
)


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
