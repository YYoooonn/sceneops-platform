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

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sceneops_core.datasets.schemas.enums import DatasetVersionStatus
from sceneops_core.datasets.schemas.records import DatasetVersionRecord
from sceneops_core.inference.enums import InferenceBackendType
from sceneops_core.inference.schemas.detection import DetectionInferenceResult
from sceneops_core.inference.schemas.runs import InferenceRunRecord
from sceneops_core.jobs.schemas.params.detection import (
    DetectionSceneSelectionConfig,
    EvaluateDetectionJobParams,
    PredictDetectionJobParams,
)
from sceneops_core.jobs.schemas.results.detection import PredictDetectionJobResult
from sceneops_core.models.schemas.enums import ModelBackend
from sceneops_core.models.schemas.records import ModelVersionRecord
from sceneops_core.runs.schemas import RunStatus
from sceneops_worker.jobs.inference.predict_detection import (
    PredictDetectionArtifacts,
    PredictDetectionExecution,
    PredictDetectionInputs,
    PredictDetectionJobHandler,
    PredictionCounts,
)
from sceneops_worker.scenarios.resolver import ResolvedScenarioSet


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


def _make_inputs(
    resolved_scenario_set: ResolvedScenarioSet | None = None,
) -> PredictDetectionInputs:
    return PredictDetectionInputs(
        dataset_manifest=MagicMock(),
        dataset_manifest_uri="file:///manifest.json",
        selected_scene_ids=["scene-001"],
        scene_selection_metadata={"selected_scene_count": 1},
        resolved_scenario_set=resolved_scenario_set,
    )


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
        inputs=_make_inputs(),
    )
    assert record.status == RunStatus.SUCCEEDED
    assert record.prediction_manifest_uri == "file:///pred_manifest.json"
    assert record.sample_count == 5
    assert record.prediction_count == 8
    assert record.finished_at is not None


def test_build_succeeded_record_no_scenario_set_has_no_scenario_metadata():
    initial = _initial_record()
    execution = MagicMock(spec=PredictDetectionExecution)
    execution.model_uri = None
    execution.endpoint_url = "http://sceneops-inference:8001"
    record = HANDLER._build_succeeded_record(
        initial_record=initial,
        execution=execution,
        inference_result=_inference_result(),
        artifacts=PredictDetectionArtifacts(
            prediction_manifest_uri="file:///pred_manifest.json",
            predictions_root_uri=None,
        ),
        inputs=_make_inputs(resolved_scenario_set=None),
    )
    assert "scenario_set_id" not in (record.metadata or {})
    assert "scenario_set_uri" not in (record.metadata or {})


def test_build_succeeded_record_includes_scenario_set_metadata():
    resolved = ResolvedScenarioSet(
        scenario_set_id="scset-001",
        scenario_set_uri="s3://bucket/candidates.json",
        candidate_scene_ids=["scene-a", "scene-b"],
        selected_scene_ids=["scene-a", "scene-b"],
        rejected_scene_ids=[],
        candidate_count=2,
        selected_count=2,
        rejected_count=5,
    )
    initial = _initial_record()
    execution = MagicMock(spec=PredictDetectionExecution)
    execution.model_uri = None
    execution.endpoint_url = "http://sceneops-inference:8001"
    record = HANDLER._build_succeeded_record(
        initial_record=initial,
        execution=execution,
        inference_result=_inference_result(),
        artifacts=PredictDetectionArtifacts(
            prediction_manifest_uri="file:///pred_manifest.json",
            predictions_root_uri=None,
        ),
        inputs=_make_inputs(resolved_scenario_set=resolved),
    )
    meta = record.metadata or {}
    assert meta["scenario_set_id"] == "scset-001"
    assert meta["scenario_set_uri"] == "s3://bucket/candidates.json"
    assert meta["scenario_candidate_count"] == 2
    assert meta["scenario_selected_count"] == 2
    assert meta["scenario_rejected_count"] == 5
    assert "scene_selection" in meta


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


