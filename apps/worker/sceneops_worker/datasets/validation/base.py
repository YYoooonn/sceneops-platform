from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from sceneops_core.datasets.contracts import DatasetValidator as CoreDatasetValidator
from sceneops_core.datasets.schemas import (
    DatasetManifest,
    DatasetValidationReport,
)
from sceneops_worker.datasets import DatasetArtifactStore


@dataclass(frozen=True)
class DatasetValidationRequest:
    validation_run_id: str
    job_id: str
    dataset_id: str
    dataset_version: str
    dataset_manifest_uri: str
    dataset_manifest: DatasetManifest
    dataset_artifact_store: DatasetArtifactStore
    require_target_channels: list[str]
    validate_samples: bool = True
    validate_sensor_artifacts: bool = False
    validate_annotations: bool = False
    validate_calibration: bool = False
    max_samples: int | None = None


DatasetValidationResult: TypeAlias = DatasetValidationReport

DatasetValidator: TypeAlias = CoreDatasetValidator[
    DatasetValidationRequest,
    DatasetValidationResult,
]
