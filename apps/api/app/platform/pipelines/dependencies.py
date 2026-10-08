from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.core.dependencies import ApiSettingsDep
from app.core.repositories import PipelineRunRepositoryDep, PipelineTaskRunRepositoryDep
from app.platform.executions.dependencies import PipelineExecutionBackendDep
from sceneops_execution.pipelines.dispatch_facade import PipelineDispatchFacade
from sceneops_execution.pipelines.service import PipelineService
from sceneops_db.session import get_async_sessionmaker


def get_pipeline_service(
    pipeline_repository: PipelineRunRepositoryDep,
    task_repository: PipelineTaskRunRepositoryDep,
    settings: ApiSettingsDep,
) -> PipelineService:
    return PipelineService(
        pipeline_repository=pipeline_repository,
        task_repository=task_repository,
    )


PipelineServiceDep = Annotated[PipelineService, Depends(get_pipeline_service)]


def get_pipeline_dispatch_facade(
    settings: ApiSettingsDep,
    pipeline_backend: PipelineExecutionBackendDep,
) -> PipelineDispatchFacade:
    return PipelineDispatchFacade(
        session_factory=get_async_sessionmaker(),
        pipeline_backend=pipeline_backend,
    )


PipelineDispatchFacadeDep = Annotated[
    PipelineDispatchFacade, Depends(get_pipeline_dispatch_facade)
]
