from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.core.dependencies import ApiSettingsDep
from app.core.repositories import PipelineRunRepositoryDep, PipelineStepRunRepositoryDep
from app.platform.pipelines.service import PipelineService


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


PipelineServiceDep = Annotated[PipelineService, Depends(get_pipeline_service)]