# ── scenario_set_id contract ──────────────────────────────────────────────────


def test_predict_detection_params_scenario_set_id_accepted():
    params = PredictDetectionJobParams(
        dataset_id="nuscenes",
        dataset_version="v1.0-mini",
        model_id="grounding-dino",
        model_version="tiny",
        scenario_set_id="scset-test",
    )
    assert params.scenario_set_id == "scset-test"


def test_predict_detection_params_scenario_set_id_defaults_none():
    params = PredictDetectionJobParams(
        dataset_id="nuscenes",
        dataset_version="v1.0-mini",
        model_id="grounding-dino",
        model_version="tiny",
    )
    assert params.scenario_set_id is None


def test_evaluate_detection_params_scenario_set_id_accepted():
    params = EvaluateDetectionJobParams(
        dataset_id="nuscenes",
        dataset_version="v1.0-mini",
        inference_run_id="infer-001",
        scenario_set_id="scset-test",
    )
    assert params.scenario_set_id == "scset-test"


def test_evaluate_detection_params_scenario_set_id_defaults_none():
    params = EvaluateDetectionJobParams(
        dataset_id="nuscenes",
        dataset_version="v1.0-mini",
        inference_run_id="infer-001",
    )
    assert params.scenario_set_id is None


# ── _resolve_inputs — ScenarioSet wiring ─────────────────────────────────────


def _make_execution(scenario_set_id: str | None = None) -> MagicMock:
    """Build a minimal mock PredictDetectionExecution for _resolve_inputs tests.

    Uses a plain MagicMock (no spec) so that context and its nested attributes
    can be set freely — frozen dataclass fields are not enumerable via dir() at
    class level, which makes spec= restrict access to them.
    """
    execution = MagicMock()

    params = MagicMock()
    params.scenario_set_id = scenario_set_id
    params.scene_selection = DetectionSceneSelectionConfig()
    execution.params = params

    execution.dataset_version_record = _dataset_version()

    context = MagicMock()
    manifest = MagicMock()
    manifest.scenes = []
    context.dataset_artifact_store.load_dataset_manifest = AsyncMock(
        return_value=manifest
    )
    context.scene_artifact_store = MagicMock()
    execution.context = context

    return execution


@patch("sceneops_worker.jobs.inference.predict_detection.select_detection_scenes")
@pytest.mark.asyncio
async def test_resolve_inputs_no_scenario_set_passes_none_to_selection(mock_select):
    mock_select.return_value = {
        "selected_scene_ids": [],
        "selected_scene_count": 0,
        "selected_sample_count": 0,
        "selected_annotation_count": 0,
        "total_scene_count": 0,
        "inspected_scene_count": 0,
        "skipped_scene_count": 0,
        "skipped_scenes": [],
        "mode": "all",
        "requested_scene_count": 0,
        "requested_scene_ids": [],
        "max_scenes": None,
        "max_samples": None,
        "max_samples_per_scene": None,
        "min_annotation_count": None,
        "ground_truth_sources": [],
    }
    execution = _make_execution(scenario_set_id=None)
    result = await PredictDetectionJobHandler._resolve_inputs(execution)

    _call_kwargs = mock_select.call_args.kwargs
    assert _call_kwargs["scenario_set_scene_ids"] is None
    assert result.resolved_scenario_set is None


