from __future__ import annotations

from dataclasses import dataclass

from sceneops_worker.datasets import DatasetArtifactStore
from sceneops_worker.registry import (
    DatasetRegistryStore,
    ModelRegistryStore,
    RunRegistryStore,
)
from sceneops_worker.runs import RunArtifactStore
from sceneops_worker.storage import ArtifactStore


@dataclass(frozen=True)
class JobContext:
    artifact_store: ArtifactStore
    dataset_artifact_store: DatasetArtifactStore
    dataset_registry_store: DatasetRegistryStore
    model_registry_store: ModelRegistryStore
    run_registry_store: RunRegistryStore
    run_artifact_store: RunArtifactStore

    raw_data_root_uri: str | None  # XXX to be removed
    manifest_root_uri: str
    runs_root_uri: str

    default_dataset_id: str
    default_dataset_version: str
