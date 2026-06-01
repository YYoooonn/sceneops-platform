from __future__ import annotations

from dataclasses import dataclass

from sceneops_core.artifacts.contracts import ArtifactStore
from sceneops_worker.datasets import DatasetArtifactStore
from sceneops_worker.registry import JobEventStore, JobStore
from sceneops_worker.registry import (
    DatasetRegistryStore,
    ModelRegistryStore,
    RunRegistryStore,
)
from sceneops_worker.runs import RunArtifactStore


@dataclass(frozen=True)
class JobContext:
    worker_id: str

    artifact_store: ArtifactStore
    dataset_artifact_store: DatasetArtifactStore
    run_artifact_store: RunArtifactStore

    dataset_registry_store: DatasetRegistryStore
    model_registry_store: ModelRegistryStore
    run_registry_store: RunRegistryStore

    job_store: JobStore
    job_event_store: JobEventStore

    default_dataset_id: str
    default_dataset_version: str
