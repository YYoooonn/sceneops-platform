from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from sceneops_core.datasets.contracts import DatasetProfiler as CoreDatasetProfiler
from sceneops_core.datasets.schemas import (
    DatasetManifest,
    DatasetProfileReport,
    DatasetProfileScope,
)
from sceneops_worker.datasets import DatasetArtifactStore


@dataclass(frozen=True)
class DatasetProfileRequest:
    profile_run_id: str
    job_id: str
    dataset_id: str
    dataset_version: str
    dataset_manifest_uri: str
    dataset_manifest: DatasetManifest
    dataset_artifact_store: DatasetArtifactStore
    required_channels: list[str]
    scope: DatasetProfileScope
    max_samples: int | None = None
    profile_samples: bool = True
    profile_annotations: bool = True
    profile_sensor_coverage: bool = True
    profile_scene_distribution: bool = True


DatasetProfileResult: TypeAlias = DatasetProfileReport

DatasetProfiler: TypeAlias = CoreDatasetProfiler[
    DatasetProfileRequest,
    DatasetProfileResult,
]
