from __future__ import annotations

from dataclasses import dataclass

from sceneops_core.artifacts.contracts import ArtifactStore
from sceneops_storage import create_artifact_store

from sceneops_worker.config import WorkerSettings, get_settings
from sceneops_worker.datasets import DatasetArtifactStore
from sceneops_worker.registry.datasets import DatasetRegistryStore
from sceneops_worker.registry.jobs import (
    JobEventStore,
    JobStore,
    PostgresJobEventStore,
    PostgresJobStore,
)
from sceneops_worker.registry.models import ModelRegistryStore
from sceneops_worker.registry.pipelines import PipelineStore, PostgresPipelineStore
from sceneops_worker.registry.runs import RunRegistryStore
from sceneops_worker.runs import RunArtifactStore


@dataclass(frozen=True)
class RuntimeStoreRegistry:
    settings: WorkerSettings

    artifact_store: ArtifactStore
    dataset_artifact_store: DatasetArtifactStore
    run_artifact_store: RunArtifactStore

    job_store: JobStore
    job_event_store: JobEventStore
    pipeline_store: PipelineStore

    dataset_registry_store: DatasetRegistryStore
    model_registry_store: ModelRegistryStore
    run_registry_store: RunRegistryStore


def create_runtime_store_registry(
    *,
    settings: WorkerSettings | None = None,
) -> RuntimeStoreRegistry:
    settings = settings or get_settings()

    artifact_store = create_artifact_store(settings.artifact)

    dataset_artifact_store = DatasetArtifactStore(
        artifact_store=artifact_store,
        dataset_root_uri=settings.dataset_root_uri,
    )

    run_artifact_store = RunArtifactStore(
        artifact_store=artifact_store,
        runs_root_uri=settings.run_root_uri,
    )

    return RuntimeStoreRegistry(
        settings=settings,
        artifact_store=artifact_store,
        dataset_artifact_store=dataset_artifact_store,
        run_artifact_store=run_artifact_store,
        job_store=PostgresJobStore(),
        job_event_store=PostgresJobEventStore(),
        pipeline_store=PostgresPipelineStore(),
        dataset_registry_store=DatasetRegistryStore(),
        model_registry_store=ModelRegistryStore(),
        run_registry_store=RunRegistryStore(),
    )
