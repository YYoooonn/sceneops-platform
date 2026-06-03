from __future__ import annotations

from dataclasses import dataclass

from sceneops_worker.datasets.scene_building.predicates.base import ScenePredicate
from sceneops_worker.datasets.scene_building.semantic_models import IndexedKeyframe


@dataclass(frozen=True)
class AndPredicate:
    predicates: tuple[ScenePredicate, ...] = ()

    def evaluate(self, keyframe: IndexedKeyframe) -> bool:
        return all(p.evaluate(keyframe) for p in self.predicates)

    def describe(self) -> str:
        return f"AND({', '.join(p.describe() for p in self.predicates)})"


@dataclass(frozen=True)
class OrPredicate:
    predicates: tuple[ScenePredicate, ...] = ()

    def evaluate(self, keyframe: IndexedKeyframe) -> bool:
        return any(p.evaluate(keyframe) for p in self.predicates)

    def describe(self) -> str:
        return f"OR({', '.join(p.describe() for p in self.predicates)})"
