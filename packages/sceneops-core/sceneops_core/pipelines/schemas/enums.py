from __future__ import annotations

from enum import StrEnum


class PipelineType(StrEnum):
    # One registered RobotRun recording -> canonical Scenes -> registration
    # -> validation / profiling (ADR-007 §17.3).
    RECORDING_SCENE_BUILDING = "recording_scene_building"

    # Pinned sample views -> ScenarioSet revision / readiness report
    # (ADR-007 §33.4).
    SCENARIO_CURATION = "scenario_curation"

    # Pinned sample views or a ScenarioSet + model -> prediction revision ->
    # evaluation against a pinned label set revision (ADR-007 §33.5).
    DETECTION_EVALUATION = "detection_evaluation"

    # One registered Episode -> AlignedEpisode -> validation / profile
    # (ADR-007 §33.6).
    ALIGNED_EPISODE_BUILDING = "aligned_episode_building"

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
