from __future__ import annotations

from sceneops_core.common.schemas import JsonDict
from sceneops_core.pipelines.schemas import PipelineTaskRunManifest
from sceneops_worker.jobs.registry import (
    JobHandlerRegistry,
    create_default_job_handler_registry,
)
from sceneops_worker.pipelines.context import PipelineExecutionContext


class PipelineResultPropagator:
    """Applies completed-task results back into the pipeline execution context.

    Context propagation is delegated to each handler via
    ``extract_context_updates(result)``. Adding a new JobType only requires
    implementing that method on the new handler — this class never needs to change.
    """

    def __init__(
        self,
        handler_registry: JobHandlerRegistry | None = None,
    ) -> None:
        self._registry = handler_registry or create_default_job_handler_registry()

    def apply_task_result(
        self,
        *,
        task: PipelineTaskRunManifest,
        result: JsonDict,
        context: PipelineExecutionContext,
    ) -> None:
        handler = self._registry.get(task.job_type)
        updates = handler.extract_context_updates(result)
        for key, value in updates.items():
            context.set(key, value)
