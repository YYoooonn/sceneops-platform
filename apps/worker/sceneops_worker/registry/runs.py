from __future__ import annotations

from sceneops_core.schemas.runs import EvaluationRunRecord, InferenceRunRecord
from sceneops_db.runs import (
    PostgresEvaluationRunRepository,
    PostgresInferenceRunRepository,
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
