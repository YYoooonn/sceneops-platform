from __future__ import annotations

from enum import StrEnum


class ArtifactBackend(StrEnum):
    LOCAL = "local"
    MINIO = "minio"
    S3 = "s3"
    GCS = "gcs"


class ArtifactKind(StrEnum):
    # Robot-level
    # The raw ROS2 rosbag2/MCAP recording for one RobotRun -- transport-
    # agnostic (a directly `ros2 bag record`-ed file and a Kafka-captured
    # file, see ros2/capture/, are registered under this same kind; there
    # is no separate STREAMING_MCAP kind, since the artifact itself is
    # identical MCAP content regardless of how it reached local disk).
    ROBOT_RUN_RECORDING = "robot_run_recording"
    # The canonical RobotRunManifest (sceneops_core.robots.manifest) that
    # publishes one recording; registered together with the recording by
    # REGISTER_ROBOT_RUN.
    ROBOT_RUN_MANIFEST = "robot_run_manifest"

    # Observation-level
    # Immutable bytes of one canonical observation (an image, a point
    # cloud, ...), extracted from a registered recording and referenced
    # from canonical manifests by artifact id
    # (sceneops_core.artifacts.schemas.payload.PayloadRef).
    OBSERVATION_PAYLOAD = "observation_payload"

    # Scene-level
    SCENE_INDEX = "scene_index"
    # A canonical SceneManifest revision (sceneops_core.scenes.schemas.manifests),
    # stored under a checksum-qualified key. The only kind REGISTER_SCENES
    # accepts.
    SCENE_MANIFEST = "scene_manifest"
    WORLD_STATE_MANIFEST = "world_state_manifest"
    SCENE_PACKAGE = "scene_package"

    # Episode-level
    EPISODE_MANIFEST = "episode_manifest"
    EPISODE_VALIDATION_REPORT = "episode_validation_report"
    EPISODE_PROFILE_REPORT = "episode_profile_report"
    # SceneOps V2 Request 2.3: persisted AlignedEpisodeArtifact envelope.
    # Owner stays ArtifactOwnerType.EPISODE — no new owner type needed.
    ALIGNED_EPISODE_MANIFEST = "aligned_episode_manifest"
    # SceneOps V2 Request 2.4: structural validation / descriptive profile
    # reports over one ALIGNED_EPISODE_MANIFEST revision. Owner stays
    # ArtifactOwnerType.EPISODE, same as the two kinds above.
    ALIGNED_EPISODE_VALIDATION_REPORT = "aligned_episode_validation_report"
    ALIGNED_EPISODE_PROFILE_REPORT = "aligned_episode_profile_report"
    # SceneOps V2 Request 2.5: index for one columnar learning-data export
    # snapshot (learning_episodes/learning_steps/learning_signals.parquet).
    # The Parquet tables themselves reuse ANALYTICS_TABLE below -- only the
    # manifest indexing them needs a dedicated kind. Owner is
    # ArtifactOwnerType.DATASET_VERSION, matching EXPORT_ANALYTICS_SNAPSHOT's
    # existing Scene-table convention.
    LEARNING_DATA_EXPORT_MANIFEST = "learning_data_export_manifest"
    # SceneOps V2 Request 2.6: selection-layer manifest over one pinned
    # LEARNING_DATA_EXPORT_MANIFEST revision -- which AlignedEpisode
    # revisions were selected/rejected and why. Owner stays
    # ArtifactOwnerType.DATASET_VERSION, same as the export manifest it
    # selects over.
    EPISODE_CURATION_MANIFEST = "episode_curation_manifest"

    # Dataset-level
    DATASET_MANIFEST = "dataset_manifest"
    DATASET_VALIDATION_REPORT = "dataset_validation_report"
    DATASET_PROFILE_REPORT = "dataset_profile_report"
    ANALYTICS_TABLE = "analytics_table"

    # Scenario-level
    SCENARIO_SET_MANIFEST = "scenario_set_manifest"
    SCENARIO_MINING_REPORT = "scenario_mining_report"
    SCENARIO_READINESS_REPORT = "scenario_readiness_report"

    # Inference / evaluation
    PREDICTION_MANIFEST = "prediction_manifest"
    PREDICTIONS_ROOT = "predictions_root"
    EVALUATION_MANIFEST = "evaluation_manifest"
    METRICS = "metrics"

    # Auto-label
    AUTO_LABEL_MANIFEST = "auto_label_manifest"
    AUTO_LABEL_REPORT = "auto_label_report"

    # Model
    MODEL_ARTIFACT = "model_artifact"
    MODEL_CONFIG = "model_config"

    OTHER = "other"
