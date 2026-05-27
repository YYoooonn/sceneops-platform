from __future__ import annotations

from sceneops_core.schemas.jobs import JobType
from sceneops_worker.runtime.context import JobContext
from sceneops_worker.jobs.handlers.base import JobHandler
from sceneops_worker.jobs.handlers.evaluate_detection import EvaluateDetectionJobHandler
from sceneops_worker.jobs.handlers.ingest_dataset import IngestDatasetJobHandler
from sceneops_worker.jobs.handlers.predict_detection import PredictDetectionJobHandler
from sceneops_worker.jobs.handlers.validate_dataset_manifest import (
    ValidateDatasetManifestJobHandler,
)


def build_job_handler_registry(
    context: JobContext,
) -> dict[JobType, JobHandler]:
    handlers: list[JobHandler] = [
        IngestDatasetJobHandler(context),
        ValidateDatasetManifestJobHandler(context),
        PredictDetectionJobHandler(context),
        EvaluateDetectionJobHandler(context),
    ]

    return {handler.job_type: handler for handler in handlers}
