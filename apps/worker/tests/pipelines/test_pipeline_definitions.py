"""Tests for pipeline definition metadata and task param contracts."""

from __future__ import annotations

import pytest

from sceneops_core.pipelines.builtin import (
    BUILTIN_PIPELINE_DEFINITIONS,
    RAW_LOG_SCENE_BUILDING_PIPELINE,
    SCENARIO_CURATION_PIPELINE,
)
from sceneops_core.pipelines.schemas import PipelineType
from sceneops_core.jobs.schemas import JobType


_SUPPORTED_TYPES = {
    PipelineType.DATASET_SCENE_INGESTION,
    PipelineType.RAW_LOG_SCENE_BUILDING,
    PipelineType.DETECTION_EVALUATION,
}

# Experimental pipelines: supported=True, implemented=True, experimental=True.
# They can be created/run but are hidden from default API listing.
_EXPERIMENTAL_SUPPORTED_TYPES = {
    PipelineType.SCENARIO_CURATION,
}

_UNSUPPORTED_TYPES = {
    PipelineType.SCENE_RECONSTRUCTION,
    PipelineType.SCENE_REGISTRATION,
    PipelineType.GENERATED_DATASET_PREPARATION,
}


class TestPipelineDefinitionMetadata:
    def test_supported_pipelines_are_supported_and_implemented(self) -> None:
        by_type = {d.type: d for d in BUILTIN_PIPELINE_DEFINITIONS}
        for pipeline_type in _SUPPORTED_TYPES:
            d = by_type[pipeline_type]
            assert d.supported is True, f"{pipeline_type} should be supported"
            assert d.implemented is True, f"{pipeline_type} should be implemented"
            assert (
                d.experimental is False
            ), f"{pipeline_type} should not be experimental"

    def test_experimental_supported_pipelines_are_supported_and_implemented(
        self,
    ) -> None:
        by_type = {d.type: d for d in BUILTIN_PIPELINE_DEFINITIONS}
        for pipeline_type in _EXPERIMENTAL_SUPPORTED_TYPES:
            d = by_type[pipeline_type]
            assert d.supported is True, f"{pipeline_type} should be supported"
            assert d.implemented is True, f"{pipeline_type} should be implemented"
            assert d.experimental is True, f"{pipeline_type} should be experimental"

    def test_unsupported_pipelines_are_marked_correctly(self) -> None:
        by_type = {d.type: d for d in BUILTIN_PIPELINE_DEFINITIONS}
        for pipeline_type in _UNSUPPORTED_TYPES:
            d = by_type[pipeline_type]
            assert d.supported is False, f"{pipeline_type} should not be supported"
            assert d.implemented is False, f"{pipeline_type} should not be implemented"
            assert d.experimental is True, f"{pipeline_type} should be experimental"


class TestBuildScenesTaskConfig:
    """Verify build_scenes default_params: channels come from dataset, not hardcoded."""

    def _get_task(self, task_id: str):
        return next(
            t
            for t in RAW_LOG_SCENE_BUILDING_PIPELINE.tasks
            if t.pipeline_task_id == task_id
        )

    def test_build_scenes_sampling_has_missing_channel_policy(self) -> None:
        task = self._get_task("build_scenes")
        sampling = task.default_params.get("sampling", {})
        assert "missing_channel_policy" in sampling

    def test_build_scenes_required_channels_not_hardcoded_in_default_params(
        self,
    ) -> None:
        # required_channels must come from DatasetVersionRecord, not be hardcoded here.
        task = self._get_task("build_scenes")
        sampling = task.default_params.get("sampling", {})
        assert "required_channels" not in sampling, (
            "required_channels must not be hardcoded in build_scenes.default_params; "
            "it is injected from DatasetVersionRecord by BuildScenesJobHandler"
        )

    def test_validate_scene_required_channels_not_hardcoded_in_default_params(
        self,
    ) -> None:
        # require_target_channels must come from DatasetVersionRecord, not be hardcoded here.
        task = self._get_task("validate_scene")
        assert "require_target_channels" not in task.default_params, (
            "require_target_channels must not be hardcoded in validate_scene.default_params; "
            "it is injected from DatasetVersionRecord by ValidateSceneJobHandler"
        )


class TestPipelineServiceFilter:
    """Simulate list_pipeline_definitions filtering logic from the API service."""

    def _list_supported(self, *, include_experimental: bool = False):
        return [
            d
            for d in BUILTIN_PIPELINE_DEFINITIONS
            if d.supported
            and d.implemented
            and (include_experimental or not d.experimental)
        ]

    def test_default_listing_only_returns_non_experimental_supported(self) -> None:
        result = self._list_supported()
        types = {d.type for d in result}
        assert types == _SUPPORTED_TYPES

    def test_experimental_listing_includes_scenario_curation(self) -> None:
        result = self._list_supported(include_experimental=True)
        types = {d.type for d in result}
        assert _SUPPORTED_TYPES | _EXPERIMENTAL_SUPPORTED_TYPES <= types

    def test_unsupported_pipelines_absent_from_default_listing(self) -> None:
        result = self._list_supported()
        types = {d.type for d in result}
        for pipeline_type in _UNSUPPORTED_TYPES:
            assert pipeline_type not in types

    def test_experimental_supported_pipelines_absent_from_default_listing(self) -> None:
        result = self._list_supported()
        types = {d.type for d in result}
        for pipeline_type in _EXPERIMENTAL_SUPPORTED_TYPES:
            assert pipeline_type not in types

    def test_create_run_raises_for_unsupported(self) -> None:
        by_type = {d.type: d for d in BUILTIN_PIPELINE_DEFINITIONS}
        for pipeline_type in _UNSUPPORTED_TYPES:
            d = by_type[pipeline_type]
            with pytest.raises(ValueError, match="not currently supported"):
                if not d.supported or not d.implemented:
                    raise ValueError(
                        f"Pipeline '{d.type}' is not currently supported because it "
                        "contains unimplemented tasks."
                    )

    def test_scenario_curation_pipeline_is_supported_and_implemented(self) -> None:
        assert SCENARIO_CURATION_PIPELINE.supported is True
        assert SCENARIO_CURATION_PIPELINE.implemented is True
        assert SCENARIO_CURATION_PIPELINE.experimental is True

    def test_scenario_curation_tasks(self) -> None:
        tasks = {t.pipeline_task_id: t for t in SCENARIO_CURATION_PIPELINE.tasks}
        assert "mine_scenarios" in tasks
        assert "score_scenario_readiness" in tasks
        assert tasks["mine_scenarios"].job_type == JobType.MINE_SCENARIOS
        assert (
            tasks["score_scenario_readiness"].job_type
            == JobType.SCORE_SCENARIO_READINESS
        )
        assert tasks["mine_scenarios"].order < tasks["score_scenario_readiness"].order
        assert (
            "mine_scenarios"
            in tasks["score_scenario_readiness"].depends_on_pipeline_task_ids
        )

    def test_scenario_curation_mine_outputs_scenario_set_ref(self) -> None:
        tasks = {t.pipeline_task_id: t for t in SCENARIO_CURATION_PIPELINE.tasks}
        mine_task = tasks["mine_scenarios"]
        output_names = {o.name for o in mine_task.outputs}
        assert "scenario_set_id" in output_names
        assert "scenario_set_uri" in output_names
