from __future__ import annotations

from typing import Any

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel
from sceneops_core.datasets.schemas import DatasetManifest


class DetectionInferenceConfig(SceneOpsBaseModel):
    model_id: str
    model_version: str

    inference_backend: str = "mock"

    model_uri: str | None = None
    endpoint_url: str | None = None
    raw_source_root_uri: str | None = None  # for image URI resolution in the worker

    # Sample / scene selection
    scene_ids: list[str] | None = None
    max_scenes: int | None = None
    max_samples: int | None = None

    # GroundingDINO / camera params (None → backend uses its own defaults)
    camera_channel: str = "CAM_FRONT"
    detection_prompt: str | None = None
    box_threshold: float | None = None
    text_threshold: float | None = None
    max_image_size: int | None = None
    enable_3d_lifting: bool = True

    metadata: JsonDict = Field(default_factory=dict)


class DetectionInferenceInput(SceneOpsBaseModel):
    run_id: str
    config: DetectionInferenceConfig
    dataset_manifest: DatasetManifest


class DetectionInferenceResult(SceneOpsBaseModel):
    run_id: str

    run_manifest_uri: str | None = None
    predictions_root_uri: str | None = None

    scene_count: int = 0
    sample_count: int = 0
    inference_request_count: int = 0
    prediction_count: int = 0

    status: str

    metrics: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
