from __future__ import annotations

from sceneops_core.datasets.schemas import (
    AndPredicateConfig,
    AnyPredicateConfig,
    EgoKinematicPredicateConfig,
    ObjectNeighborhoodPredicateConfig,
    OrPredicateConfig,
)
from sceneops_worker.datasets.scene_building.predicates.base import ScenePredicate
from sceneops_worker.datasets.scene_building.predicates.composite import (
    AndPredicate,
    OrPredicate,
)
from sceneops_worker.datasets.scene_building.predicates.ego_kinematic import (
    EgoKinematicPredicate,
)
from sceneops_worker.datasets.scene_building.predicates.object_neighborhood import (
    ObjectNeighborhoodPredicate,
)


def build_predicate(config: AnyPredicateConfig) -> ScenePredicate:
    if isinstance(config, ObjectNeighborhoodPredicateConfig):
        return ObjectNeighborhoodPredicate(
            required_categories=frozenset(config.required_categories),
            excluded_categories=frozenset(config.excluded_categories),
            max_distance_m=config.max_distance_m,
            min_count=config.min_count,
            max_count=config.max_count,
        )

    if isinstance(config, EgoKinematicPredicateConfig):
        return EgoKinematicPredicate(
            speed_min_kmh=config.speed_min_kmh,
            speed_max_kmh=config.speed_max_kmh,
            decel_min_ms2=config.decel_min_ms2,
        )

    if isinstance(config, AndPredicateConfig):
        return AndPredicate(
            predicates=tuple(build_predicate(p) for p in config.predicates)
        )

    if isinstance(config, OrPredicateConfig):
        return OrPredicate(
            predicates=tuple(build_predicate(p) for p in config.predicates)
        )

    raise ValueError(f"Unknown predicate config type: {type(config).__name__}")
