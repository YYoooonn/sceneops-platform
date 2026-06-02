from __future__ import annotations

from typing import Generic, Protocol, TypeVar, runtime_checkable

DatasetIngestionRequestT = TypeVar("DatasetIngestionRequestT", contravariant=True)
DatasetIngestionResultT = TypeVar("DatasetIngestionResultT", covariant=True)

DatasetValidationRequestT = TypeVar("DatasetValidationRequestT", contravariant=True)
DatasetValidationResultT = TypeVar("DatasetValidationResultT", covariant=True)

DatasetProfileRequestT = TypeVar("DatasetProfileRequestT", contravariant=True)
DatasetProfileResultT = TypeVar("DatasetProfileResultT", covariant=True)


@runtime_checkable
class DatasetIngestor(
    Protocol,
    Generic[DatasetIngestionRequestT, DatasetIngestionResultT],
):
    """Port-like contract for dataset ingestion.

    Implementations may ingest nuScenes, ROS bags, custom robot logs,
    or simulation-generated datasets into SceneOps manifests.
    """

    @property
    def dataset_type(self) -> str:
        """Stable dataset type identifier, e.g. nuscenes."""

    async def run(
        self,
        request: DatasetIngestionRequestT,
    ) -> DatasetIngestionResultT:
        """Ingest a dataset version and return a task-specific result."""


@runtime_checkable
class DatasetValidator(
    Protocol,
    Generic[DatasetValidationRequestT, DatasetValidationResultT],
):
    """Port-like contract for dataset quality validation."""

    @property
    def validator_id(self) -> str:
        """Stable validator identifier, e.g. manifest-validator."""

    async def run(
        self,
        request: DatasetValidationRequestT,
    ) -> DatasetValidationResultT:
        """Validate a dataset version and return a validation report."""


@runtime_checkable
class DatasetProfiler(
    Protocol,
    Generic[DatasetProfileRequestT, DatasetProfileResultT],
):
    """Port-like contract for dataset profiling."""

    @property
    def profiler_id(self) -> str:
        """Stable profiler identifier, e.g. standard-dataset-profiler."""

    async def run(
        self,
        request: DatasetProfileRequestT,
    ) -> DatasetProfileResultT:
        """Profile a dataset version and return a profile report."""
