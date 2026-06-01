from __future__ import annotations

from sceneops_core.common.schemas import JsonDict
from sceneops_core.jobs.schemas import JobManifest, JobType
from sceneops_worker.jobs.handlers import JobHandler


class JobExecutor:
    def __init__(
        self,
        *,
        handlers: dict[JobType, JobHandler],
    ) -> None:
        self.handlers = handlers

    async def execute(self, job: JobManifest) -> JsonDict:
        handler = self.handlers.get(job.type)

        if handler is None:
            raise ValueError(f"Unsupported job type: {job.type}")

        return await handler.execute(job)
