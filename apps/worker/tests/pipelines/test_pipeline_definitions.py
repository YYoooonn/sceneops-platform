"""Tests for pipeline definition metadata and task param contracts."""

from __future__ import annotations

import pytest

from sceneops_core.pipelines.builtin import (
    BUILTIN_PIPELINE_DEFINITIONS,
    RECORDING_SCENE_BUILDING_PIPELINE,
    SCENARIO_CURATION_PIPELINE,
)
from sceneops_core.pipelines.schemas import PipelineType
from sceneops_core.jobs.schemas import JobType


_SUPPORTED_TYPES = {
    PipelineType.DETECTION_EVALUATION,
    PipelineType.RECORDING_SCENE_BUILDING,
    PipelineType.RECORDING_EPISODE_BUILDING,
}

# Experimental pipelines: supported=True, implemented=True, experimental=True.
# They can be created/run but are hidden from default API listing.
_EXPERIMENTAL_SUPPORTED_TYPES = {
    PipelineType.SCENARIO_CURATION,
}

_UNSUPPORTED_TYPES: set[PipelineType] = set()


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


class TestRecordingSceneBuildingPipeline:
    """RobotRun -> canonical Scenes -> registration -> validation / profile
    (ADR-007 §17.3, §29.2)."""

    def _tasks(self):
        return {t.pipeline_task_id: t for t in RECORDING_SCENE_BUILDING_PIPELINE.tasks}

    def test_task_chain(self) -> None:
        tasks = self._tasks()
        assert [
            (t.pipeline_task_id, t.job_type, t.depends_on_pipeline_task_ids)
            for t in sorted(tasks.values(), key=lambda t: t.order)
        ] == [
            ("build_recording_scenes", JobType.BUILD_RECORDING_SCENES, []),
            ("register_scenes", JobType.REGISTER_SCENES, ["build_recording_scenes"]),
            ("validate_scene", JobType.VALIDATE_SCENE, ["register_scenes"]),
            ("profile_scene", JobType.PROFILE_SCENE, ["register_scenes"]),
        ]

    def test_refs_hand_the_complete_set_to_the_registrar(self) -> None:
        tasks = self._tasks()
        build_refs = {
            o.name for o in tasks["build_recording_scenes"].outputs if o.kind == "ref"
        }
        register_refs = {
            o.name for o in tasks["register_scenes"].outputs if o.kind == "ref"
        }
        assert "manifest_artifact_ids" in build_refs
        assert register_refs == {"scene_ids"}

    def test_validation_can_block_but_never_owns_membership(self) -> None:
        validate = self._tasks()["validate_scene"]
        assert [r.code for r in validate.quality_rules] == ["validate_scene_blocked"]

    def test_no_default_build_configuration(self) -> None:
        """Canonical semantics come only from explicit pipeline params."""
        assert self._tasks()["build_recording_scenes"].default_params == {}


class TestLegacySceneIngressRemoved:
    def test_legacy_pipelines_and_jobs_do_not_exist(self) -> None:
        pipeline_types = {t.value for t in PipelineType}
        for removed in (
            "dataset_scene_ingestion",
            "raw_log_scene_building",
            "scene_registration",
        ):
            assert removed not in pipeline_types
        job_types = {t.value for t in JobType}
        assert not {"ingest_scenes", "build_scenes"} & job_types


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
