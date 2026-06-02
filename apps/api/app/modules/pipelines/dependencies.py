from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.core.dependencies import ApiSettingsDep, DbSessionDep
from app.modules.pipelines.service import PipelineService
from sceneops_db.pipelines import (
    PipelineRunRepository,
    PipelineStepRunRepository,
    PostgresPipelineRunRepository,
    PostgresPipelineStepRunRepository,
)


def get_pipeline_run_repository(
    session: DbSessionDep,
) -> PipelineRunRepository:
    return PostgresPipelineRunRepository(session)


PipelineRunRepositoryDep = Annotated[
    PipelineRunRepository,
    Depends(get_pipeline_run_repository),
]


def get_pipeline_step_run_repository(
    session: DbSessionDep,
) -> PipelineStepRunRepository:
    return PostgresPipelineStepRunRepository(session)


PipelineStepRunRepositoryDep = Annotated[
    PipelineStepRunRepository,
    Depends(get_pipeline_step_run_repository),
]


def get_pipeline_service(
    pipeline_repository: PipelineRunRepositoryDep,
    step_repository: PipelineStepRunRepositoryDep,
    settings: ApiSettingsDep,
) -> PipelineService:
    return PipelineService(
        pipeline_repository=pipeline_repository,
        step_repository=step_repository,
        default_dataset_id=settings.default_dataset_id,
        default_dataset_version=settings.default_dataset_version,
    )


PipelineServiceDep = Annotated[
    PipelineService,
    Depends(get_pipeline_service),
]
