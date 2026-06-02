from __future__ import annotations

from collections.abc import Iterable

from sceneops_core.pipelines.schemas import PipelineDefinition, PipelineType


class PipelineDefinitionRegistry:
    def __init__(
        self,
        definitions: Iterable[PipelineDefinition],
    ) -> None:
        self._definitions: dict[PipelineType, PipelineDefinition] = {}

        for definition in definitions:
            self.register(definition)

    def register(self, definition: PipelineDefinition) -> None:
        if definition.type in self._definitions:
            raise ValueError(f"Duplicate pipeline definition: {definition.type}")

        self._definitions[definition.type] = definition

    def get(self, pipeline_type: PipelineType) -> PipelineDefinition:
        try:
            return self._definitions[pipeline_type]
        except KeyError as exc:
            raise ValueError(f"Unsupported pipeline type: {pipeline_type}") from exc

    def list(self) -> list[PipelineDefinition]:
        return sorted(
            self._definitions.values(),
            key=lambda definition: definition.type.value,
        )
