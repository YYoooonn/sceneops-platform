from __future__ import annotations

from collections.abc import Iterable

from sceneops_core.jobs.schemas import JobType
from sceneops_worker.jobs.base import AnyJobHandler
from sceneops_worker.jobs.dataset import (
    BuildDatasetManifestJobHandler,
    BuildSceneIndexJobHandler,
    BuildScenesJobHandler,
    ExportAnalyticsSnapshotJobHandler,
    IngestScenesJobHandler,
    ProfileSceneJobHandler,
    RegisterSceneJobHandler,
    ValidateSceneJobHandler,
)
from sceneops_worker.jobs.evaluation import EvaluateDetectionJobHandler
from sceneops_worker.jobs.inference import PredictDetectionJobHandler
from sceneops_worker.jobs.scenarios import (
    MineScenariosJobHandler,
    ScoreScenarioReadinessJobHandler,
)


class JobHandlerRegistry:
    def __init__(self, handlers: Iterable[AnyJobHandler]) -> None:
        self._handlers: dict[JobType, AnyJobHandler] = {}

        for handler in handlers:
            self.register(handler)

    def register(self, handler: AnyJobHandler) -> None:
        job_type = handler.job_type

        if job_type in self._handlers:
            raise ValueError(f"Duplicate job handler registered: {job_type}")

        self._handlers[job_type] = handler

    def get(self, job_type: JobType) -> AnyJobHandler:
        try:
            return self._handlers[job_type]
        except KeyError as exc:
            raise ValueError(f"Unsupported job type: {job_type}") from exc

    def list_job_types(self) -> list[JobType]:
        return sorted(self._handlers.keys(), key=lambda item: item.value)


def create_default_job_handler_registry() -> JobHandlerRegistry:
    return JobHandlerRegistry(
        handlers=[
            IngestScenesJobHandler(),
            RegisterSceneJobHandler(),
            BuildSceneIndexJobHandler(),
            BuildScenesJobHandler(),
            ValidateSceneJobHandler(),
            ProfileSceneJobHandler(),
            BuildDatasetManifestJobHandler(),
            ExportAnalyticsSnapshotJobHandler(),
            PredictDetectionJobHandler(),
            EvaluateDetectionJobHandler(),
            MineScenariosJobHandler(),
            ScoreScenarioReadinessJobHandler(),
        ]
    )