@patch("sceneops_worker.jobs.inference.predict_detection.ScenarioSetSceneResolver")
@patch("sceneops_worker.jobs.inference.predict_detection.select_detection_scenes")
@pytest.mark.asyncio
async def test_resolve_inputs_with_scenario_set_id_calls_resolver_and_passes_ids(
    mock_select, MockResolver
):
    resolved = ResolvedScenarioSet(
        scenario_set_id="scset-001",
        scenario_set_uri="s3://bucket/candidates.json",
        candidate_scene_ids=["scene-a", "scene-b"],
        selected_scene_ids=["scene-a", "scene-b"],
        rejected_scene_ids=[],
        candidate_count=2,
        selected_count=2,
        rejected_count=3,
    )
    mock_resolver_instance = MagicMock()
    mock_resolver_instance.resolve = AsyncMock(return_value=resolved)
    MockResolver.return_value = mock_resolver_instance

    mock_select.return_value = {
        "selected_scene_ids": ["scene-a", "scene-b"],
        "selected_scene_count": 2,
        "selected_sample_count": 0,
        "selected_annotation_count": 0,
        "total_scene_count": 0,
        "inspected_scene_count": 0,
        "skipped_scene_count": 0,
        "skipped_scenes": [],
        "mode": "all",
        "requested_scene_count": 0,
        "requested_scene_ids": [],
        "max_scenes": None,
        "max_samples": None,
        "max_samples_per_scene": None,
        "min_annotation_count": None,
        "ground_truth_sources": [],
    }

    execution = _make_execution(scenario_set_id="scset-001")
    result = await PredictDetectionJobHandler._resolve_inputs(execution)

    mock_resolver_instance.resolve.assert_awaited_once_with("scset-001")
    _call_kwargs = mock_select.call_args.kwargs
    assert _call_kwargs["scenario_set_scene_ids"] == {"scene-a", "scene-b"}
    assert result.resolved_scenario_set is resolved


@patch("sceneops_worker.jobs.inference.predict_detection.ScenarioSetSceneResolver")
@patch("sceneops_worker.jobs.inference.predict_detection.select_detection_scenes")
@pytest.mark.asyncio
async def test_resolve_inputs_empty_scenario_set_passes_empty_set_not_none(
    mock_select, MockResolver
):
    """Empty ScenarioSet passes set() (not None) so all scenes get not_in_scenario_set."""
    resolved = ResolvedScenarioSet(
        scenario_set_id="scset-empty",
        scenario_set_uri="s3://bucket/candidates.json",
        candidate_scene_ids=[],
        selected_scene_ids=[],
        rejected_scene_ids=[],
        candidate_count=0,
        selected_count=0,
        rejected_count=10,
    )
    mock_resolver_instance = MagicMock()
    mock_resolver_instance.resolve = AsyncMock(return_value=resolved)
    MockResolver.return_value = mock_resolver_instance

    mock_select.return_value = {
        "selected_scene_ids": [],
        "selected_scene_count": 0,
        "selected_sample_count": 0,
        "selected_annotation_count": 0,
        "total_scene_count": 5,
        "inspected_scene_count": 0,
        "skipped_scene_count": 5,
        "skipped_scenes": [],
        "mode": "all",
        "requested_scene_count": 0,
        "requested_scene_ids": [],
        "max_scenes": None,
        "max_samples": None,
        "max_samples_per_scene": None,
        "min_annotation_count": None,
        "ground_truth_sources": [],
    }

    execution = _make_execution(scenario_set_id="scset-empty")
    await PredictDetectionJobHandler._resolve_inputs(execution)

    _call_kwargs = mock_select.call_args.kwargs
    # Must be an empty set, not None — ensures not_in_scenario_set skip reason applies
    assert _call_kwargs["scenario_set_scene_ids"] == set()
    assert _call_kwargs["scenario_set_scene_ids"] is not None


@patch("sceneops_worker.jobs.inference.predict_detection.ScenarioSetSceneResolver")
@pytest.mark.asyncio
async def test_resolve_inputs_resolver_failure_propagates(MockResolver):
    """If the resolver raises, the error propagates — no silent fallback."""
    mock_resolver_instance = MagicMock()
    mock_resolver_instance.resolve = AsyncMock(
        side_effect=ValueError("ScenarioSet not found: 'scset-bad'")
    )
    MockResolver.return_value = mock_resolver_instance

    execution = _make_execution(scenario_set_id="scset-bad")
    with pytest.raises(ValueError, match="ScenarioSet not found"):
        await PredictDetectionJobHandler._resolve_inputs(execution)
