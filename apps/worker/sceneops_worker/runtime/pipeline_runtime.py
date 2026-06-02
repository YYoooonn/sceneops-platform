from __future__ import annotations

from dataclasses import dataclass

from sceneops_core.pipelines.schemas import PipelineRunManifest
from sceneops_worker.pipelines.factory import create_pipeline_runner
from sceneops_worker.registry.runtime import create_runtime_store_registry


@dataclass(frozen=True)
class PipelineRuntime:
    worker_id: str

    async def run_pipeline(
        self,
        *,
        pipeline_run_id: str,
    ) -> PipelineRunManifest:
        registry = create_runtime_store_registry()

        runner = create_pipeline_runner(
            registry=registry,
            worker_id=self.worker_id,
        )

        return await runner.run(
            pipeline_run_id=pipeline_run_id,
        )
