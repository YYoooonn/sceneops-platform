from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from sceneops_core.schemas.base import SceneOpsBaseModel
from sceneops_core.schemas.common import JsonDict
from sceneops_core.schemas.datasets import DatasetType
from sceneops_core.schemas.jobs.enums import JobType


class IngestMode(StrEnum):
    UPSERT = "upsert"
    OVERWRITE = "overwrite"


class InferenceBackend(StrEnum):
    MOCK = "mock"
    ONNX_RUNTIME = "onnx_runtime"
    # TRITON = "triton"


class BaseJobParams(SceneOpsBaseModel):
    dataset_id: str
    dataset_version: str
    extra: JsonDict = Field(default_factory=dict)


class IngestDatasetJobParams(BaseJobParams):
    dataset_type: DatasetType = DatasetType.NUSCENES

    source_uri: str | None = None
    max_scenes: int | None = None
    mode: IngestMode = IngestMode.UPSERT


class ValidateDatasetJobParams(BaseJobParams):
    require_target_channels: list[str] = Field(
        default_factory=lambda: ["CAM_FRONT", "LIDAR_TOP"]
    )

    validate_samples: bool = True
    validate_sensor_artifacts: bool = True
    validate_annotations: bool = True
    validate_calibration: bool = False
    validate_timestamps: bool = False

    max_samples: int | None = None

    fail_on_missing_required_channels: bool = True
    fail_on_missing_samples: bool = True
    fail_on_missing_sensor_artifacts: bool = False


class ProfileDatasetJobParams(BaseJobParams):
    require_target_channels: list[str] = Field(
        default_factory=lambda: ["CAM_FRONT", "LIDAR_TOP"]
    )

    profile_samples: bool = True
    profile_annotations: bool = True
    profile_sensor_coverage: bool = True
    profile_scene_distribution: bool = True

    max_samples: int | None = None


class PredictDetectionJobParams(BaseJobParams):
    model_id: str
    model_version: str
    inference_backend: InferenceBackend = InferenceBackend.MOCK

    inference_run_id: str | None = None
    max_samples: int | None = None

    model_uri: str | None = None
    endpoint_url: str | None = None


class EvaluateDetectionJobParams(BaseJobParams):
    inference_run_id: str
    evaluation_run_id: str | None = None

    evaluator_id: str = "center-distance"
    match_distance_m: float = 2.0


JobParams = (
    IngestDatasetJobParams
    | ValidateDatasetJobParams
    | ProfileDatasetJobParams
    | PredictDetectionJobParams
    | EvaluateDetectionJobParams
)


def parse_job_params(job_type: JobType, params: JsonDict) -> JobParams:
    if job_type == JobType.INGEST_DATASET:
        return IngestDatasetJobParams.model_validate(params)

    if job_type == JobType.PROFILE_DATASET:
        return ProfileDatasetJobParams.model_validate(params)

    if job_type == JobType.VALIDATE_DATASET:
        return ValidateDatasetJobParams.model_validate(params)

    if job_type == JobType.PREDICT_DETECTION:
        return PredictDetectionJobParams.model_validate(params)

    if job_type == JobType.EVALUATE_DETECTION:
        return EvaluateDetectionJobParams.model_validate(params)

    raise ValueError(f"Unsupported job type: {job_type}")
