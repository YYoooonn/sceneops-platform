from __future__ import annotations

from dataclasses import dataclass

from sceneops_worker.jobs.factory import create_job_runner
from sceneops_worker.registry import JobRegistryStore


@dataclass(frozen=True)
class JobRuntime:
    worker_id: str

    async def run_job(
        self,
        *,
        job_id: str,
    ):
        runner = create_job_runner(
            job_store=JobRegistryStore(),
            worker_id=self.worker_id,
        )

        return await runner.run(
            job_id=job_id,
        )
