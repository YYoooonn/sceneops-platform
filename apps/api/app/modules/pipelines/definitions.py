from __future__ import annotations

from sceneops_core.schemas.jobs import JobType
from sceneops_core.schemas.pipelines import (
    PipelineDefinition,
    PipelineStepDefinition,
    PipelineType,
)


DETECTION_VALIDATION_PIPELINE = PipelineDefinition(
    type=PipelineType.DETECTION_VALIDATION,
    name="Detection Validation Pipeline",
    description=(
        "Ingest a dataset, generate detection predictions, "
        "and evaluate detection metrics."
    ),
    steps=[
        PipelineStepDefinition(
            name="ingest",
            order=1,
            jobType=JobType.INGEST_NUSCENES.value,
            dependsOn=[],
            defaultParams={
                "mode": "upsert",
            },
        ),
        PipelineStepDefinition(
            name="predict",
            order=2,
            jobType=JobType.PREDICT_MOCK_DETECTION.value,
            dependsOn=["ingest"],
            defaultParams={},
        ),
        PipelineStepDefinition(
            name="evaluate",
            order=3,
            jobType=JobType.EVALUATE_DETECTION.value,
            dependsOn=["predict"],
            defaultParams={
                "matchDistanceM": 2.0,
            },
        ),
    ],
)


PIPELINE_DEFINITIONS: dict[PipelineType, PipelineDefinition] = {
    PipelineType.DETECTION_VALIDATION: DETECTION_VALIDATION_PIPELINE,
}


def get_pipeline_definition(pipeline_type: PipelineType) -> PipelineDefinition:
    try:
        return PIPELINE_DEFINITIONS[pipeline_type]
    except KeyError as error:
        raise ValueError(f"Unsupported pipeline type: {pipeline_type}") from error
