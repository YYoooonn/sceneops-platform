from __future__ import annotations

from sceneops_worker.jobs.factory import create_job_runner
from sceneops_worker.pipelines.planning import PipelineJobPlanner
from sceneops_worker.pipelines.propagation import PipelineResultPropagator
from sceneops_worker.pipelines.quality_gate import PipelineQualityGate
from sceneops_worker.pipelines.runner import PipelineRunner
from sceneops_worker.pipelines.step_executor import PipelineStepExecutor
from sceneops_worker.registry.runtime import (
    RuntimeStoreRegistry,
    create_runtime_store_registry,
)


def create_pipeline_runner(
    *,
    registry: RuntimeStoreRegistry | None = None,
    worker_id: str | None = None,
) -> PipelineRunner:
    registry = registry or create_runtime_store_registry()

    job_runner = create_job_runner(
        registry=registry,
        worker_id=worker_id,
    )

    step_executor = PipelineStepExecutor(
        pipeline_store=registry.pipeline_store,
        job_store=registry.job_store,
        job_runner=job_runner,
        planner=PipelineJobPlanner(),
        propagator=PipelineResultPropagator(),
        quality_gate=PipelineQualityGate(),
    )

    return PipelineRunner(
        pipeline_store=registry.pipeline_store,
        step_executor=step_executor,
    )
