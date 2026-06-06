from __future__ import annotations

from collections.abc import Iterable

from sceneops_core.pipelines.schemas import PipelineDefinition, PipelineType


def _validate_pipeline_definition(definition: PipelineDefinition) -> None:
    task_ids = {task.pipeline_task_id for task in definition.tasks}

    if len(task_ids) != len(definition.tasks):
        raise ValueError(f"Duplicate pipeline_task_id in pipeline: {definition.type}")

    for task in definition.tasks:
        for dependency in task.depends_on_pipeline_task_ids:
            if dependency not in task_ids:
                raise ValueError(
                    f"Invalid dependency in pipeline={definition.type}: "
                    f"task={task.pipeline_task_id}, dependency={dependency}"
                )


class PipelineDefinitionRegistry:
    """In-memory registry for pipeline definitions.

    This registry maps a PipelineType to a PipelineDefinition.
    It is intended for built-in/static pipeline definitions. Runtime pipeline
    run state should be stored separately as PipelineRunManifest records.
    """

    def __init__(
        self,
        definitions: Iterable[PipelineDefinition] | None = None,
    ) -> None:
        self._definitions: dict[PipelineType, PipelineDefinition] = {}

        if definitions is not None:
            self.register_many(definitions)

    def register(self, definition: PipelineDefinition) -> None:
        if definition.type in self._definitions:
            raise ValueError(f"Duplicate pipeline definition: {definition.type}")

        _validate_pipeline_definition(definition)

        self._definitions[definition.type] = definition

    def register_many(
        self,
        definitions: Iterable[PipelineDefinition],
    ) -> None:
        for definition in definitions:
            self.register(definition)

    def has(self, pipeline_type: PipelineType) -> bool:
        return pipeline_type in self._definitions

    def get_optional(
        self,
        pipeline_type: PipelineType,
    ) -> PipelineDefinition | None:
        return self._definitions.get(pipeline_type)

    def get(
        self,
        pipeline_type: PipelineType,
    ) -> PipelineDefinition:
        definition = self.get_optional(pipeline_type)

        if definition is None:
            raise ValueError(f"Unsupported pipeline type: {pipeline_type}")

        return definition

    def list(self) -> list[PipelineDefinition]:
        return sorted(
            self._definitions.values(),
            key=lambda definition: definition.type.value,
        )
