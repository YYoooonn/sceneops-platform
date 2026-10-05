from __future__ import annotations

from enum import StrEnum


class JobType(StrEnum):
    # ── registered RobotRun recording → canonical Scenes ──
    BUILD_RECORDING_SCENES = "build_recording_scenes"

    # ── scene-level jobs ──
    VALIDATE_SCENE = "validate_scene"
    PROFILE_SCENE = "profile_scene"
    # The only writer of SceneRecord membership and DatasetVersion Scene
    # summaries (ADR-007 §17.5).
    REGISTER_SCENES = "register_scenes"

    # ── derived L3 inputs (ADR-007 §33) ──
    # Post-acquisition labels as an independent, lineage-bearing label set.
    IMPORT_LABELS = "import_labels"
    # Policy-driven synchronized sample views of registered Scenes.
    BUILD_SCENE_SAMPLE_VIEWS = "build_scene_sample_views"

    # ── scenario-level jobs ──
    MINE_SCENARIOS = "mine_scenarios"
    SCORE_SCENARIO_READINESS = "score_scenario_readiness"

    # ── dataset version-level jobs ──
    EXPORT_ANALYTICS_SNAPSHOT = "export_analytics_snapshot"

    # ── detection ──
    PREDICT_DETECTION = "predict_detection"
    EVALUATE_DETECTION = "evaluate_detection"

    # ── robot recording registration ──
    REGISTER_ROBOT_RUN = "register_robot_run"

    # ── robot runtime ──
    INGEST_ROBOT_STATES = "ingest_robot_states"
    EXPORT_ROBOT_ANALYTICS_SNAPSHOT = "export_robot_analytics_snapshot"

    # ── registered RobotRun recording → canonical Episodes (ADR-007 §17.4) ──
    BUILD_RECORDING_EPISODES = "build_recording_episodes"
    REGISTER_EPISODES = "register_episodes"

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
