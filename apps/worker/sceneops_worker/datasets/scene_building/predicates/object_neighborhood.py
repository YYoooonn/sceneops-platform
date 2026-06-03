from __future__ import annotations

from dataclasses import dataclass

from sceneops_worker.datasets.scene_building.semantic_models import IndexedKeyframe


@dataclass(frozen=True)
class ObjectNeighborhoodPredicate:
    """Matches keyframes based on the presence/absence of nearby object categories.

    Use cases:
    - Seed for "add pedestrian" intervention: excluded_categories={"pedestrian"}, near crosswalk
    - Seed for "remove vehicle" intervention: required_categories={"vehicle.car"}, max_count=1
    """

    required_categories: frozenset[str] = frozenset()
    excluded_categories: frozenset[str] = frozenset()
    max_distance_m: float | None = None
    min_count: int = 1
    max_count: int | None = None

    def evaluate(self, keyframe: IndexedKeyframe) -> bool:
        objects = keyframe.nearby_objects
        if self.max_distance_m is not None:
            objects = tuple(o for o in objects if o.distance_m <= self.max_distance_m)

        by_category: dict[str, int] = {}
        for obj in objects:
            by_category[obj.category_name] = by_category.get(obj.category_name, 0) + 1

        for cat in self.excluded_categories:
            if by_category.get(cat, 0) > 0:
                return False

        for cat in self.required_categories:
            count = by_category.get(cat, 0)
            if count < self.min_count:
                return False
            if self.max_count is not None and count > self.max_count:
                return False

        return True

    def describe(self) -> str:
        parts = []
        if self.required_categories:
            parts.append(f"requires={sorted(self.required_categories)}")
        if self.excluded_categories:
            parts.append(f"excludes={sorted(self.excluded_categories)}")
        if self.max_distance_m is not None:
            parts.append(f"within={self.max_distance_m}m")
        if self.min_count != 1:
            parts.append(f"min_count={self.min_count}")
        if self.max_count is not None:
            parts.append(f"max_count={self.max_count}")
        return f"ObjectNeighborhood({', '.join(parts)})"
