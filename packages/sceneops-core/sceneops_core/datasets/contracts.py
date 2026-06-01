# packages/sceneops-core/sceneops_core/datasets/contracts.py

from __future__ import annotations

from typing import Protocol, runtime_checkable

from sceneops_core.common.types import DatasetId, DatasetVersion, JsonDict, Metadata


@runtime_checkable
class DatasetSource(Protocol):
    """Adapter contract for robotics dataset sources.

    Examples:
    - nuScenes
    - ROS bag logs
    - custom robot logs
    - simulation-generated datasets
    """

    @property
    def dataset_type(self) -> str:
        ...

    def load_scenes(
        self,
        *,
        dataset_id: DatasetId,
        dataset_version: DatasetVersion,
        params: Metadata,
    ) -> list[JsonDict]:
        ...

    def load_samples(
        self,
        *,
        dataset_id: DatasetId,
        dataset_version: DatasetVersion,
        scene_id: str,
        params: Metadata,
    ) -> list[JsonDict]:
        ...

    def load_annotations(
        self,
        *,
        dataset_id: DatasetId,
        dataset_version: DatasetVersion,
        sample_id: str,
        params: Metadata,
    ) -> list[JsonDict]:
        ...


@runtime_checkable
class DatasetIngestor(Protocol):
    """Contract for converting raw dataset files into SceneOps manifests."""

    def ingest(
        self,
        *,
        dataset_id: DatasetId,
        dataset_version: DatasetVersion,
        params: Metadata,
    ) -> JsonDict:
        ...


@runtime_checkable
class DatasetValidator(Protocol):
    """Contract for dataset quality gate validation."""

    def validate(
        self,
        *,
        dataset_id: DatasetId,
        dataset_version: DatasetVersion,
        params: Metadata,
    ) -> JsonDict:
        ...


@runtime_checkable
class DatasetProfiler(Protocol):
    """Contract for dataset profiling and statistics."""

    def profile(
        self,
        *,
        dataset_id: DatasetId,
        dataset_version: DatasetVersion,
        params: Metadata,
    ) -> JsonDict:
        ...
