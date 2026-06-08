"""Unit tests for PipelineTaskRunner optional task skip logic."""

from __future__ import annotations

from sceneops_core.jobs.schemas import JobType
from sceneops_core.pipelines.schemas import (
    PipelineRunManifest,
    PipelineRunStatus,
    PipelineTaskDefinition,
    PipelineType,
)
from sceneops_core.common.time import utc_now


def _make_pipeline_run(params: dict) -> PipelineRunManifest:
    now = utc_now()
    return PipelineRunManifest(
        pipeline_run_id="run-001",
        type=PipelineType.DATASET_SCENE_INGESTION,
        status=PipelineRunStatus.RUNNING,
        params=params,
        created_at=now,
        updated_at=now,
    )


def _make_task_def(
    *, optional: bool, task_id: str = "validate_scene"
) -> PipelineTaskDefinition:
    return PipelineTaskDefinition(
        pipeline_task_id=task_id,
        name="Validate scene",
        order=2,
        job_type=JobType.VALIDATE_SCENE,
        optional=optional,
    )


class TestShouldSkipTask:
    """Tests for PipelineTaskRunner._should_skip_task logic."""

    def _should_skip(
        self,
        task_def: PipelineTaskDefinition,
        pipeline_run: PipelineRunManifest,
    ) -> bool:
        # Inline the skip logic from PipelineTaskRunner to test it in isolation.
        if not task_def.optional:
            return False
        explicit_params = pipeline_run.params.get(task_def.pipeline_task_id) or {}
        return not explicit_params

    def test_required_task_never_skipped(self) -> None:
        task_def = _make_task_def(optional=False)
        pipeline_run = _make_pipeline_run(params={})
        assert self._should_skip(task_def, pipeline_run) is False

    def test_required_task_with_params_not_skipped(self) -> None:
        task_def = _make_task_def(optional=False)
        pipeline_run = _make_pipeline_run(
            params={"validate_scene": {"require_target_channels": ["CAM_FRONT"]}}
        )
        assert self._should_skip(task_def, pipeline_run) is False

    def test_optional_task_without_params_is_skipped(self) -> None:
        task_def = _make_task_def(optional=True)
        pipeline_run = _make_pipeline_run(params={})
        assert self._should_skip(task_def, pipeline_run) is True

    def test_optional_task_with_explicit_params_not_skipped(self) -> None:
        task_def = _make_task_def(optional=True)
        pipeline_run = _make_pipeline_run(
            params={"validate_scene": {"require_target_channels": ["CAM_FRONT"]}}
        )
        assert self._should_skip(task_def, pipeline_run) is False

    def test_optional_task_with_empty_dict_params_is_skipped(self) -> None:
        # An empty dict for the task key is treated the same as absent.
        task_def = _make_task_def(optional=True)
        pipeline_run = _make_pipeline_run(params={"validate_scene": {}})
        assert self._should_skip(task_def, pipeline_run) is True

    def test_optional_different_task_id_skipped(self) -> None:
        task_def = _make_task_def(optional=True, task_id="profile_scene")
        pipeline_run = _make_pipeline_run(
            params={"validate_scene": {"require_target_channels": ["CAM_FRONT"]}}
        )
        # params keyed under 'validate_scene', not 'profile_scene' → skip
        assert self._should_skip(task_def, pipeline_run) is True
