from __future__ import annotations

from typing import Protocol

from sceneops_core.runs.schemas import (
    AutoLabelRunRecord,
    DatasetValidationRunRecord,
    EvaluationRunRecord,
    InferenceRunRecord,
    DatasetProfileRunRecord,
    RunStatus,
)
from sceneops_core.datasets.schemas import DatasetValidationStatus


class InferenceRunRepository(Protocol):
    async def upsert(self, record: InferenceRunRecord) -> InferenceRunRecord: ...

    async def get(self, run_id: str) -> InferenceRunRecord: ...

    async def list(
        self,
        *,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        model_id: str | None = None,
        model_version: str | None = None,
        inference_run_id: str | None = None,
        evaluator_id: str | None = None,
        status: RunStatus | None = None,
        limit: int | None = None,
    ) -> list[InferenceRunRecord]: ...


class EvaluationRunRepository(Protocol):
    async def upsert(self, record: EvaluationRunRecord) -> EvaluationRunRecord: ...

    async def get(self, evaluation_run_id: str) -> EvaluationRunRecord: ...

    async def list(
        self,
        *,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        model_id: str | None = None,
        model_version: str | None = None,
        inference_run_id: str | None = None,
        status: RunStatus | None = None,
    ) -> list[EvaluationRunRecord]: ...


class DatasetValidationRunRepository(Protocol):
    async def upsert(
        self,
        record: DatasetValidationRunRecord,
    ) -> DatasetValidationRunRecord: ...

    async def get(
        self,
        validation_run_id: str,
    ) -> DatasetValidationRunRecord: ...

    async def list(
        self,
        *,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        status: RunStatus | None = None,
        validation_status: DatasetValidationStatus | None = None,
    ) -> list[DatasetValidationRunRecord]: ...


class DatasetProfileRunRepository(Protocol):
    async def upsert(
        self,
        record: DatasetProfileRunRecord,
    ) -> DatasetProfileRunRecord: ...

    async def get(
        self,
        profile_run_id: str,
    ) -> DatasetProfileRunRecord | None: ...

    async def list(
        self,
        *,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        limit: int = 50,
    ) -> list[DatasetProfileRunRecord]: ...


class AutoLabelRunRepository(Protocol):
    async def upsert(self, record: AutoLabelRunRecord) -> AutoLabelRunRecord: ...

    async def get(self, auto_label_run_id: str) -> AutoLabelRunRecord: ...

    async def list(
        self,
        *,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        model_id: str | None = None,
        model_version: str | None = None,
        status: RunStatus | None = None,
        limit: int | None = None,
    ) -> list[AutoLabelRunRecord]: ...
