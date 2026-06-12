from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel

from .enums import MetricDirection


class EvaluationMetricSpec(SceneOpsBaseModel):
    key: str
    label: str
    direction: MetricDirection
    unit: str | None = None
    description: str | None = None

    metadata: JsonDict = Field(default_factory=dict)


class EvaluationMetricValue(SceneOpsBaseModel):
    key: str
    value: float | int | str | None = None
    direction: MetricDirection | None = None
    rankable: bool = True

    metadata: JsonDict = Field(default_factory=dict)
