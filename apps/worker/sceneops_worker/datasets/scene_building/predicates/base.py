from __future__ import annotations

from typing import Protocol, runtime_checkable

from sceneops_worker.datasets.scene_building.semantic_models import IndexedKeyframe


@runtime_checkable
class ScenePredicate(Protocol):
    def evaluate(self, keyframe: IndexedKeyframe) -> bool: ...
    def describe(self) -> str: ...
