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

    # Scene-level
    SCENE_VALIDATION = "scene_validation"
    SCENE_PROFILE = "scene_profile"

    # Scenario-level
    SCENARIO_MINING = "scenario_mining"
    SCENARIO_READINESS = "scenario_readiness"
