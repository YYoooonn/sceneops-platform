from __future__ import annotations

from enum import StrEnum


class PipelineType(StrEnum):
    # Existing scene-aware dataset source -> SceneOps scenes -> DatasetManifest
    DATASET_SCENE_INGESTION = "dataset_scene_ingestion"

    # Raw log / raw sensor stream -> SceneOps scenes -> DatasetManifest
    RAW_LOG_SCENE_BUILDING = "raw_log_scene_building"

    # Raw/reconstructed scene -> explicit world state / scene package
    SCENE_RECONSTRUCTION = "scene_reconstruction"

    # Generated/reconstructed/simulated scene registration flow
    SCENE_REGISTRATION = "scene_registration"

    # Dataset scenes -> scenario set / readiness report
    SCENARIO_CURATION = "scenario_curation"

    # Generated/reconstructed scenes -> dataset version export
    GENERATED_DATASET_PREPARATION = "generated_dataset_preparation"

    # Dataset/model -> prediction -> evaluation
    DETECTION_EVALUATION = "detection_evaluation"


class PipelineRunStatus(StrEnum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PipelineStepRunStatus(StrEnum):
    PENDING = "pending"
    WAITING = "waiting"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"
