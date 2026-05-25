from __future__ import annotations

from sceneops_core.schemas.jobs import JobType
from sceneops_worker.jobs.context import JobExecutionContext
from sceneops_worker.jobs.handlers.base import JobHandler
from sceneops_worker.jobs.handlers.evaluate_detection import EvaluateDetectionJobHandler
from sceneops_worker.jobs.handlers.ingest_dataset import IngestDatasetJobHandler
from sceneops_worker.jobs.handlers.predict_detection import PredictDetectionJobHandler


def build_job_handler_registry(
    context: JobExecutionContext,
) -> dict[JobType, JobHandler]:
    handlers: list[JobHandler] = [
        IngestDatasetJobHandler(context),
        PredictDetectionJobHandler(context),
        EvaluateDetectionJobHandler(context),
    ]

    return {handler.job_type: handler for handler in handlers}
