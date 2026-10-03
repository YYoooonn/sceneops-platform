from __future__ import annotations

from enum import StrEnum


class PipelineType(StrEnum):
    # Legacy Scene producers: emit pre-canonical scene manifests that are
    # never registered as canonical Scenes.
    DATASET_SCENE_INGESTION = "dataset_scene_ingestion"
    RAW_LOG_SCENE_BUILDING = "raw_log_scene_building"

    # Dataset scenes -> scenario set / readiness report
    SCENARIO_CURATION = "scenario_curation"

    # Dataset/model -> prediction -> evaluation
    DETECTION_EVALUATION = "detection_evaluation"

    # Robot rosbag/MCAP -> SceneOps episodes (task-oriented observation+action
    # units, segmented by Mission boundaries) -> registered EpisodeRecords.
    # Deliberately separate from RAW_LOG_SCENE_BUILDING.
    RAW_LOG_EPISODE_BUILDING = "raw_log_episode_building"


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
