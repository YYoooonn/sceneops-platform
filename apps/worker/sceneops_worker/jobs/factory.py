from __future__ import annotations

from sceneops_worker.config import get_settings
from sceneops_worker.jobs.context import JobContext
from sceneops_worker.jobs.event_store import PostgresJobEventStore
from sceneops_worker.jobs.executor import JobExecutor
from sceneops_worker.jobs.handlers import build_job_handler_registry
from sceneops_worker.jobs.runner import JobRunner
from sceneops_worker.jobs.store import JobStore, PostgresJobStore


def create_job_execution_context() -> JobContext:
    settings = get_settings()

    return JobContext(
        raw_data_root=settings.raw_data_root,
        manifest_root=settings.manifest_root,
        artifact_root=settings.artifact_root,
        runs_root=settings.runs_root,
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
