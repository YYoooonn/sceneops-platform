from __future__ import annotations

from enum import StrEnum


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RunType(StrEnum):
    # Inference / evaluation
    INFERENCE = "inference"
    EVALUATION = "evaluation"

    # Dataset-level
    DATASET_VALIDATION = "dataset_validation"
    DATASET_PROFILE = "dataset_profile"

    # Scene-level
    SCENE_VALIDATION = "scene_validation"
    SCENE_PROFILE = "scene_profile"
    SCENE_COMPARISON = "scene_comparison"
    SCENE_RECONSTRUCTION = "scene_reconstruction"
    SCENE_PACKAGE_EXPORT = "scene_package_export"

    # Scenario-level
    SCENARIO_MINING = "scenario_mining"
    SCENARIO_READINESS = "scenario_readiness"

    # Labeling
    SCENE_AUTO_LABEL = "scene_auto_label"
    DATASET_AUTO_LABEL = "dataset_auto_label"

    # Distribution / export
    DATASET_DISTRIBUTION = "dataset_distribution"
    DATASET_EXPORT = "dataset_export"
