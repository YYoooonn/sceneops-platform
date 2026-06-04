from __future__ import annotations

from enum import StrEnum


class ArtifactOwnerType(StrEnum):
    # resources
    DATASET = "dataset"
    DATASET_VERSION = "dataset_version"
    SCENE = "scene"
    SCENARIO_SET = "scenario_set"
    MODEL = "model"
    MODEL_VERSION = "model_version"
    # platform
    JOB = "job"
    PIPELINE_RUN = "pipeline_run"
    # scene-level runs
    SCENE_VALIDATION_RUN = "scene_validation_run"
    SCENE_PROFILE_RUN = "scene_profile_run"
    SCENE_COMPARISON_RUN = "scene_comparison_run"
    SCENE_EXPORT_RUN = "scene_export_run"
    SCENE_AUTO_LABEL_RUN = "scene_auto_label_run"
    # dataset-level runs
    DATASET_DISTRIBUTION_RUN = "dataset_distribution_run"
    DATASET_EXPORT_RUN = "dataset_export_run"
    DATASET_AUTO_LABEL_RUN = "dataset_auto_label_run"
    # scenario-level runs
    SCENARIO_MINING_RUN = "scenario_mining_run"
    SCENARIO_READINESS_RUN = "scenario_readiness_run"
    # ML workflow runs
    INFERENCE_RUN = "inference_run"
    EVALUATION_RUN = "evaluation_run"


def model_version_owner_id(model_id: str, version: str) -> str:
    """Canonical owner_id for a ModelVersion artifact."""
    return f"{model_id}:{version}"
