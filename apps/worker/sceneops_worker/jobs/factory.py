from __future__ import annotations

from sceneops_worker.config import get_settings
from sceneops_worker.datasets import DatasetArtifactStore
from sceneops_worker.jobs.runner import JobRunner
from sceneops_worker.registry import (
    DatasetRegistryStore,
    ModelRegistryStore,
    JobEventRegistryStore,
    RunRegistryStore,
    JobStore,
    JobRegistryStore,
)
from sceneops_worker.runs import RunArtifactStore
from sceneops_worker.jobs.context import JobContext
from sceneops_storage import create_artifact_store


def create_job_context(
    worker_id: str | None,
    job_store: JobStore | None,
) -> JobContext:
    settings = get_settings()

    artifact_store = create_artifact_store(settings.artifact)

    dataset_artifact_store = DatasetArtifactStore(
        artifact_store=artifact_store,
        dataset_root_uri=settings.dataset_root_uri,
    )

    run_artifact_store = RunArtifactStore(
        artifact_store=artifact_store,
        runs_root_uri=settings.run_root_uri,
    )

    return JobContext(
        worker_id=worker_id or settings.worker_id,
        artifact_store=artifact_store,
        dataset_artifact_store=dataset_artifact_store,
        dataset_registry_store=DatasetRegistryStore(),
        model_registry_store=ModelRegistryStore(),
        run_registry_store=RunRegistryStore(),
        run_artifact_store=run_artifact_store,
        job_store=job_store or JobRegistryStore(),
        job_event_store=JobEventRegistryStore(),
        default_dataset_id=settings.default_dataset_id,
        default_dataset_version=settings.default_dataset_version,
    )


def create_job_runner(
    *, job_store: JobStore | None = None, worker_id: str | None = None
) -> JobRunner:
    context = create_job_context(
        worker_id=worker_id,
        job_store=job_store,
    )

    return JobRunner(context=context)
