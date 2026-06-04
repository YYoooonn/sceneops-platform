from __future__ import annotations

from typing import Protocol, TypeAlias, runtime_checkable

from sceneops_core.datasets.schemas import DatasetRecord, DatasetVersionRecord
from sceneops_core.datasets.schemas.enums import DatasetStatus, DatasetType
from sceneops_core.datasets.schemas.runs import (
    DatasetDistributionRunRecord,
    DatasetExportRunRecord,
    DatasetProfileRunRecord,
    DatasetValidationRunRecord,
)
from sceneops_core.datasets.schemas.validation import DatasetValidationStatus
from sceneops_core.runs.schemas import RunStatus, RunType

DatasetRunRecord: TypeAlias = (
    DatasetValidationRunRecord
    | DatasetProfileRunRecord
    | DatasetDistributionRunRecord
    | DatasetExportRunRecord
)


@runtime_checkable
class DatasetRepository(Protocol):
    async def create(self, dataset: DatasetRecord) -> DatasetRecord: ...

    async def upsert(self, dataset: DatasetRecord) -> DatasetRecord: ...

    async def get(self, dataset_id: str) -> DatasetRecord | None: ...

    async def update(self, dataset: DatasetRecord) -> DatasetRecord: ...

    async def list(
        self,
        *,
        type: DatasetType | None = None,
        status: DatasetStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DatasetRecord]: ...


@runtime_checkable
class DatasetVersionRepository(Protocol):
    async def create(self, version: DatasetVersionRecord) -> DatasetVersionRecord: ...

    async def upsert(self, version: DatasetVersionRecord) -> DatasetVersionRecord: ...

    async def get(
        self,
        *,
        dataset_id: str,
        version: str,
    ) -> DatasetVersionRecord | None: ...

    async def update(self, version: DatasetVersionRecord) -> DatasetVersionRecord: ...

    async def update_quality_cache(
        self,
        *,
        dataset_id: str,
        version: str,
        latest_validation_run_id: str | None = None,
        validation_status: DatasetValidationStatus | None = None,
        should_block_pipeline: bool | None = None,
        validation_report_uri: str | None = None,
        latest_profile_run_id: str | None = None,
        profile_report_uri: str | None = None,
        latest_distribution_run_id: str | None = None,
        distribution_report_uri: str | None = None,
    ) -> DatasetVersionRecord: ...

    async def list(
        self,
        *,
        dataset_id: str | None = None,
        status: DatasetStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DatasetVersionRecord]: ...


@runtime_checkable
class DatasetRunRepository(Protocol):
    async def create(self, run: DatasetRunRecord) -> DatasetRunRecord: ...

    async def get(self, run_id: str) -> DatasetRunRecord | None: ...

    async def update(self, run: DatasetRunRecord) -> DatasetRunRecord: ...

    async def list(
        self,
        *,
        type: RunType | None = None,
        status: RunStatus | None = None,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        job_id: str | None = None,
        pipeline_run_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DatasetRunRecord]: ...
