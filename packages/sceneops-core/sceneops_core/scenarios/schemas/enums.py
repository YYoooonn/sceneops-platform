from __future__ import annotations

from enum import StrEnum


class ScenarioStatus(StrEnum):
    CANDIDATE = "candidate"
    SELECTED = "selected"
    REJECTED = "rejected"
    EXPORTED = "exported"
    DEPRECATED = "deprecated"


class ScenarioSourceType(StrEnum):
    SCENE = "scene"
    DATASET = "dataset"
    RUN = "run"
    MANUAL = "manual"
    GENERATED = "generated"


class ScenarioPredicateType(StrEnum):
    TAG = "tag"
    CATEGORY = "category"
    SENSOR_CHANNEL = "sensor_channel"
    TIME_RANGE = "time_range"
    OBJECT_COUNT = "object_count"
    OBJECT_DISTANCE = "object_distance"
    EGO_SPEED = "ego_speed"
    WEATHER = "weather"
    LIGHTING = "lighting"
    CUSTOM = "custom"


class ScenarioSelectionStrategy(StrEnum):
    TOP_K = "top_k"
    THRESHOLD = "threshold"
    BALANCED = "balanced"
    DIVERSITY = "diversity"
    MANUAL = "manual"
