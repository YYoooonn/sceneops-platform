"""Unit tests for EvaluateDetectionJobHandler.

Tests cover:
- build_job_params validation
- _require_ready_dataset_version
- _require_inference_run
- _validate_inference_run_matches_dataset
- _extract_evaluation_counts
- _write_metrics_artifact payload
- artifact registration
- _build_succeeded_record
- _build_result
- pipeline output spec coverage
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from sceneops_core.datasets.schemas.enums import DatasetVersionStatus
from sceneops_core.datasets.schemas.records import DatasetVersionRecord
from sceneops_core.evaluations.schemas.manifests import DetectionEvaluationManifest
from sceneops_core.evaluations.schemas.runs import EvaluationRunRecord
from sceneops_core.inference.schemas.runs import InferenceRunRecord
from sceneops_core.pipelines.schemas import PipelineTaskInputs
from sceneops_core.runs.schemas import RunStatus
from sceneops_worker.jobs.evaluation.evaluate_detection import (
    EvaluateDetectionArtifacts,
    EvaluateDetectionJobHandler,
)


# ── helpers ───────────────────────────────────────────────────────────────────

HANDLER = EvaluateDetectionJobHandler()

DATASET_ID = "nuscenes"
DATASET_VERSION = "v1.0-mini"
INFERENCE_RUN_ID = "infer-001"
EVALUATION_RUN_ID = "eval-001"


def _dataset_version(
    status: DatasetVersionStatus = DatasetVersionStatus.READY,
    manifest_uri: str | None = "file:///manifest.json",
) -> DatasetVersionRecord:
    return DatasetVersionRecord(
        dataset_id=DATASET_ID,
        version=DATASET_VERSION,
        status=status,
        manifest_uri=manifest_uri,
    )


def _inference_run(
    status: RunStatus = RunStatus.SUCCEEDED,
    dataset_id: str = DATASET_ID,
    dataset_version: str = DATASET_VERSION,
    prediction_manifest_uri: str | None = "file:///pred_manifest.json",
) -> InferenceRunRecord:
    return InferenceRunRecord(
        run_id=INFERENCE_RUN_ID,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        model_id="grounding-dino",
        model_version="tiny",
        inference_backend="grounding_dino",
        status=status,
        prediction_manifest_uri=prediction_manifest_uri,
    )


def _evaluation_manifest(**overrides) -> DetectionEvaluationManifest:
    defaults = dict(
        evaluation_run_id=EVALUATION_RUN_ID,
        inference_run_id=INFERENCE_RUN_ID,
        dataset_id=DATASET_ID,
        dataset_version=DATASET_VERSION,
        model_id="grounding-dino",
        model_version="tiny",
        status="succeeded",
        match_distance_m=2.0,
        sample_count=5,
        prediction_count=20,
        evaluable_prediction_count=18,
        lifting_failed_prediction_count=2,
        ground_truth_count=15,
        evaluation_unit="annotation",
        primary_metric_name="precision",
        primary_metric_value=0.75,
        evaluation_manifest_uri="file:///eval_manifest.json",
        metrics_uri="file:///metrics.json",
        samples_root_uri="file:///samples/",
        metrics={"precision": 0.75, "recall": 0.6},
        class_metrics={"vehicle.car": {"precision": 0.8}},
    )
    defaults.update(overrides)
    return DetectionEvaluationManifest(**defaults)


def _initial_record() -> EvaluationRunRecord:
    return EvaluationRunRecord(
        run_id=EVALUATION_RUN_ID,
        inference_run_id=INFERENCE_RUN_ID,
        dataset_id=DATASET_ID,
        dataset_version=DATASET_VERSION,
        evaluator_id="center-distance",
        status=RunStatus.RUNNING,
    )


def _execution(inference_run: InferenceRunRecord | None = None) -> MagicMock:
    # Use plain MagicMock (no spec) — frozen dataclass spec blocks attribute assignment.
    exe = MagicMock()
    exe.evaluation_run_id = EVALUATION_RUN_ID
    exe.params.dataset_id = DATASET_ID
    exe.params.dataset_version = DATASET_VERSION
    exe.params.inference_run_id = INFERENCE_RUN_ID
    exe.params.evaluator_id = "center-distance"
    exe.params.match_distance_m = 2.0
    exe.inference_run_record = inference_run or _inference_run()
    exe.job.job_id = "job-001"
    exe.job.pipeline_run_id = "pipeline-001"
    return exe


# ── build_job_params ──────────────────────────────────────────────────────────


def test_build_job_params_requires_inference_run_id():
    inputs = MagicMock(spec=PipelineTaskInputs)
    inputs.refs = {}
    with pytest.raises(ValueError, match="inference_run_id is required"):
        HANDLER.build_job_params(inputs)


def test_build_job_params_includes_inference_run_id():
    inputs = MagicMock()
    inputs.refs = {"inference_run_id": "infer-xyz"}
    inputs.dataset.dataset_id = DATASET_ID
    inputs.dataset.dataset_version = DATASET_VERSION
    inputs.params = {}
    result = HANDLER.build_job_params(inputs)
    assert result["inference_run_id"] == "infer-xyz"


# ── _require_ready_dataset_version ────────────────────────────────────────────


async def test_require_dataset_version_not_found():
    context = MagicMock()
    context.dataset_store.get_version = AsyncMock(return_value=None)
    with pytest.raises(ValueError, match="not found"):
        await EvaluateDetectionJobHandler._require_ready_dataset_version(
            context, DATASET_ID, DATASET_VERSION
        )


async def test_require_dataset_version_not_ready():
    context = MagicMock()
    context.dataset_store.get_version = AsyncMock(
        return_value=_dataset_version(status=DatasetVersionStatus.INGESTING)
    )
    with pytest.raises(ValueError, match="not usable"):
        await EvaluateDetectionJobHandler._require_ready_dataset_version(
            context, DATASET_ID, DATASET_VERSION
        )


async def test_require_dataset_version_no_manifest_uri():
    context = MagicMock()
    context.dataset_store.get_version = AsyncMock(
        return_value=_dataset_version(manifest_uri=None)
    )
    with pytest.raises(ValueError, match="manifest_uri"):
        await EvaluateDetectionJobHandler._require_ready_dataset_version(
            context, DATASET_ID, DATASET_VERSION
        )


async def test_require_dataset_version_ok():
    context = MagicMock()
    context.dataset_store.get_version = AsyncMock(return_value=_dataset_version())
    result = await EvaluateDetectionJobHandler._require_ready_dataset_version(
        context, DATASET_ID, DATASET_VERSION
    )
    assert result.dataset_id == DATASET_ID


# ── _require_inference_run ────────────────────────────────────────────────────


async def test_require_inference_run_not_found():
    context = MagicMock()
    context.runs.inference.get = AsyncMock(return_value=None)
    with pytest.raises(ValueError, match="not found"):
        await EvaluateDetectionJobHandler._require_inference_run(
            context, INFERENCE_RUN_ID
        )


async def test_require_inference_run_not_succeeded():
    context = MagicMock()
    context.runs.inference.get = AsyncMock(
        return_value=_inference_run(status=RunStatus.RUNNING)
    )
    with pytest.raises(ValueError, match="not complete"):
        await EvaluateDetectionJobHandler._require_inference_run(
            context, INFERENCE_RUN_ID
        )


async def test_require_inference_run_no_prediction_manifest_uri():
    context = MagicMock()
    context.runs.inference.get = AsyncMock(
        return_value=_inference_run(prediction_manifest_uri=None)
    )
    with pytest.raises(ValueError, match="prediction_manifest_uri"):
        await EvaluateDetectionJobHandler._require_inference_run(
            context, INFERENCE_RUN_ID
        )


async def test_require_inference_run_ok():
    context = MagicMock()
    context.runs.inference.get = AsyncMock(return_value=_inference_run())
    result = await EvaluateDetectionJobHandler._require_inference_run(
        context, INFERENCE_RUN_ID
    )
    assert result.run_id == INFERENCE_RUN_ID


# ── _validate_inference_run_matches_dataset ───────────────────────────────────


def test_validate_dataset_match_ok():
    EvaluateDetectionJobHandler._validate_inference_run_matches_dataset(
        inference_run=_inference_run(),
        dataset_id=DATASET_ID,
        dataset_version=DATASET_VERSION,
    )


def test_validate_dataset_id_mismatch_fails():
    with pytest.raises(ValueError, match="dataset mismatch"):
        EvaluateDetectionJobHandler._validate_inference_run_matches_dataset(
            inference_run=_inference_run(dataset_id="other-dataset"),
            dataset_id=DATASET_ID,
            dataset_version=DATASET_VERSION,
        )


def test_validate_dataset_version_mismatch_fails():
    with pytest.raises(ValueError, match="dataset mismatch"):
        EvaluateDetectionJobHandler._validate_inference_run_matches_dataset(
            inference_run=_inference_run(dataset_version="v2.0"),
            dataset_id=DATASET_ID,
            dataset_version=DATASET_VERSION,
        )


# ── _extract_evaluation_counts ────────────────────────────────────────────────


def test_extract_evaluation_counts():
    manifest = _evaluation_manifest()
    counts = HANDLER._extract_evaluation_counts(manifest)
    assert counts.sample_count == 5
    assert counts.prediction_count == 20
    assert counts.evaluable_prediction_count == 18
    assert counts.lifting_failed_prediction_count == 2
    assert counts.ground_truth_count == 15
    assert counts.evaluation_unit == "annotation"
    assert counts.primary_metric_name == "precision"
    assert counts.primary_metric_value == 0.75


def test_extract_evaluation_counts_defaults_none_fields():
    manifest = _evaluation_manifest(
        sample_count=None,
        prediction_count=None,
        evaluable_prediction_count=None,
        lifting_failed_prediction_count=None,
        ground_truth_count=None,
        evaluation_unit=None,
    )
    counts = HANDLER._extract_evaluation_counts(manifest)
    assert counts.sample_count == 0
    assert counts.prediction_count == 0
    assert counts.evaluable_prediction_count == 0
    assert counts.lifting_failed_prediction_count == 0
    assert counts.ground_truth_count == 0
    assert counts.evaluation_unit == "annotation"


# ── _write_metrics_artifact payload ──────────────────────────────────────────


async def test_metrics_artifact_contains_evaluable_prediction_count():
    context = MagicMock()
    captured: list[dict] = []

    async def fake_write(evaluation_run_id, metrics):
        captured.append(metrics)
        return "file:///metrics.json"

    context.run_artifact_store.write_evaluation_run_metrics = fake_write

    exe = _execution()
    exe.context = context

    manifest = _evaluation_manifest()
    counts = HANDLER._extract_evaluation_counts(manifest)

    await EvaluateDetectionJobHandler._write_metrics_artifact(
        execution=exe,
        evaluation_manifest=manifest,
        counts=counts,
    )

    assert len(captured) == 1
    payload = captured[0]
    assert payload["evaluable_prediction_count"] == 18


async def test_metrics_artifact_contains_lifting_failed_prediction_count():
    context = MagicMock()
    captured: list[dict] = []

    async def fake_write(evaluation_run_id, metrics):
        captured.append(metrics)
        return "file:///metrics.json"

    context.run_artifact_store.write_evaluation_run_metrics = fake_write

    exe = _execution()
    exe.context = context

    manifest = _evaluation_manifest()
    counts = HANDLER._extract_evaluation_counts(manifest)

    await EvaluateDetectionJobHandler._write_metrics_artifact(
        execution=exe,
        evaluation_manifest=manifest,
        counts=counts,
    )

    payload = captured[0]
    assert payload["lifting_failed_prediction_count"] == 2


async def test_metrics_artifact_contains_full_context():
    context = MagicMock()
    captured: list[dict] = []

    async def fake_write(evaluation_run_id, metrics):
        captured.append(metrics)
        return "file:///metrics.json"

    context.run_artifact_store.write_evaluation_run_metrics = fake_write

    exe = _execution()
    exe.context = context

    manifest = _evaluation_manifest()
    counts = HANDLER._extract_evaluation_counts(manifest)

    await EvaluateDetectionJobHandler._write_metrics_artifact(
        execution=exe,
        evaluation_manifest=manifest,
        counts=counts,
    )

    payload = captured[0]
    assert payload["evaluation_run_id"] == EVALUATION_RUN_ID
    assert payload["inference_run_id"] == INFERENCE_RUN_ID
    assert payload["dataset_id"] == DATASET_ID
    assert payload["model_id"] == "grounding-dino"
    assert payload["evaluator_id"] == "center-distance"
    assert payload["match_distance_m"] == 2.0


# ── artifact registration ─────────────────────────────────────────────────────


async def test_register_artifacts_creates_evaluation_manifest_and_metrics():
    context = MagicMock()
    created_kinds: list[str] = []

    async def fake_create(*, artifact_id, ref, **kwargs):
        created_kinds.append(ref.kind.value)

    context.artifact_record_store.create = fake_create

    exe = _execution()
    exe.context = context

    result = await EvaluateDetectionJobHandler._register_artifacts(
        execution=exe,
        evaluation_manifest_uri="file:///eval_manifest.json",
        metrics_uri="file:///metrics.json",
    )

    assert "evaluation_manifest" in created_kinds
    assert "metrics" in created_kinds
    assert result.evaluation_manifest_uri == "file:///eval_manifest.json"
    assert result.metrics_uri == "file:///metrics.json"


# ── _build_succeeded_record ───────────────────────────────────────────────────


def test_build_succeeded_record_status_and_fields():
    initial = _initial_record()
    exe = _execution()
    manifest = _evaluation_manifest()
    artifacts = EvaluateDetectionArtifacts(
        evaluation_manifest_uri="file:///eval_manifest.json",
        metrics_uri="file:///metrics.json",
    )
    counts = HANDLER._extract_evaluation_counts(manifest)

    record = HANDLER._build_succeeded_record(
        initial_record=initial,
        execution=exe,
        evaluation_manifest=manifest,
        artifacts=artifacts,
        counts=counts,
    )

    assert record.status == RunStatus.SUCCEEDED
    assert record.model_id == "grounding-dino"
    assert record.model_version == "tiny"
    assert record.sample_count == 5
    assert record.prediction_count == 20
    assert record.ground_truth_count == 15
    assert record.primary_metric_name == "precision"
    assert record.primary_metric_value == 0.75
    assert record.evaluation_manifest_uri == "file:///eval_manifest.json"
    assert record.metrics_uri == "file:///metrics.json"
    assert record.finished_at is not None


def test_build_succeeded_record_summary_includes_evaluability_counts():
    initial = _initial_record()
    exe = _execution()
    manifest = _evaluation_manifest()
    artifacts = EvaluateDetectionArtifacts(
        evaluation_manifest_uri="file:///eval_manifest.json",
        metrics_uri="file:///metrics.json",
    )
    counts = HANDLER._extract_evaluation_counts(manifest)

    record = HANDLER._build_succeeded_record(
        initial_record=initial,
        execution=exe,
        evaluation_manifest=manifest,
        artifacts=artifacts,
        counts=counts,
    )

    assert record.summary["evaluable_prediction_count"] == 18
    assert record.summary["lifting_failed_prediction_count"] == 2


# ── _build_result ─────────────────────────────────────────────────────────────


def test_build_result_fields():
    exe = _execution()
    manifest = _evaluation_manifest()
    artifacts = EvaluateDetectionArtifacts(
        evaluation_manifest_uri="file:///eval_manifest.json",
        metrics_uri="file:///metrics.json",
    )
    counts = HANDLER._extract_evaluation_counts(manifest)

    result = HANDLER._build_result(
        execution=exe,
        evaluation_manifest=manifest,
        artifacts=artifacts,
        counts=counts,
    )

    assert result.evaluation_run_id == EVALUATION_RUN_ID
    assert result.inference_run_id == INFERENCE_RUN_ID
    assert result.model_id == "grounding-dino"
    assert result.sample_count == 5
    assert result.evaluable_prediction_count == 18
    assert result.lifting_failed_prediction_count == 2
    assert result.ground_truth_count == 15
    assert result.primary_metric_name == "precision"
    assert result.primary_metric_value == 0.75
    assert result.metadata["evaluator_id"] == "center-distance"
    assert result.metadata["match_distance_m"] == 2.0


def test_build_result_summary_consistent_with_record():
    exe = _execution()
    manifest = _evaluation_manifest()
    artifacts = EvaluateDetectionArtifacts(
        evaluation_manifest_uri="file:///eval_manifest.json",
        metrics_uri="file:///metrics.json",
    )
    counts = HANDLER._extract_evaluation_counts(manifest)
    initial = _initial_record()

    record = HANDLER._build_succeeded_record(
        initial_record=initial,
        execution=exe,
        evaluation_manifest=manifest,
        artifacts=artifacts,
        counts=counts,
    )
    result = HANDLER._build_result(
        execution=exe,
        evaluation_manifest=manifest,
        artifacts=artifacts,
        counts=counts,
    )

    assert record.summary == result.summary


# ── pipeline output spec ──────────────────────────────────────────────────────


def test_pipeline_output_spec_includes_evaluable_prediction_count():
    from sceneops_core.pipelines.builtin import _EVALUATE_DETECTION_OUTPUTS

    sources = {spec.source for spec in _EVALUATE_DETECTION_OUTPUTS}
    assert "evaluable_prediction_count" in sources


def test_pipeline_output_spec_includes_lifting_failed_prediction_count():
    from sceneops_core.pipelines.builtin import _EVALUATE_DETECTION_OUTPUTS

    sources = {spec.source for spec in _EVALUATE_DETECTION_OUTPUTS}
    assert "lifting_failed_prediction_count" in sources


def test_pipeline_output_spec_includes_sample_count():
    from sceneops_core.pipelines.builtin import _EVALUATE_DETECTION_OUTPUTS

    sources = {spec.source for spec in _EVALUATE_DETECTION_OUTPUTS}
    assert "sample_count" in sources


def test_pipeline_output_spec_does_not_have_annotation_count():
    """annotation_count was a legacy alias for ground_truth_count — removed."""
    from sceneops_core.pipelines.builtin import _EVALUATE_DETECTION_OUTPUTS

    sources = {spec.source for spec in _EVALUATE_DETECTION_OUTPUTS}
    assert "annotation_count" not in sources


def test_pipeline_output_spec_sources_exist_on_job_result():
    """Every source field must exist on EvaluateDetectionJobResult."""
    from sceneops_core.jobs.schemas.results.detection import EvaluateDetectionJobResult
    from sceneops_core.pipelines.builtin import _EVALUATE_DETECTION_OUTPUTS

    result_fields = set(EvaluateDetectionJobResult.model_fields.keys())
    for spec in _EVALUATE_DETECTION_OUTPUTS:
        assert spec.source in result_fields, (
            f"Pipeline output spec source={spec.source!r} not found in "
            f"EvaluateDetectionJobResult fields: {sorted(result_fields)}"
        )
