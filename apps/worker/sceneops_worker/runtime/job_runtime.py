from __future__ import annotations

from dataclasses import dataclass

from sceneops_core.jobs.schemas import JobManifest
from sceneops_worker.jobs.factory import create_job_runner
from sceneops_worker.registry.runtime import create_runtime_store_registry


@dataclass(frozen=True)
class JobRuntime:
    worker_id: str

    async def run_job(
        self,
        *,
        job_id: str,
    ) -> JobManifest:
        registry = create_runtime_store_registry()

        runner = create_job_runner(
            registry=registry,
            worker_id=self.worker_id,
        )

        return await runner.run(job_id)
