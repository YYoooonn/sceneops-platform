from __future__ import annotations

from enum import StrEnum
from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel
from sceneops_core.inference.enums import InferenceBackendType

from .base import BaseJobParams


class DetectionSceneSelectionMode(StrEnum):
    ALL = "all"
    GROUND_TRUTH_ONLY = "ground_truth_only"
    EXPLICIT_SCENES = "explicit_scenes"


class DetectionSceneSelectionConfig(SceneOpsBaseModel):
    mode: DetectionSceneSelectionMode = DetectionSceneSelectionMode.ALL

    # mode == explicit_scenes
    scene_ids: list[str] = Field(default_factory=list)

    # mode == ground_truth_only
    min_annotation_count: int = 1
    ground_truth_sources: list[str] = Field(default_factory=list)

    # 공통 limit
    max_scenes: int | None = None
    max_samples: int | None = None
    max_samples_per_scene: int | None = None

    metadata: JsonDict = Field(default_factory=dict)


class PredictDetectionJobParams(BaseJobParams):
    dataset_id: str
    dataset_version: str

    model_id: str
    model_version: str

    dataset_manifest_uri: str | None = None
    scene_ids: list[str] | None = None
    scenario_set_id: str | None = None

    inference_backend: InferenceBackendType = InferenceBackendType.MOCK

    inference_run_id: str | None = None

    scene_selection: DetectionSceneSelectionConfig = Field(
        default_factory=DetectionSceneSelectionConfig
    )

    # model_uri: str | None = None
    # endpoint_url: str | None = None

    # GroundingDINO / camera params (None → backend uses its own defaults)
    camera_channel: str = "CAM_FRONT"
    detection_prompt: str | None = None
    box_threshold: float | None = None
    text_threshold: float | None = None
    max_image_size: int | None = None
    enable_3d_lifting: bool = True

    metadata: JsonDict = Field(default_factory=dict)


class MissingGroundTruthPolicy(StrEnum):
    FAIL = "fail"
    SKIP = "skip"


class EvaluateDetectionJobParams(BaseJobParams):
    dataset_id: str
    dataset_version: str

    inference_run_id: str
    scenario_set_id: str | None = None

    evaluation_run_id: str | None = None

    evaluator_id: str = "center-distance"
    match_distance_m: float = 2.0

    missing_gt_policy: MissingGroundTruthPolicy = MissingGroundTruthPolicy.SKIP

    metadata: JsonDict = Field(default_factory=dict)
