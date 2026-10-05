"""Tests for pipeline definition metadata and task param contracts."""

from __future__ import annotations

from sceneops_core.pipelines.builtin import (
    BUILTIN_PIPELINE_DEFINITIONS,
    EPISODE_LEARNING_DATA_BUILDING_PIPELINE,
    RECORDING_SCENE_BUILDING_PIPELINE,
    SCENE_ML_EVALUATION_PIPELINE,
)
from sceneops_core.pipelines.schemas import PipelineType
from sceneops_core.jobs.schemas import JobType


_FINAL_PIPELINE_TYPES = {
    PipelineType.RECORDING_SCENE_BUILDING,
    PipelineType.RECORDING_EPISODE_BUILDING,
    PipelineType.SCENE_ML_EVALUATION,
    PipelineType.EPISODE_LEARNING_DATA_BUILDING,
}


class TestFinalPipelineSurface:
    """Exactly four first-class Pipelines exist (ADR-007 §34)."""

    def test_exactly_the_four_final_pipelines(self) -> None:
        assert {p.value for p in PipelineType} == {
            "recording_scene_building",
            "recording_episode_building",
            "scene_ml_evaluation",
            "episode_learning_data_building",
        }
        assert {d.type for d in BUILTIN_PIPELINE_DEFINITIONS} == _FINAL_PIPELINE_TYPES
        assert len(BUILTIN_PIPELINE_DEFINITIONS) == len(_FINAL_PIPELINE_TYPES)

    def test_every_pipeline_has_a_definition_with_tasks(self) -> None:
        for definition in BUILTIN_PIPELINE_DEFINITIONS:
            assert definition.tasks, definition.type

    def test_task_dependencies_reference_earlier_tasks(self) -> None:
        for definition in BUILTIN_PIPELINE_DEFINITIONS:
            order = {t.pipeline_task_id: t.order for t in definition.tasks}
            assert len(order) == len(definition.tasks), "task ids are unique"
            for task in definition.tasks:
                for dep in task.depends_on_pipeline_task_ids:
                    assert order[dep] < task.order, (definition.type, task)

    def test_removed_pipelines_do_not_exist(self) -> None:
        values = {t.value for t in PipelineType}
        assert not values & {
            "scenario_curation",
            "detection_evaluation",
            "aligned_episode_building",
        }


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


class TestSceneMlEvaluationPipeline:
    """Scenes -> sample views -> ScenarioSet -> prediction -> evaluation."""

    def _tasks(self):
        return {t.pipeline_task_id: t for t in SCENE_ML_EVALUATION_PIPELINE.tasks}

    def test_task_chain(self) -> None:
        tasks = self._tasks()
        assert [
            (t.pipeline_task_id, t.job_type, t.depends_on_pipeline_task_ids)
            for t in sorted(tasks.values(), key=lambda t: t.order)
        ] == [
            ("build_scene_sample_views", JobType.BUILD_SCENE_SAMPLE_VIEWS, []),
            (
                "mine_scenarios",
                JobType.MINE_SCENARIOS,
                ["build_scene_sample_views"],
            ),
            (
                "score_scenario_readiness",
                JobType.SCORE_SCENARIO_READINESS,
                ["mine_scenarios"],
            ),
            ("predict_detection", JobType.PREDICT_DETECTION, ["mine_scenarios"]),
            ("evaluate_detection", JobType.EVALUATE_DETECTION, ["predict_detection"]),
        ]

    def test_refs_hand_pinned_revisions_between_stages(self) -> None:
        tasks = self._tasks()

        def refs(task_id: str) -> set[str]:
            return {o.name for o in tasks[task_id].outputs if o.kind == "ref"}

        assert refs("build_scene_sample_views") == {"views"}
        assert {"scenario_set_id", "scenario_set_checksum"} <= refs("mine_scenarios")
        assert {"inference_run_id", "prediction_manifest_checksum"} <= refs(
            "predict_detection"
        )

    def test_no_stage_defaults_a_source_vocabulary(self) -> None:
        """Policies, channels, label sets and categories are explicit params."""
        tasks = self._tasks()
        assert tasks["build_scene_sample_views"].default_params == {}
        assert tasks["mine_scenarios"].default_params == {}
        assert set(tasks["predict_detection"].default_params) == {"inference_backend"}
        assert set(tasks["evaluate_detection"].default_params) == {
            "evaluator_id",
            "match_distance_m",
        }


class TestEpisodeLearningDataBuildingPipeline:
    """Episodes -> AlignedEpisodes -> LearningDataExport."""

    def _tasks(self):
        return {
            t.pipeline_task_id: t for t in EPISODE_LEARNING_DATA_BUILDING_PIPELINE.tasks
        }

    def test_task_chain(self) -> None:
        tasks = self._tasks()
        assert [
            (t.pipeline_task_id, t.job_type, t.depends_on_pipeline_task_ids)
            for t in sorted(tasks.values(), key=lambda t: t.order)
        ] == [
            ("align_episode", JobType.ALIGN_EPISODE, []),
            (
                "export_learning_data",
                JobType.EXPORT_LEARNING_DATA,
                ["align_episode"],
            ),
        ]

    def test_alignment_hands_the_export_its_pinned_inputs(self) -> None:
        align = self._tasks()["align_episode"]
        assert {o.name for o in align.outputs if o.kind == "ref"} == {"export_inputs"}

    def test_no_default_alignment_config(self) -> None:
        assert self._tasks()["align_episode"].default_params == {}
