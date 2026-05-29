from __future__ import annotations

from sceneops_worker.config import get_settings
from sceneops_worker.datasets import DatasetArtifactStore
from sceneops_worker.jobs.event_store import PostgresJobEventStore
from sceneops_worker.jobs.executor import JobExecutor
from sceneops_worker.jobs.handlers import build_job_handler_registry
from sceneops_worker.jobs.runner import JobRunner
from sceneops_worker.jobs.store import JobStore, PostgresJobStore
from sceneops_worker.registry import (
    DatasetRegistryStore,
    ModelRegistryStore,
    RunRegistryStore,
)
from sceneops_worker.runs import RunArtifactStore
from sceneops_worker.runtime.context import JobContext
from sceneops_storage import create_artifact_store


def create_job_execution_context() -> JobContext:
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
        artifact_store=artifact_store,
        dataset_artifact_store=dataset_artifact_store,
        dataset_registry_store=DatasetRegistryStore(),
        model_registry_store=ModelRegistryStore(),
        run_registry_store=RunRegistryStore(),
        run_artifact_store=run_artifact_store,
        default_dataset_id=settings.default_dataset_id,
        default_dataset_version=settings.default_dataset_version,
    )


def create_job_executor() -> JobExecutor:
    context = create_job_execution_context()

    return JobExecutor(
        handlers=build_job_handler_registry(context),
    )


def create_job_runner(
    *,
    job_store: JobStore | None = None,
) -> JobRunner:
    settings = get_settings()

    return JobRunner(
        job_store=job_store or PostgresJobStore(),
        job_executor=create_job_executor(),
        job_event_store=PostgresJobEventStore(),
        worker_id=settings.worker_id,
    )
