from __future__ import annotations

from enum import StrEnum


class ArtifactOwnerType(StrEnum):
    # resources
    DATASET = "dataset"
    DATASET_VERSION = "dataset_version"
    SCENE = "scene"
    EPISODE = "episode"
    SCENARIO_SET = "scenario_set"
    MODEL = "model"
    MODEL_VERSION = "model_version"
    ROBOT_RUN = "robot_run"
    LABEL_SET = "label_set"
    # platform
    JOB = "job"
    PIPELINE_RUN = "pipeline_run"
    # scene-level runs
    SCENE_VALIDATION_RUN = "scene_validation_run"
    SCENE_PROFILE_RUN = "scene_profile_run"
    # episode-level runs
    EPISODE_VALIDATION_RUN = "episode_validation_run"
    EPISODE_PROFILE_RUN = "episode_profile_run"
    # scenario-level runs
    SCENARIO_MINING_RUN = "scenario_mining_run"
    SCENARIO_READINESS_RUN = "scenario_readiness_run"
    # ML workflow runs
    INFERENCE_RUN = "inference_run"
    EVALUATION_RUN = "evaluation_run"


def model_version_owner_id(model_id: str, version: str) -> str:
    """Canonical owner_id for a ModelVersion artifact."""
    return f"{model_id}:{version}"
