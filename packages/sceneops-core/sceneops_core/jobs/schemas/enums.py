from __future__ import annotations

from enum import StrEnum


class JobType(StrEnum):
    # ── source dataset / raw log → SceneOps scenes ──
    INGEST_SCENES = "ingest_scenes"
    BUILD_SCENES = "build_scenes"

    # ── dataset-level aggregation ──
    BUILD_DATASET_MANIFEST = "build_dataset_manifest"
    BUILD_SCENE_INDEX = "build_scene_index"

    # ── scene-level jobs ──
    VALIDATE_SCENE = "validate_scene"
    PROFILE_SCENE = "profile_scene"
    REGISTER_SCENE = "register_scene"
    COMPARE_SCENES = "compare_scenes"
    AUTO_LABEL_SCENE = "auto_label_scene"
    EXPORT_SCENE_PACKAGE = "export_scene_package"

    # ── scenario-level jobs ──
    MINE_SCENARIOS = "mine_scenarios"
    SCORE_SCENARIO_READINESS = "score_scenario_readiness"

    # ── dataset version-level jobs ──
    AUTO_LABEL_DATASET = "auto_label_dataset"
    CHECK_DISTRIBUTION = "check_distribution"
    EXPORT_DATASET = "export_dataset"
    EXPORT_ANALYTICS_SNAPSHOT = "export_analytics_snapshot"

    # ── detection ──
    PREDICT_DETECTION = "predict_detection"
    EVALUATE_DETECTION = "evaluate_detection"

    # ── robot runtime ──
    INGEST_ROBOT_STATES = "ingest_robot_states"


class JobStatus(StrEnum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class JobStepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class JobEventLevel(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class JobEventType(StrEnum):
    CREATED = "created"
    QUEUED = "queued"
    LOCKED = "locked"
    STARTED = "started"
    HEARTBEAT = "heartbeat"

    STEP_STARTED = "step_started"
    STEP_SUCCEEDED = "step_succeeded"
    STEP_FAILED = "step_failed"
    STEP_SKIPPED = "step_skipped"

    RETRYING = "retrying"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
