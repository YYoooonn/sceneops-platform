from __future__ import annotations

from typing import Any

from pydantic import Field

from sceneops_core.schemas.base import SceneOpsBaseModel
from sceneops_core.schemas.datasets import DatasetManifest
from sceneops_core.schemas.jobs.params import PredictDetectionJobParams


class DetectionInferenceInput(SceneOpsBaseModel):
    params: PredictDetectionJobParams
    dataset_manifest: DatasetManifest

    run_id: str
    model_uri: str | None = Field(default=None)
    endpoint_url: str | None = Field(default=None)


class DetectionInferenceResult(SceneOpsBaseModel):
    run_id: str
    run_manifest_uri: str
    predictions_root_uri: str

    sample_count: int
    prediction_count: int

    status: str
    metrics: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
