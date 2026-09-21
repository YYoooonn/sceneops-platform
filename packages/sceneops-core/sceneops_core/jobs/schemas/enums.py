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
    # No handler registered yet — reserved. Matching
    # ArtifactOwnerType.SCENE_COMPARISON_RUN / SCENE_AUTO_LABEL_RUN /
    # SCENE_EXPORT_RUN + ArtifactKind.SCENE_PACKAGE already exist for these
    # (see artifacts/schemas/{owner,enums}.py), confirming deliberate
    # reservation rather than accidental drift (Stabilization Request 5).
    COMPARE_SCENES = "compare_scenes"
    AUTO_LABEL_SCENE = "auto_label_scene"
    EXPORT_SCENE_PACKAGE = "export_scene_package"

    # ── scenario-level jobs ──
    MINE_SCENARIOS = "mine_scenarios"
    SCORE_SCENARIO_READINESS = "score_scenario_readiness"

    # ── dataset version-level jobs ──
    # No handler registered yet — reserved, same as the scene-level trio
    # above. Matching ArtifactOwnerType.DATASET_AUTO_LABEL_RUN /
    # DATASET_EXPORT_RUN already exist.
    AUTO_LABEL_DATASET = "auto_label_dataset"
    EXPORT_DATASET = "export_dataset"
    EXPORT_ANALYTICS_SNAPSHOT = "export_analytics_snapshot"

    # ── detection ──
    PREDICT_DETECTION = "predict_detection"
    EVALUATE_DETECTION = "evaluate_detection"

    # ── robot runtime ──
    INGEST_ROBOT_STATES = "ingest_robot_states"
    EXPORT_ROBOT_ANALYTICS_SNAPSHOT = "export_robot_analytics_snapshot"

    # ── robot rosbag / MCAP → SceneOps episodes ──
    BUILD_EPISODES = "build_episodes"
    REGISTER_EPISODE = "register_episode"

    # ── episode-level jobs ──
    VALIDATE_EPISODE = "validate_episode"
    PROFILE_EPISODE = "profile_episode"

    # ── episode temporal alignment (SceneOps V2 Request 2.3) ──
    ALIGN_EPISODE = "align_episode"

    # ── aligned-episode validation / profiling (SceneOps V2 Request 2.4) ──
    VALIDATE_ALIGNED_EPISODE = "validate_aligned_episode"
    PROFILE_ALIGNED_EPISODE = "profile_aligned_episode"

    # ── columnar learning-data export (SceneOps V2 Request 2.5) ──
    EXPORT_LEARNING_DATA = "export_learning_data"

    # ── episode curation (SceneOps V2 Request 2.6) ──
    CURATE_EPISODES = "curate_episodes"


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
