from __future__ import annotations

from enum import StrEnum


class ArtifactOwnerType(StrEnum):
    DATASET = "dataset"
    DATASET_VERSION = "dataset_version"
    SCENE = "scene"
    SCENARIO_SET = "scenario_set"
    MODEL = "model"
    MODEL_VERSION = "model_version"
    JOB = "job"
    PIPELINE_RUN = "pipeline_run"
    SCENE_VALIDATION_RUN = "scene_validation_run"
    SCENE_PROFILE_RUN = "scene_profile_run"
    INFERENCE_RUN = "inference_run"
    EVALUATION_RUN = "evaluation_run"


def model_version_owner_id(model_id: str, version: str) -> str:
    """Canonical owner_id for a ModelVersion artifact."""
    return f"{model_id}:{version}"
