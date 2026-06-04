from __future__ import annotations

from typing import Literal

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel

from .enums import ScenarioPredicateType


class ScenarioPredicate(SceneOpsBaseModel):
    predicate_id: str | None = None

    type: ScenarioPredicateType
    name: str

    params: JsonDict = Field(default_factory=dict)

    weight: float = 1.0
    enabled: bool = True

    metadata: JsonDict = Field(default_factory=dict)


class TagPredicate(SceneOpsBaseModel):
    type: Literal[ScenarioPredicateType.TAG] = ScenarioPredicateType.TAG
    tags: list[str] = Field(default_factory=list)


class CategoryPredicate(SceneOpsBaseModel):
    type: Literal[ScenarioPredicateType.CATEGORY] = ScenarioPredicateType.CATEGORY
    categories: list[str] = Field(default_factory=list)


class SensorChannelPredicate(SceneOpsBaseModel):
    type: Literal[ScenarioPredicateType.SENSOR_CHANNEL] = (
        ScenarioPredicateType.SENSOR_CHANNEL
    )
    required_channels: list[str] = Field(default_factory=list)


class TimeRangePredicate(SceneOpsBaseModel):
    type: Literal[ScenarioPredicateType.TIME_RANGE] = ScenarioPredicateType.TIME_RANGE
    start_timestamp_us: int | None = None
    end_timestamp_us: int | None = None


class ObjectCountPredicate(SceneOpsBaseModel):
    type: Literal[ScenarioPredicateType.OBJECT_COUNT] = (
        ScenarioPredicateType.OBJECT_COUNT
    )

    category: str | None = None
    min_count: int | None = None
    max_count: int | None = None


class EgoSpeedPredicate(SceneOpsBaseModel):
    type: Literal[ScenarioPredicateType.EGO_SPEED] = ScenarioPredicateType.EGO_SPEED

    min_speed_mps: float | None = None
    max_speed_mps: float | None = None


class CustomPredicate(SceneOpsBaseModel):
    type: Literal[ScenarioPredicateType.CUSTOM] = ScenarioPredicateType.CUSTOM

    expression: str | None = None
    params: JsonDict = Field(default_factory=dict)


PredicateConfig = (
    TagPredicate
    | CategoryPredicate
    | SensorChannelPredicate
    | TimeRangePredicate
    | ObjectCountPredicate
    | EgoSpeedPredicate
    | CustomPredicate
)
