from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict

from .base import BaseJobResult


class PredictDetectionJobResult(BaseJobResult):
    inference_run_id: str

    predictions_root_uri: str | None = None

    sample_count: int = 0

    metadata: JsonDict = Field(default_factory=dict)


class EvaluateDetectionJobResult(BaseJobResult):
    evaluation_run_id: str

    metrics_uri: str | None = None

    summary: JsonDict = Field(default_factory=dict)

    metadata: JsonDict = Field(default_factory=dict)
