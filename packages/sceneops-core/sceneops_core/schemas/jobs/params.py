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
    # ONNX_RUNTIME = "onnx_runtime"
    # TRITON = "triton"


class IngestDatasetJobParams(SceneOpsBaseModel):
    dataset_id: str
    dataset_version: str
    dataset_type: DatasetType = DatasetType.NUSCENES

    raw_data_root: str | None = None
    max_scenes: int | None = None
    mode: IngestMode = IngestMode.UPSERT

    extra: JsonDict = Field(default_factory=dict)


class ValidateDatasetManifestJobParams(SceneOpsBaseModel):
    dataset_id: str
    dataset_version: str

    require_target_channels: list[str] = Field(default_factory=list)
    validate_samples: bool = True
    max_samples: int | None = None

    extra: JsonDict = Field(default_factory=dict)

class PredictDetectionJobParams(SceneOpsBaseModel):
    dataset_id: str
    dataset_version: str

    model_id: str
    model_version: str
    inference_backend: InferenceBackend = InferenceBackend.MOCK

    inference_run_id: str | None = None
    max_samples: int | None = None

    model_uri: str | None = None
    endpoint_url: str | None = None
    extra: JsonDict = Field(default_factory=dict)


class EvaluateDetectionJobParams(SceneOpsBaseModel):
    dataset_id: str
    dataset_version: str

    inference_run_id: str
    evaluation_run_id: str | None = None

    evaluator_id: str = "center-distance"
    match_distance_m: float = 2.0
    extra: JsonDict = Field(default_factory=dict)


JobParams = (
    IngestDatasetJobParams
    | ValidateDatasetManifestJobParams
    | PredictDetectionJobParams
    | EvaluateDetectionJobParams
)


def parse_job_params(job_type: JobType, params: JsonDict) -> JobParams:
    if job_type == JobType.INGEST_DATASET:
        return IngestDatasetJobParams.model_validate(params)

    if job_type == JobType.VALIDATE_DATASET_MANIFEST:
        return ValidateDatasetManifestJobParams.model_validate(params)

    if job_type == JobType.PREDICT_DETECTION:
        return PredictDetectionJobParams.model_validate(params)

    if job_type == JobType.EVALUATE_DETECTION:
        return EvaluateDetectionJobParams.model_validate(params)

    raise ValueError(f"Unsupported job type: {job_type}")
