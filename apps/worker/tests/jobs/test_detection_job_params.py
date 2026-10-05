"""Pure unit tests for the detection job handlers' parameter plumbing and
validation helpers. The behaviour of the jobs over pinned inputs is covered
by ``tests/derived/test_detection_vertical.py``."""

from __future__ import annotations

import pytest

from sceneops_core.inference.enums import InferenceBackendType
from sceneops_core.models.schemas.enums import ModelBackend
from sceneops_core.pipelines.schemas import (
    DatasetInputRef,
    ModelInputRef,
    PipelineInputRef,
    PipelineTaskInputs,
)
from sceneops_worker.jobs.evaluation import EvaluateDetectionJobHandler
from sceneops_worker.jobs.inference import PredictDetectionJobHandler

PREDICT = PredictDetectionJobHandler()
EVALUATE = EvaluateDetectionJobHandler()


def _inputs(**kwargs) -> PipelineTaskInputs:
    return PipelineTaskInputs(
        pipeline=PipelineInputRef(
            pipeline_run_id="pipe-1",
            pipeline_type="scene_ml_evaluation",
            task_id="t",
            pipeline_task_id="t",
            pipeline_task_run_id="ptask-1",
        ),
        dataset=DatasetInputRef(dataset_id="d", dataset_version="v1"),
        model=ModelInputRef(model_id="m", model_version="1"),
        **kwargs,
    )


def test_model_backend_must_match_the_registered_backend():
    PREDICT._validate_model_backend(InferenceBackendType.MOCK, ModelBackend.MOCK)
    with pytest.raises(ValueError, match="backend mismatch"):
        PREDICT._validate_model_backend(
            InferenceBackendType.GROUNDING_DINO, ModelBackend.MOCK
        )


def test_grounding_dino_requires_an_endpoint():
    with pytest.raises(ValueError, match="endpoint_url"):
        PREDICT._validate_backend_inputs(
            InferenceBackendType.GROUNDING_DINO, model_uri=None, endpoint_url=None
        )
    PREDICT._validate_backend_inputs(
        InferenceBackendType.GROUNDING_DINO, model_uri=None, endpoint_url="http://x:1"
    )
    PREDICT._validate_backend_inputs(
        InferenceBackendType.MOCK, model_uri=None, endpoint_url=None
    )


def test_onnx_runtime_is_no_longer_a_backend():
    assert "onnx_runtime" not in {b.value for b in InferenceBackendType}
    assert "onnx_runtime" not in {b.value for b in ModelBackend}


def test_predict_params_take_the_pipeline_scope_and_resolved_model():
    params = PREDICT.build_job_params(
        _inputs(params={"camera_channel": "CAM_FRONT", "scenario_set_id": "scset-1"})
    )
    assert params["dataset_id"] == "d" and params["dataset_version"] == "v1"
    assert (params["model_id"], params["model_version"]) == ("m", "1")
    assert params["scenario_set_id"] == "scset-1"


def test_evaluate_requires_the_inference_run_and_forwards_the_prediction_pin():
    with pytest.raises(ValueError, match="inference_run_id is required"):
        EVALUATE.build_job_params(_inputs())
    params = EVALUATE.build_job_params(
        _inputs(
            refs={
                "inference_run_id": "run-1",
                "prediction_manifest_checksum": "sha256:" + "a" * 64,
            },
            params={"label_set": {"label_set_id": "gt"}},
        )
    )
    assert params["inference_run_id"] == "run-1"
    assert params["prediction_manifest_checksum"] == "sha256:" + "a" * 64
    assert params["label_set"] == {"label_set_id": "gt"}
