from __future__ import annotations

from sceneops_core.jobs.schemas import JobType
from sceneops_core.pipelines.schemas import (
    PipelineDefinition,
    PipelineStepDefinition,
    PipelineType,
)


DATASET_INGESTION_PIPELINE = PipelineDefinition(
    type=PipelineType.DATASET_INGESTION,
    name="Dataset Ingestion",
    description="Ingest and validate a dataset version.",
    steps=[
        PipelineStepDefinition(
            name="ingest",
            order=0,
            job_type=JobType.INGEST_DATASET.value,
            depends_on=[],
            default_params={
                "dataset_type": "nuscenes",
                "mode": "upsert",
            },
        ),
        PipelineStepDefinition(
            name="validate",
            order=1,
            job_type=JobType.VALIDATE_DATASET.value,
            depends_on=["ingest"],
            default_params={
                "validate_samples": True,
                "require_target_channels": ["CAM_FRONT", "LIDAR_TOP"],
            },
        ),
        PipelineStepDefinition(
            name="profile",
            order=2,
            job_type=JobType.PROFILE_DATASET.value,
            depends_on=["validate"],
            default_params={
                "profile_samples": True,
                "profile_annotations": True,
                "profile_sensor_coverage": True,
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
            job_type=JobType.PREDICT_DETECTION.value,
            depends_on=[],
            default_params={
                "inference_backend": "mock",
            },
        ),
        PipelineStepDefinition(
            name="evaluate",
            order=1,
            job_type=JobType.EVALUATE_DETECTION.value,
            depends_on=["predict"],
            default_params={
                "evaluator_id": "center-distance",
                "match_distance_m": 2.0,
            },
        ),
    ],
)


BUILTIN_PIPELINE_DEFINITIONS = {
    PipelineType.DATASET_INGESTION: DATASET_INGESTION_PIPELINE,
    PipelineType.DETECTION_VALIDATION: DETECTION_VALIDATION_PIPELINE,
}


def get_pipeline_definition(
    pipeline_type: PipelineType,
) -> PipelineDefinition:
    try:
        return BUILTIN_PIPELINE_DEFINITIONS[pipeline_type]
    except KeyError as error:
        raise ValueError(f"Unsupported pipeline type: {pipeline_type}") from error
