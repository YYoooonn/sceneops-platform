"""Unit tests for PipelineService execution_key dedup.

Uses tiny in-memory fakes implementing PipelineRunRepository/
PipelineTaskRunRepository, mirroring test_job_service.py's approach.
"""

from __future__ import annotations

from app.platform.pipelines.service import PipelineService
from sceneops_core.pipelines.schemas import (
    CreatePipelineRunRequest,
    PipelineRunManifest,
    PipelineRunStatus,
    PipelineTaskRunManifest,
    PipelineType,
)

DATASET_ID = "nuscenes"
DATASET_VERSION = "v1.0-mini"


class FakePipelineRunRepository:
    def __init__(self) -> None:
        self.runs: dict[str, PipelineRunManifest] = {}

    async def create(self, run: PipelineRunManifest) -> PipelineRunManifest:
        self.runs[run.pipeline_run_id] = run
        return run

    async def get(self, pipeline_run_id: str) -> PipelineRunManifest | None:
        return self.runs.get(pipeline_run_id)

    async def update(self, run: PipelineRunManifest) -> PipelineRunManifest:
        self.runs[run.pipeline_run_id] = run
        return run

    async def list(self, **kwargs) -> list[PipelineRunManifest]:
        return list(self.runs.values())

    async def count_by_status(self) -> dict[str, int]:
        return {}

    async def find_by_execution_key(
        self, execution_key: str, *, statuses
    ) -> PipelineRunManifest | None:
        for run in self.runs.values():
            if run.execution_key == execution_key and run.status in statuses:
                return run
        return None


class FakePipelineTaskRunRepository:
    def __init__(self) -> None:
        self.tasks: dict[str, PipelineTaskRunManifest] = {}

    async def create(self, task: PipelineTaskRunManifest) -> PipelineTaskRunManifest:
        self.tasks[task.pipeline_task_run_id] = task
        return task

    async def get(self, pipeline_task_run_id: str) -> PipelineTaskRunManifest | None:
        return self.tasks.get(pipeline_task_run_id)

    async def update(self, task: PipelineTaskRunManifest) -> PipelineTaskRunManifest:
        self.tasks[task.pipeline_task_run_id] = task
        return task

    async def list_for_pipeline_run(
        self, pipeline_run_id: str, **kwargs
    ) -> list[PipelineTaskRunManifest]:
        return [t for t in self.tasks.values() if t.pipeline_run_id == pipeline_run_id]

    async def get_by_task_id(
        self, *, pipeline_run_id: str, task_id: str
    ) -> PipelineTaskRunManifest | None:
        for t in self.tasks.values():
            if t.pipeline_run_id == pipeline_run_id and t.pipeline_task_id == task_id:
                return t
        return None


def _service() -> tuple[PipelineService, FakePipelineRunRepository]:
    pipeline_repo = FakePipelineRunRepository()
    service = PipelineService(
        pipeline_repository=pipeline_repo,
        task_repository=FakePipelineTaskRunRepository(),
        default_dataset_id=DATASET_ID,
        default_dataset_version=DATASET_VERSION,
    )
    return service, pipeline_repo


def _request(**overrides) -> CreatePipelineRunRequest:
    base = dict(
        type=PipelineType.SCENARIO_CURATION,
        dataset_id=DATASET_ID,
        dataset_version=DATASET_VERSION,
        params={},
    )
    base.update(overrides)
    return CreatePipelineRunRequest(**base)


async def test_identical_requests_return_same_pipeline_run():
    service, _ = _service()

    first = await service.create_pipeline_run(_request())
    second = await service.create_pipeline_run(_request())

    assert first.pipeline_run.pipeline_run_id == second.pipeline_run.pipeline_run_id
    # Reused response still carries the originally created tasks.
    assert len(second.tasks) == len(first.tasks) > 0


async def test_force_bypasses_dedup():
    service, _ = _service()

    first = await service.create_pipeline_run(_request())
    second = await service.create_pipeline_run(_request(force=True))

    assert first.pipeline_run.pipeline_run_id != second.pipeline_run.pipeline_run_id


async def test_different_dataset_version_not_deduped():
    service, _ = _service()

    first = await service.create_pipeline_run(_request())
    second = await service.create_pipeline_run(
        _request(dataset_version="v1.0-trainval")
    )

    assert first.pipeline_run.pipeline_run_id != second.pipeline_run.pipeline_run_id


async def test_succeeded_run_is_a_dedup_match():
    service, repo = _service()

    first = await service.create_pipeline_run(_request())
    succeeded = first.pipeline_run.model_copy(
        update={"status": PipelineRunStatus.SUCCEEDED}
    )
    await repo.update(succeeded)

    second = await service.create_pipeline_run(_request())

    assert first.pipeline_run.pipeline_run_id == second.pipeline_run.pipeline_run_id
