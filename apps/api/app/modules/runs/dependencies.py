from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.core.dependencies import DbSessionDep
from app.modules.runs.service import RunService
from sceneops_db.runs import (
    DatasetProfileRunRepository,
    DatasetValidationRunRepository,
    EvaluationRunRepository,
    InferenceRunRepository,
    PostgresDatasetProfileRunRepository,
    PostgresDatasetValidationRunRepository,
    PostgresEvaluationRunRepository,
    PostgresInferenceRunRepository,
)


def get_inference_run_repository(
    session: DbSessionDep,
) -> InferenceRunRepository:
    return PostgresInferenceRunRepository(session)


InferenceRunRepositoryDep = Annotated[
    InferenceRunRepository,
    Depends(get_inference_run_repository),
]


def get_evaluation_run_repository(
    session: DbSessionDep,
) -> EvaluationRunRepository:
    return PostgresEvaluationRunRepository(session)


EvaluationRunRepositoryDep = Annotated[
    EvaluationRunRepository,
    Depends(get_evaluation_run_repository),
]


def get_validation_run_repository(
    session: DbSessionDep,
) -> DatasetValidationRunRepository:
    return PostgresDatasetValidationRunRepository(session)


ValidationRunRepositoryDep = Annotated[
    DatasetValidationRunRepository,
    Depends(get_validation_run_repository),
]


def get_profile_run_repository(
    session: DbSessionDep,
) -> DatasetProfileRunRepository:
    return PostgresDatasetProfileRunRepository(session)


ProfileRunRepositoryDep = Annotated[
    DatasetProfileRunRepository,
    Depends(get_profile_run_repository),
]


def get_run_service(
    inference_repository: InferenceRunRepositoryDep,
    evaluation_repository: EvaluationRunRepositoryDep,
    validation_repository: ValidationRunRepositoryDep,
    profile_repository: ProfileRunRepositoryDep,
) -> RunService:
    return RunService(
        inference_repository=inference_repository,
        evaluation_repository=evaluation_repository,
        validation_repository=validation_repository,
        profile_repository=profile_repository,
    )


RunServiceDep = Annotated[
    RunService,
    Depends(get_run_service),
]
