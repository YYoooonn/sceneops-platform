from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from sceneops_core.datasets.contracts import DatasetIngestor as CoreDatasetIngestor
from sceneops_core.datasets.schemas import DatasetIngestMode, DatasetManifest
from sceneops_worker.datasets import DatasetArtifactStore


@dataclass(frozen=True)
class DatasetIngestionRequest:
    dataset_id: str
    dataset_version: str
    source_uri: str
    dataset_artifact_store: DatasetArtifactStore
    max_scenes: int | None = None
    mode: DatasetIngestMode = DatasetIngestMode.UPSERT


DatasetIngestionResult: TypeAlias = DatasetManifest

DatasetIngestor: TypeAlias = CoreDatasetIngestor[
    DatasetIngestionRequest,
    DatasetIngestionResult,
]
