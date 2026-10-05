from __future__ import annotations

from typing import Any

from pydantic import Field

from sceneops_core.common.schemas import SceneOpsBaseModel
from sceneops_core.sample_views.schemas import SampleViewRef
from sceneops_core.scenarios.schemas.manifests import ScenarioSetRef

from .manifests import PredictionInputRef


class DetectionInferenceConfig(SceneOpsBaseModel):
    """The explicit, identity-bearing configuration of one detection run.
    Runtime locators (model location, endpoint) are not configuration: they
    say where to run, not what was run, and live on the run input."""

    model_id: str
    model_version: str
    inference_backend: str

    # The channel whose observation is the detector's image. Always explicit:
    # channel names belong to the recording, never to the platform.
    camera_channel: str
    # The channel whose point cloud lifts 2-D boxes to 3-D. None disables lifting.
    lidar_channel: str | None = None

    detection_prompt: str | None = None
    box_threshold: float | None = None
    text_threshold: float | None = None
    max_image_size: int | None = None

    max_samples: int | None = None


class DetectionInferenceInput(SceneOpsBaseModel):
    run_id: str
    config: DetectionInferenceConfig

    dataset_id: str
    dataset_version: str

    # Exactly which samples of which pinned views run.
    inputs: list[PredictionInputRef]
    scenario_set: ScenarioSetRef | None = None

    # Runtime locators.
    model_uri: str | None = None
    endpoint_url: str | None = None

    def sample_views(self) -> list[SampleViewRef]:
        return [item.sample_view for item in self.inputs]


class DetectionInferenceResult(SceneOpsBaseModel):
    run_id: str

    # The published prediction manifest revision.
    prediction_manifest_uri: str
    prediction_manifest_checksum: str
    predictions_root_uri: str | None = None

    scene_count: int = 0
    sample_count: int = 0
    inference_request_count: int = 0
    prediction_count: int = 0
    evaluable_prediction_count: int = 0
    lifting_succeeded_count: int = 0
    lifting_failed_count: int = 0

    status: str

    metrics: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
