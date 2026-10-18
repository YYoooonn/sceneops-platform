"""Tests for ValidateSceneJobParams and ValidateSceneJobHandler.build_job_params."""

from __future__ import annotations

from sceneops_core.jobs.schemas.params.scene import (
    SceneKeyframeValidationConfig,
    ValidateSceneJobParams,
)
from sceneops_core.pipelines.schemas import PipelineInputRef, PipelineTaskInputs
from sceneops_core.provenance import RevisionPin, manifest_artifact_id_for, unit_id_for
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


_UNIT = unit_id_for(
    domain="scene", source_kind="recording", source_id="run-1", unit_key="p/0"
)
_CHECKSUM = "sha256:" + "a" * 64
PIN = RevisionPin(
    domain="scene",
    unit_id=_UNIT,
    manifest_checksum=_CHECKSUM,
    manifest_artifact_id=manifest_artifact_id_for(
        unit_id=_UNIT, manifest_checksum=_CHECKSUM
    ),
)


class TestValidateSceneJobParams:
    def test_defaults(self) -> None:
        params = ValidateSceneJobParams(revisions=[PIN])
        assert params.revisions == [PIN]
        assert params.require_target_channels == []
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
            revisions=[PIN], require_target_channels=["CAM_FRONT", "LIDAR_TOP"]
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

    def test_the_revisions_are_the_register_stage_pins(self) -> None:
        pins = [PIN.model_dump(mode="json")]
        inputs = _make_validate_inputs(refs={"revisions": pins, "scene_ids": [_UNIT]})
        result = self._handler().build_job_params(inputs)
        assert result["revisions"] == pins
        assert not {"scene_ids", "dataset_id", "dataset_version"} & result.keys()
        assert ValidateSceneJobParams.model_validate(result).revisions == [PIN]
