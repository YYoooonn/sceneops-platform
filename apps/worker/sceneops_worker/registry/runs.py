from __future__ import annotations

from sceneops_core.runs.schemas import (
    DatasetProfileRunRecord,
    EvaluationRunRecord,
    InferenceRunRecord,
    DatasetValidationRunRecord,
    RunStatus,
)
from sceneops_db.runs import (
    PostgresDatasetProfileRunRepository,
    PostgresEvaluationRunRepository,
    PostgresInferenceRunRepository,
    PostgresDatasetValidationRunRepository,
)
from sceneops_db.session import async_session_scope


class RunRegistryStore:
    async def get_inference_run(
        self,
        run_id: str,
    ) -> InferenceRunRecord:
        async with async_session_scope() as session:
            repository = PostgresInferenceRunRepository(session)
            return await repository.get(run_id)

    async def upsert_inference_run(
        self,
        record: InferenceRunRecord,
    ) -> InferenceRunRecord:
        async with async_session_scope() as session:
            repository = PostgresInferenceRunRepository(session)
            return await repository.upsert(record)

    async def get_evaluation_run(
        self,
        evaluation_run_id: str,
    ) -> EvaluationRunRecord:
        async with async_session_scope() as session:
            repository = PostgresEvaluationRunRepository(session)
            return await repository.get(evaluation_run_id)

    async def upsert_evaluation_run(
        self,
        record: EvaluationRunRecord,
    ) -> EvaluationRunRecord:
        async with async_session_scope() as session:
            repository = PostgresEvaluationRunRepository(session)
            return await repository.upsert(record)

    async def get_validation_run(
        self,
        validation_run_id: str,
    ) -> DatasetValidationRunRecord:
        async with async_session_scope() as session:
            repository = PostgresDatasetValidationRunRepository(session)
            return await repository.get(validation_run_id)

    async def upsert_validation_run(
        self,
        record: DatasetValidationRunRecord,
    ) -> DatasetValidationRunRecord:
        async with async_session_scope() as session:
            repository = PostgresDatasetValidationRunRepository(session)
            return await repository.upsert(record)

    async def list_validation_runs(
        self,
        *,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        status=None,
        validation_status=None,
    ) -> list[DatasetValidationRunRecord]:
        async with async_session_scope() as session:
            repository = PostgresDatasetValidationRunRepository(session)
            return await repository.list(
                dataset_id=dataset_id,
                dataset_version=dataset_version,
                status=status,
                validation_status=validation_status,
            )

    async def get_profile_run(
        self,
        validation_run_id: str,
    ) -> DatasetProfileRunRecord:
        async with async_session_scope() as session:
            repository = PostgresDatasetProfileRunRepository(session)
            return await repository.get(validation_run_id)

    async def upsert_profile_run(
        self,
        record: DatasetProfileRunRecord,
    ) -> DatasetProfileRunRecord:
        async with async_session_scope() as session:
            repository = PostgresDatasetProfileRunRepository(session)
            return await repository.upsert(record)

    async def list_profile_runs(
        self,
        *,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        status: RunStatus | None = None,
    ) -> list[DatasetProfileRunRecord]:
        async with async_session_scope() as session:
            repository = PostgresDatasetProfileRunRepository(session)
            return await repository.list(
                dataset_id=dataset_id,
                dataset_version=dataset_version,
                status=status,
            )
