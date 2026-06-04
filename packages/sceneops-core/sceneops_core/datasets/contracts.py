from __future__ import annotations

from typing import Generic, Protocol, TypeVar, runtime_checkable

DatasetAssemblyRequestT = TypeVar("DatasetAssemblyRequestT", contravariant=True)
DatasetAssemblyResultT = TypeVar("DatasetAssemblyResultT", covariant=True)

DatasetValidationRequestT = TypeVar("DatasetValidationRequestT", contravariant=True)
DatasetValidationResultT = TypeVar("DatasetValidationResultT", covariant=True)

DatasetProfileRequestT = TypeVar("DatasetProfileRequestT", contravariant=True)
DatasetProfileResultT = TypeVar("DatasetProfileResultT", covariant=True)


@runtime_checkable
class DatasetAssembler(
    Protocol,
    Generic[DatasetAssemblyRequestT, DatasetAssemblyResultT],
):
    """Port-like contract for assembling a dataset version from scene manifests.

    This does not ingest raw sensor logs. It builds or updates a dataset
    manifest as a versioned collection of scene manifest references.
    """

    @property
    def assembler_id(self) -> str:
        """Stable assembler identifier, e.g. scene-dataset-assembler."""

    async def run(
        self,
        request: DatasetAssemblyRequestT,
    ) -> DatasetAssemblyResultT:
        """Assemble a dataset manifest from scene references."""


@runtime_checkable
class DatasetValidator(
    Protocol,
    Generic[DatasetValidationRequestT, DatasetValidationResultT],
):
    """Port-like contract for dataset-level quality validation."""

    @property
    def validator_id(self) -> str:
        """Stable validator identifier, e.g. dataset-manifest-validator."""

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
    """Port-like contract for dataset-level profiling."""

    @property
    def profiler_id(self) -> str:
        """Stable profiler identifier, e.g. standard-dataset-profiler."""

    async def run(
        self,
        request: DatasetProfileRequestT,
    ) -> DatasetProfileResultT:
        """Profile a dataset version and return a profile report."""
