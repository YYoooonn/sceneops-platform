from __future__ import annotations

from dataclasses import dataclass

from sceneops_worker.jobs.factory import create_job_runner
from sceneops_worker.registry import JobRegistryStore
from sceneops_worker.pipelines.runner import PipelineRunner
from sceneops_worker.pipelines.store import PostgresPipelineStore


@dataclass(frozen=True)
class PipelineRuntime:
    worker_id: str

    async def run_pipeline(
        self,
        *,
        pipeline_run_id: str,
    ):
        pipeline_store = PostgresPipelineStore()
        job_store = JobRegistryStore()

        job_runner = create_job_runner(
            job_store=job_store,
            worker_id=self.worker_id,
        )

        runner = PipelineRunner(
            pipeline_store=pipeline_store,
            job_store=job_store,
            job_runner=job_runner,
        )

        return await runner.run(
            pipeline_run_id=pipeline_run_id,
        )
