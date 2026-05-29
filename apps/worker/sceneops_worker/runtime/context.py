from __future__ import annotations

from dataclasses import dataclass

from sceneops_worker.datasets import DatasetArtifactStore
from sceneops_worker.registry import (
    DatasetRegistryStore,
    ModelRegistryStore,
    RunRegistryStore,
)
from sceneops_worker.runs import RunArtifactStore
from sceneops_storage import ArtifactStore


@dataclass(frozen=True)
class JobContext:
    artifact_store: ArtifactStore
    dataset_artifact_store: DatasetArtifactStore
    dataset_registry_store: DatasetRegistryStore
    model_registry_store: ModelRegistryStore
    run_registry_store: RunRegistryStore
    run_artifact_store: RunArtifactStore

    default_dataset_id: str
    default_dataset_version: str
