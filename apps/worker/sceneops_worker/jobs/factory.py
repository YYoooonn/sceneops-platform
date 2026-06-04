from __future__ import annotations

from sceneops_worker.jobs.context import JobContext
from sceneops_worker.jobs.registry import create_default_job_handler_registry
from sceneops_worker.jobs.runner import JobRunner
from sceneops_worker.registry.runtime import (
    RuntimeStoreRegistry,
    create_runtime_store_registry,
)


def create_job_context(
    *,
    registry: RuntimeStoreRegistry,
    worker_id: str | None = None,
) -> JobContext:
    settings = registry.settings

    return JobContext(
        worker_id=worker_id or settings.worker_id,
        artifact_store=registry.artifact_store,
        dataset_artifact_store=registry.dataset_artifact_store,
        run_artifact_store=registry.run_artifact_store,
        dataset_registry_store=registry.dataset_registry_store,
        model_registry_store=registry.model_registry_store,
        run_registry_store=registry.run_registry_store,
        scene_registry_store=registry.scene_registry_store,
        default_dataset_id=settings.default_dataset_id,
        default_dataset_version=settings.default_dataset_version,
    )


def create_job_runner(
    *,
    registry: RuntimeStoreRegistry | None = None,
    worker_id: str | None = None,
) -> JobRunner:
    registry = registry or create_runtime_store_registry()

    context = create_job_context(
        registry=registry,
        worker_id=worker_id,
    )

    return JobRunner(
        job_store=registry.job_store,
        job_event_store=registry.job_event_store,
        context=context,
        handler_registry=create_default_job_handler_registry(),
    )
