# packages/sceneops-core/sceneops_core/pipelines/builtin.py

from __future__ import annotations

from sceneops_core.jobs.schemas import JobType
from sceneops_core.pipelines.registry import PipelineDefinitionRegistry
from sceneops_core.pipelines.schemas import (
    PipelineDefinition,
    PipelineStepDefinition,
    PipelineType,
)


DATASET_INGESTION_PIPELINE = PipelineDefinition(
    type=PipelineType.DATASET_INGESTION,
    name="Dataset Ingestion",
    description="Ingest, validate, and profile a dataset version.",
    steps=[
        PipelineStepDefinition(
            name="ingest",
            order=0,
            job_type=JobType.INGEST_DATASET,
            depends_on=[],
            default_params={
                "dataset_type": "nuscenes",
                "mode": "upsert",
            },
        ),
        PipelineStepDefinition(
            name="validate",
            order=1,
            job_type=JobType.VALIDATE_DATASET,
            depends_on=["ingest"],
            default_params={
                "validate_samples": True,
                "require_target_channels": ["CAM_FRONT", "LIDAR_TOP"],
            },
        ),
        PipelineStepDefinition(
            name="profile",
            order=2,
            job_type=JobType.PROFILE_DATASET,
            depends_on=["validate"],
            default_params={
                "profile_samples": True,
                "profile_annotations": True,
                "profile_sensor_coverage": True,
                "profile_scene_distribution": True,
                "require_target_channels": ["CAM_FRONT", "LIDAR_TOP"],
            },
        ),
    ],
)


DETECTION_VALIDATION_PIPELINE = PipelineDefinition(
    type=PipelineType.DETECTION_VALIDATION,
    name="Detection Validation",
    description="Run prediction and evaluate detection metrics on a ready dataset.",
    steps=[
        PipelineStepDefinition(
            name="predict",
            order=0,
            job_type=JobType.PREDICT_DETECTION,
            depends_on=[],
            default_params={
                "inference_backend": "mock",
            },
        ),
        PipelineStepDefinition(
            name="evaluate",
            order=1,
            job_type=JobType.EVALUATE_DETECTION,
            depends_on=["predict"],
            default_params={
                "evaluator_id": "center-distance",
                "match_distance_m": 2.0,
            },
        ),
    ],
)


BUILTIN_PIPELINE_DEFINITIONS = [
    DATASET_INGESTION_PIPELINE,
    DETECTION_VALIDATION_PIPELINE,
]


def create_builtin_pipeline_definition_registry() -> PipelineDefinitionRegistry:
    return PipelineDefinitionRegistry(BUILTIN_PIPELINE_DEFINITIONS)


def get_pipeline_definition(
    pipeline_type: PipelineType,
) -> PipelineDefinition:
    return create_builtin_pipeline_definition_registry().get(pipeline_type)
