from fastapi import APIRouter, Depends, Query

from sceneops_core.schemas.runs import (
    InferenceRunListResponse,
    InferenceRunManifest,
    PredictionListResponse,
    PredictionManifest,
)

from app.core.dependencies import get_inference_run_service
from app.modules.runs.service import InferenceRunService
from app.shared.errors import not_found

router = APIRouter(prefix="/runs", tags=["runs"])


@router.get(
    "/inference",
    response_model=InferenceRunListResponse,
)
def list_inference_runs(
    dataset_id: str | None = Query(default=None, alias="datasetId"),
    dataset_version: str | None = Query(default=None, alias="datasetVersion"),
    model_id: str | None = Query(default=None, alias="modelId"),
    model_version: str | None = Query(default=None, alias="modelVersion"),
    status: str | None = Query(default=None),
    service: InferenceRunService = Depends(get_inference_run_service),
):
    runs = service.list_inference_runs(
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        model_id=model_id,
        model_version=model_version,
        status=status,
    )

    return {
        "runs": runs,
        "count": len(runs),
    }


@router.get(
    "/inference/{run_id}",
    response_model=InferenceRunManifest,
)
def get_inference_run(
    run_id: str,
    service: InferenceRunService = Depends(get_inference_run_service),
):
    run = service.get_inference_run(run_id)

    if run is None:
        raise not_found("Inference run not found")

    return run


@router.get(
    "/inference/{run_id}/predictions",
    response_model=PredictionListResponse,
)
def list_predictions(
    run_id: str,
    service: InferenceRunService = Depends(get_inference_run_service),
):
    run = service.get_inference_run(run_id)

    if run is None:
        raise not_found("Inference run not found")

    predictions = service.list_predictions(run_id)

    return {
        "predictions": predictions,
        "count": len(predictions),
    }


@router.get(
    "/inference/{run_id}/predictions/{sample_id}",
    response_model=PredictionManifest,
)
def get_prediction(
    run_id: str,
    sample_id: str,
    service: InferenceRunService = Depends(get_inference_run_service),
):
    run = service.get_inference_run(run_id)

    if run is None:
        raise not_found("Inference run not found")

    prediction = service.get_prediction(run_id, sample_id)

    if prediction is None:
        raise not_found("Prediction not found")

    return prediction
