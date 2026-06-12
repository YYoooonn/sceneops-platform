"""Unit tests for PredictDetectionJobHandler.

Tests cover:
- Validation helpers (pure functions, no DB needed)
- _require_ready_dataset_version (mocked dataset store)
- _require_model_version (mocked model store)
- _extract_prediction_counts
- _build_succeeded_record
- _build_result
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from sceneops_core.datasets.schemas.enums import DatasetVersionStatus
from sceneops_core.datasets.schemas.records import DatasetVersionRecord
from sceneops_core.inference.enums import InferenceBackendType
from sceneops_core.inference.schemas.detection import DetectionInferenceResult
from sceneops_core.inference.schemas.runs import InferenceRunRecord
from sceneops_core.jobs.schemas.results.detection import PredictDetectionJobResult
from sceneops_core.models.schemas.enums import ModelBackend
from sceneops_core.models.schemas.records import ModelVersionRecord
from sceneops_core.runs.schemas import RunStatus
from sceneops_worker.jobs.inference.predict_detection import (
    PredictDetectionArtifacts,
    PredictDetectionExecution,
    PredictDetectionJobHandler,
    PredictionCounts,
)


# ── helpers ───────────────────────────────────────────────────────────────────

HANDLER = PredictDetectionJobHandler()


def _dataset_version(
    status: DatasetVersionStatus = DatasetVersionStatus.READY,
    manifest_uri: str | None = "file:///manifest.json",
) -> DatasetVersionRecord:
    return DatasetVersionRecord(
        dataset_id="nuscenes",
        version="v1.0-mini",
        status=status,
        manifest_uri=manifest_uri,
    )


def _model_version(backend: ModelBackend = ModelBackend.MOCK) -> ModelVersionRecord:
    return ModelVersionRecord(
        id="mv-001",
        model_id="grounding-dino",
        version="tiny",
        backend=backend,
        model_uri=None,
        endpoint_url=None,
    )


def _inference_result(**overrides) -> DetectionInferenceResult:
    defaults = dict(
        run_id="infer-001",
        prediction_manifest_uri="file:///pred_manifest.json",  # required
        predictions_root_uri="file:///preds/",
        scene_count=2,
        sample_count=5,
        inference_request_count=4,
        prediction_count=8,
        evaluable_prediction_count=7,
        lifting_succeeded_count=5,
        lifting_failed_count=1,
        status="succeeded",
        metrics={"avg_roundtrip_ms": 300.0, "camera_channel": "CAM_FRONT"},
        metadata={"backend": "grounding_dino", "endpoint_url": "http://test:8001"},
    )
    defaults.update(overrides)
    return DetectionInferenceResult(**defaults)


def _initial_record() -> InferenceRunRecord:
    return InferenceRunRecord(
        run_id="infer-001",
        dataset_id="nuscenes",
        dataset_version="v1.0-mini",
        model_id="grounding-dino",
        model_version="tiny",
        inference_backend="grounding_dino",
        status=RunStatus.RUNNING,
    )


# ── _validate_model_backend ───────────────────────────────────────────────────


def test_validate_model_backend_ok():
    HANDLER._validate_model_backend(
        InferenceBackendType.GROUNDING_DINO, ModelBackend.GROUNDING_DINO
    )


def test_validate_model_backend_mismatch():
    with pytest.raises(ValueError, match="Model backend mismatch"):
        HANDLER._validate_model_backend(
            InferenceBackendType.GROUNDING_DINO, ModelBackend.MOCK
        )


# ── _validate_backend_inputs ──────────────────────────────────────────────────


def test_validate_backend_inputs_gdino_no_endpoint_url():
    with pytest.raises(ValueError, match="endpoint_url"):
        HANDLER._validate_backend_inputs(
            InferenceBackendType.GROUNDING_DINO,
            model_uri=None,
            endpoint_url=None,
        )


def test_validate_backend_inputs_gdino_with_endpoint_url_ok():
    HANDLER._validate_backend_inputs(
        InferenceBackendType.GROUNDING_DINO,
        model_uri=None,
        endpoint_url="http://sceneops-inference:8001",
    )


def test_validate_backend_inputs_onnx_no_model_uri():
    with pytest.raises(ValueError, match="model_uri"):
        HANDLER._validate_backend_inputs(
            InferenceBackendType.ONNX_RUNTIME,
            model_uri=None,
            endpoint_url=None,
        )


def test_validate_backend_inputs_onnx_with_model_uri_ok():
    HANDLER._validate_backend_inputs(
        InferenceBackendType.ONNX_RUNTIME,
        model_uri="s3://bucket/model.onnx",
        endpoint_url=None,
    )


def test_validate_backend_inputs_mock_both_optional():
    HANDLER._validate_backend_inputs(
        InferenceBackendType.MOCK, model_uri=None, endpoint_url=None
    )


# ── _require_ready_dataset_version ────────────────────────────────────────────


async def _require_dataset_version(version: DatasetVersionRecord):
    context = MagicMock()
    context.dataset_store.get_version = AsyncMock(return_value=version)
    return await PredictDetectionJobHandler._require_ready_dataset_version(
        context, "nuscenes", "v1.0-mini"
    )


async def test_require_dataset_version_not_found():
    context = MagicMock()
    context.dataset_store.get_version = AsyncMock(return_value=None)
    with pytest.raises(ValueError, match="not found"):
        await PredictDetectionJobHandler._require_ready_dataset_version(
            context, "nuscenes", "v1.0-mini"
        )


async def test_require_dataset_version_not_ready():
    with pytest.raises(ValueError, match="not usable"):
        await _require_dataset_version(
            _dataset_version(status=DatasetVersionStatus.INGESTING)
        )


async def test_require_dataset_version_no_manifest_uri():
    with pytest.raises(ValueError, match="manifest_uri"):
        await _require_dataset_version(_dataset_version(manifest_uri=None))


async def test_require_dataset_version_ready_ok():
    result = await _require_dataset_version(_dataset_version())
    assert result.dataset_id == "nuscenes"


# ── _require_model_version ────────────────────────────────────────────────────


async def test_require_model_version_not_found():
    context = MagicMock()
    context.model_store.get_version = AsyncMock(return_value=None)
    with pytest.raises(ValueError, match="not found"):
        await PredictDetectionJobHandler._require_model_version(
            context, "grounding-dino", "tiny"
        )


async def test_require_model_version_ok():
    context = MagicMock()
    mv = _model_version(ModelBackend.GROUNDING_DINO)
    context.model_store.get_version = AsyncMock(return_value=mv)
    result = await PredictDetectionJobHandler._require_model_version(
        context, "grounding-dino", "tiny"
    )
    assert result.backend == ModelBackend.GROUNDING_DINO


# ── _extract_prediction_counts ────────────────────────────────────────────────


def test_extract_prediction_counts_full():
    result = _inference_result()
    counts = HANDLER._extract_prediction_counts(result)
    assert counts.scene_count == 2
    assert counts.sample_count == 5
    assert counts.inference_request_count == 4
    assert counts.prediction_count == 8
    assert counts.evaluable_prediction_count == 7
    assert counts.lifting_succeeded_count == 5
    assert counts.lifting_failed_count == 1


def test_extract_prediction_counts_from_direct_fields():
    """Counts come from direct fields, not the metrics dict."""
    result = _inference_result(
        prediction_count=10,
        evaluable_prediction_count=7,
        lifting_failed_count=3,
    )
    counts = HANDLER._extract_prediction_counts(result)
    assert counts.evaluable_prediction_count == 7
    assert counts.lifting_failed_count == 3


def test_extract_prediction_counts_no_lifting():
    """Mock backend: lifting fields are 0, evaluable_prediction_count equals prediction_count."""
    result = _inference_result(
        prediction_count=5,
        evaluable_prediction_count=5,
        lifting_succeeded_count=0,
        lifting_failed_count=0,
        metrics={},
    )
    counts = HANDLER._extract_prediction_counts(result)
    assert counts.evaluable_prediction_count == 5
    assert counts.lifting_failed_count == 0
    assert counts.lifting_succeeded_count == 0


# ── _build_succeeded_record ───────────────────────────────────────────────────


def test_build_succeeded_record_status():
    initial = _initial_record()
    execution = MagicMock(spec=PredictDetectionExecution)
    execution.model_uri = None
    execution.endpoint_url = "http://sceneops-inference:8001"
    inference_result = _inference_result()
    artifacts = PredictDetectionArtifacts(
        prediction_manifest_uri="file:///pred_manifest.json",
        predictions_root_uri="file:///preds/",
    )
    record = HANDLER._build_succeeded_record(
        initial_record=initial,
        execution=execution,
        inference_result=inference_result,
        artifacts=artifacts,
    )
    assert record.status == RunStatus.SUCCEEDED
    assert record.prediction_manifest_uri == "file:///pred_manifest.json"
    assert record.sample_count == 5
    assert record.prediction_count == 8
    assert record.finished_at is not None


# ── _build_result ─────────────────────────────────────────────────────────────


def test_build_result_fields():
    params = MagicMock()
    params.model_id = "grounding-dino"
    params.model_version = "tiny"
    params.inference_backend = InferenceBackendType.GROUNDING_DINO

    execution = MagicMock(spec=PredictDetectionExecution)
    execution.inference_run_id = "infer-001"
    execution.params = params
    execution.model_uri = None
    execution.endpoint_url = "http://sceneops-inference:8001"

    artifacts = PredictDetectionArtifacts(
        prediction_manifest_uri="file:///pred_manifest.json",
        predictions_root_uri="file:///preds/",
    )
    counts = PredictionCounts(
        scene_count=2,
        sample_count=5,
        inference_request_count=4,
        prediction_count=8,
        evaluable_prediction_count=7,
        lifting_succeeded_count=5,
        lifting_failed_count=1,
    )
    result = HANDLER._build_result(
        execution=execution,
        inference_result=_inference_result(),
        artifacts=artifacts,
        counts=counts,
    )
    assert isinstance(result, PredictDetectionJobResult)
    assert result.inference_run_id == "infer-001"
    assert result.scene_count == 2
    assert result.sample_count == 5
    assert result.inference_request_count == 4
    assert result.prediction_count == 8
    assert result.evaluable_prediction_count == 7
    assert result.lifting_succeeded_count == 5
    assert result.lifting_failed_count == 1
    assert result.inference_backend == "grounding_dino"
