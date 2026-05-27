from __future__ import annotations

from sceneops_core.schemas.jobs import JobType
from sceneops_core.schemas.pipelines import (
    PipelineDefinition,
    PipelineStepDefinition,
    PipelineType,
)


DETECTION_VALIDATION_PIPELINE = PipelineDefinition(
    type=PipelineType.DETECTION_VALIDATION,
    name="Detection Validation",
    description="Ingest dataset, generate predictions, and evaluate detection metrics.",
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
            name="predict",
            order=1,
            job_type=JobType.PREDICT_DETECTION.value,
            depends_on=["ingest"],
            default_params={
                "inference_backend": "mock",
            },
        ),
        PipelineStepDefinition(
            name="evaluate",
            order=2,
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
    DETECTION_VALIDATION_PIPELINE.type: DETECTION_VALIDATION_PIPELINE,
}


def get_pipeline_definition(
    pipeline_type: PipelineType,
) -> PipelineDefinition:
    try:
        return BUILTIN_PIPELINE_DEFINITIONS[pipeline_type]
    except KeyError as error:
        raise ValueError(f"Unsupported pipeline type: {pipeline_type}") from error
