from __future__ import annotations

from enum import StrEnum


class PipelineType(StrEnum):
    """The first-class Pipelines. A Pipeline exists only where multi-stage
    orchestration, retry and lineage justify it; single operations are Jobs
    (ADR-007 §34)."""

    # One registered RobotRun recording -> canonical Scenes -> registration
    # -> validation / profiling (ADR-007 §17.3).
    RECORDING_SCENE_BUILDING = "recording_scene_building"

    # One registered RobotRun recording -> canonical Episodes -> registration
    # -> validation / profiling (ADR-007 §17.4). A sibling of
    # RECORDING_SCENE_BUILDING; neither depends on the other.
    RECORDING_EPISODE_BUILDING = "recording_episode_building"

    # Registered Scenes + pinned label set revisions -> sample views ->
    # ScenarioSet -> prediction revision -> evaluation (ADR-007 §33, §34).
    SCENE_ML_EVALUATION = "scene_ml_evaluation"

    # Pinned registered Episodes -> AlignedEpisodes -> learning data export
    # (ADR-007 §33.6, §33.7, §34).
    EPISODE_LEARNING_DATA_BUILDING = "episode_learning_data_building"


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
