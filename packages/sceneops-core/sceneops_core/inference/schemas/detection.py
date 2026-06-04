from __future__ import annotations

from typing import Any

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel
from sceneops_core.datasets.schemas import DatasetManifest


class DetectionInferenceConfig(SceneOpsBaseModel):
    model_id: str
    model_version: str

    inference_backend: str = "mock"

    max_samples: int | None = None

    model_uri: str | None = None
    endpoint_url: str | None = None

    metadata: JsonDict = Field(default_factory=dict)


class DetectionInferenceInput(SceneOpsBaseModel):
    run_id: str
    config: DetectionInferenceConfig
    dataset_manifest: DatasetManifest


class DetectionInferenceResult(SceneOpsBaseModel):
    run_id: str

    run_manifest_uri: str | None = None
    predictions_root_uri: str | None = None

    sample_count: int = 0
    prediction_count: int = 0

    status: str

    metrics: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
