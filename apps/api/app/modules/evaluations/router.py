from fastapi import APIRouter, Depends, Query

from sceneops_core.schemas.evaluations import (
    DetectionEvaluationRunManifest,
    DetectionSampleEvaluation,
    EvaluationRunListResponse,
    SampleEvaluationListResponse,
)

from app.core.dependencies import get_evaluation_run_service
from app.modules.evaluations.service import EvaluationRunService
from app.shared.errors import not_found

router = APIRouter(prefix="/evaluations", tags=["evaluations"])


@router.get(
    "",
    response_model=EvaluationRunListResponse,
)
def list_evaluations(
    dataset_id: str | None = Query(default=None, alias="datasetId"),
    dataset_version: str | None = Query(default=None, alias="datasetVersion"),
    model_id: str | None = Query(default=None, alias="modelId"),
    model_version: str | None = Query(default=None, alias="modelVersion"),
    inference_run_id: str | None = Query(default=None, alias="inferenceRunId"),
    status: str | None = Query(default=None),
    service: EvaluationRunService = Depends(get_evaluation_run_service),
):
    evaluations = service.list_evaluations(
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        model_id=model_id,
        model_version=model_version,
        inference_run_id=inference_run_id,
        status=status,
    )

    return {
        "evaluations": evaluations,
        "count": len(evaluations),
    }


@router.get(
    "/{evaluation_run_id}",
    response_model=DetectionEvaluationRunManifest,
)
def get_evaluation(
    evaluation_run_id: str,
    service: EvaluationRunService = Depends(get_evaluation_run_service),
):
    evaluation = service.get_evaluation(evaluation_run_id)

    if evaluation is None:
        raise not_found("Evaluation run not found")

    return evaluation


@router.get(
    "/{evaluation_run_id}/samples",
    response_model=SampleEvaluationListResponse,
)
def list_sample_evaluations(
    evaluation_run_id: str,
    service: EvaluationRunService = Depends(get_evaluation_run_service),
):
    evaluation = service.get_evaluation(evaluation_run_id)

    if evaluation is None:
        raise not_found("Evaluation run not found")

    samples = service.list_sample_evaluations(evaluation_run_id)

    return {
        "samples": samples,
        "count": len(samples),
    }


@router.get(
    "/{evaluation_run_id}/samples/{sample_id}",
    response_model=DetectionSampleEvaluation,
)
def get_sample_evaluation(
    evaluation_run_id: str,
    sample_id: str,
    service: EvaluationRunService = Depends(get_evaluation_run_service),
):
    evaluation = service.get_evaluation(evaluation_run_id)

    if evaluation is None:
        raise not_found("Evaluation run not found")

    sample = service.get_sample_evaluation(evaluation_run_id, sample_id)

    if sample is None:
        raise not_found("Sample evaluation not found")

    return sample
