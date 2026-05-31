from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from celery import Celery
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ApiSettings, get_settings
from app.modules.artifacts import ArtifactService
from app.modules.datasets.service import DatasetService
from app.modules.jobs.service import JobService
from app.modules.models.service import ModelService
from app.modules.pipelines.service import PipelineService
from app.modules.runs.service import RunService
from app.modules.executions import create_celery_app
from app.modules.executions.dispatchers import (
    AirflowExecutionDispatcher,
    CeleryExecutionDispatcher,
    ExecutionDispatcher,
)
from sceneops_core.schemas.executions import ExecutionBackend
from sceneops_db.datasets import (
    DatasetRepository,
    DatasetVersionRepository,
    PostgresDatasetRepository,
    PostgresDatasetVersionRepository,
)
from sceneops_db.jobs import (
    JobEventRepository,
    JobRepository,
    PostgresJobEventRepository,
    PostgresJobRepository,
)
from sceneops_db.model_registry import (
    ModelRepository,
    ModelVersionRepository,
    PostgresModelRepository,
    PostgresModelVersionRepository,
)
from sceneops_db.pipelines import (
    PipelineRunRepository,
    PipelineStepRunRepository,
    PostgresPipelineRunRepository,
    PostgresPipelineStepRunRepository,
)
from sceneops_db.runs import (
    DatasetValidationRunRepository,
    EvaluationRunRepository,
    InferenceRunRepository,
    PostgresDatasetValidationRunRepository,
    PostgresEvaluationRunRepository,
    PostgresInferenceRunRepository,
)
from sceneops_db.session import async_session_scope

from sceneops_storage import ArtifactStore, create_artifact_store


def get_api_settings() -> ApiSettings:
    return get_settings()


async def get_db_session() -> AsyncIterator[AsyncSession]:
    async with async_session_scope() as session:
        yield session


async def get_dataset_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> DatasetRepository:
    return PostgresDatasetRepository(session)


async def get_dataset_version_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> DatasetVersionRepository:
    return PostgresDatasetVersionRepository(session)


async def get_job_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> JobRepository:
    return PostgresJobRepository(session)


async def get_job_event_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> JobEventRepository:
    return PostgresJobEventRepository(session)


async def get_pipeline_run_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> PipelineRunRepository:
    return PostgresPipelineRunRepository(session)


async def get_pipeline_step_run_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> PipelineStepRunRepository:
    return PostgresPipelineStepRunRepository(session)


async def get_inference_run_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> InferenceRunRepository:
    return PostgresInferenceRunRepository(session)


async def get_evaluation_run_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> EvaluationRunRepository:
    return PostgresEvaluationRunRepository(session)


async def get_validation_run_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> DatasetValidationRunRepository:
    return PostgresDatasetValidationRunRepository(session)


async def get_model_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ModelRepository:
    return PostgresModelRepository(session)


async def get_model_version_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ModelVersionRepository:
    return PostgresModelVersionRepository(session)


def get_artifact_store(
    settings: Annotated[ApiSettings, Depends(get_api_settings)],
) -> ArtifactStore:
    return create_artifact_store(settings.artifact)


def get_celery_app(
    settings: Annotated[ApiSettings, Depends(get_api_settings)],
) -> Celery:
    celery_settings = settings.execution.celery
    return create_celery_app(
        broker_url=celery_settings.broker_url,
        result_backend=celery_settings.result_backend,
    )


def get_execution_dispatcher(
    settings: Annotated[ApiSettings, Depends(get_api_settings)],
    celery_app: Annotated[Celery, Depends(get_celery_app)],
) -> ExecutionDispatcher:
    execution = settings.execution

    if execution.backend == ExecutionBackend.CELERY:
        return CeleryExecutionDispatcher(
            app=celery_app,
            pipeline_queue=execution.celery.pipeline_queue,
            job_queue=execution.celery.job_queue,
        )

    if execution.backend == ExecutionBackend.AIRFLOW:
        return AirflowExecutionDispatcher(
            base_url=execution.airflow.base_url,
            username=execution.airflow.username,
            password=execution.airflow.password,
            pipeline_dag_id=execution.airflow.pipeline_dag_id,
            job_dag_id=execution.airflow.job_dag_id,
        )

    raise ValueError(
        f"Unsupported execution backend for API dispatch: {execution.backend}"
    )


def get_dataset_service(
    repository: Annotated[DatasetRepository, Depends(get_dataset_repository)],
    version_repository: Annotated[
        DatasetVersionRepository,
        Depends(get_dataset_version_repository),
    ],
) -> DatasetService:
    return DatasetService(
        repository=repository,
        version_repository=version_repository,
    )


def get_job_service(
    repository: Annotated[JobRepository, Depends(get_job_repository)],
    event_repository: Annotated[
        JobEventRepository,
        Depends(get_job_event_repository),
    ],
    settings: Annotated[ApiSettings, Depends(get_api_settings)],
) -> JobService:
    return JobService(
        repository=repository,
        event_repository=event_repository,
        default_dataset_id=settings.default_dataset_id,
        default_dataset_version=settings.default_dataset_version,
    )


def get_pipeline_service(
    pipeline_repository: Annotated[
        PipelineRunRepository,
        Depends(get_pipeline_run_repository),
    ],
    step_repository: Annotated[
        PipelineStepRunRepository,
        Depends(get_pipeline_step_run_repository),
    ],
    settings: Annotated[ApiSettings, Depends(get_api_settings)],
) -> PipelineService:
    return PipelineService(
        pipeline_repository=pipeline_repository,
        step_repository=step_repository,
        default_dataset_id=settings.default_dataset_id,
        default_dataset_version=settings.default_dataset_version,
    )


def get_run_service(
    inference_repository: Annotated[
        InferenceRunRepository,
        Depends(get_inference_run_repository),
    ],
    evaluation_repository: Annotated[
        EvaluationRunRepository,
        Depends(get_evaluation_run_repository),
    ],
    validation_repository: Annotated[
        DatasetValidationRunRepository,
        Depends(get_validation_run_repository),
    ],
) -> RunService:
    return RunService(
        inference_repository=inference_repository,
        evaluation_repository=evaluation_repository,
        validation_repository=validation_repository,
    )


def get_model_service(
    repository: Annotated[ModelRepository, Depends(get_model_repository)],
    version_repository: Annotated[
        ModelVersionRepository,
        Depends(get_model_version_repository),
    ],
) -> ModelService:
    return ModelService(
        repository=repository,
        version_repository=version_repository,
    )


def get_artifact_service(
    dataset_version_repository: Annotated[
        DatasetVersionRepository,
        Depends(get_dataset_version_repository),
    ],
    inference_run_repository: Annotated[
        InferenceRunRepository,
        Depends(get_inference_run_repository),
    ],
    evaluation_run_repository: Annotated[
        EvaluationRunRepository,
        Depends(get_evaluation_run_repository),
    ],
    validation_run_repository: Annotated[
        DatasetValidationRunRepository,
        Depends(get_validation_run_repository),
    ],
    artifact_store: Annotated[
        ArtifactStore,
        Depends(get_artifact_store),
    ],
) -> ArtifactService:
    return ArtifactService(
        dataset_version_repository=dataset_version_repository,
        inference_run_repository=inference_run_repository,
        evaluation_run_repository=evaluation_run_repository,
        validation_run_repository=validation_run_repository,
        artifact_store=artifact_store,
    )
