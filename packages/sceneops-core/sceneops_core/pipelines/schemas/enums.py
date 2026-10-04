from __future__ import annotations

from enum import StrEnum


class PipelineType(StrEnum):
    # One registered RobotRun recording -> canonical Scenes -> registration
    # -> validation / profiling (ADR-007 §17.3).
    RECORDING_SCENE_BUILDING = "recording_scene_building"

    # Dataset scenes -> scenario set / readiness report
    SCENARIO_CURATION = "scenario_curation"

    # Dataset/model -> prediction -> evaluation
    DETECTION_EVALUATION = "detection_evaluation"

    # One registered RobotRun recording -> canonical Episodes -> registration
    # -> validation / profiling (ADR-007 §17.4). A sibling of
    # RECORDING_SCENE_BUILDING; neither depends on the other.
    RECORDING_EPISODE_BUILDING = "recording_episode_building"


class PipelineRunStatus(StrEnum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    BLOCKED = "blocked"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PipelineTaskRunStatus(StrEnum):
    PENDING = "pending"
    WAITING = "waiting"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    BLOCKED = "blocked"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"
