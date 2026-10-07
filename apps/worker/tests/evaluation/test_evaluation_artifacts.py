"""Evaluation manifest assembly: the manifest records the exact pins it
consumed and the checksum-pinned sample results it scored, and is a pure
function of them (no location, no timestamp), so its bytes are reproducible."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from sceneops_core.evaluations.schemas import (
    EvaluationInputs,
    EvaluationManifestError,
    EvaluationSampleShardRef,
    load_canonical_evaluation_manifest,
)
from sceneops_core.inference.schemas import (
    DetectionPredictionManifest,
    PredictionRevisionRef,
)
from sceneops_core.labels import LabelSetRef
from sceneops_worker.evaluation.detection.accumulation import EvaluationAccumulator
from sceneops_worker.evaluation.detection.artifacts import (
    build_final_evaluation_manifest,
    build_skipped_evaluation_manifest,
)
from sceneops_worker.evaluation.detection.base import DetectionEvaluationRequest
from tests.derived.labels_support import label_document

CHECKSUM = "sha256:" + "a" * 64
SHARD_CHECKSUM = "sha256:" + "b" * 64


def _request() -> DetectionEvaluationRequest:
    prediction = DetectionPredictionManifest(
        inference_run_id="infer-001",
        dataset_id="ds",
        dataset_version="v1",
        config={"model_id": "dummy", "model_version": "v1"},
        inputs=[],
        scene_count=0,
        sample_count=0,
        prediction_count=0,
        evaluable_prediction_count=0,
        lifting_succeeded_count=0,
        lifting_failed_count=0,
        lifting_not_applicable_count=0,
        prediction_shards=[],
    )
    return DetectionEvaluationRequest(
        evaluation_run_id="eval-001",
        inference_run_id="infer-001",
        dataset_id="ds",
        dataset_version="v1",
        inputs=EvaluationInputs(
            prediction=PredictionRevisionRef(
                inference_run_id="infer-001",
                manifest_artifact_id="predmanifest-1",
                manifest_checksum=CHECKSUM,
            ),
            label_set=LabelSetRef(
                label_set_id="gt",
                manifest_artifact_id="labelset-1",
                manifest_checksum=CHECKSUM,
            ),
        ),
        prediction=prediction,
        label_set=label_document("gt", covered=[1]),
        views={},
        run_artifact_store=MagicMock(),
        match_distance_m=2.0,
    )


def _accumulator() -> EvaluationAccumulator:
    accumulator = EvaluationAccumulator()
    accumulator.add(
        {
            "tp": 3,
            "fp": 1,
            "fn": 1,
            "total_center_distance_error": 1.5,
            "matched_count": 3,
            "prediction_count": 5,
            "class_metrics": {"vehicle.car": {"tp": 3, "fp": 1, "fn": 1}},
            "not_localized_prediction_count": 1,
        }
    )
    return accumulator


def _shards() -> list[EvaluationSampleShardRef]:
    return [
        EvaluationSampleShardRef(
            scene_id="scene-a",
            sample_id=sample_id,
            uri=f"file:///runs/evaluations/eval-001/samples/scene-a/{sample_id}.json",
            checksum=SHARD_CHECKSUM,
        )
        for sample_id in ("s2", "s1")
    ]


def test_skipped_manifest_records_inputs_and_reason():
    request = _request()
    manifest = build_skipped_evaluation_manifest(
        request=request, reason="no covered samples", metadata={"x": 1}
    )
    assert manifest.status == "skipped"
    assert manifest.inputs == request.inputs
    assert manifest.metadata == {"x": 1, "reason": "no covered samples"}
    assert (manifest.model_id, manifest.model_version) == ("dummy", "v1")
    assert manifest.sample_shards == []


def test_final_manifest_records_inputs_pinned_shards_and_metrics():
    request = _request()
    manifest = build_final_evaluation_manifest(
        request=request, accumulator=_accumulator(), sample_shards=_shards()
    )
    assert manifest.status == "succeeded"
    assert manifest.inputs == request.inputs
    assert manifest.sample_count == 2
    # Shards are ordered by (scene_id, sample_id), whatever order they arrived in.
    assert [s.sample_id for s in manifest.sample_shards] == ["s1", "s2"]
    assert manifest.evaluation_unit == "label"
    assert manifest.ground_truth_count == 4
    assert manifest.metrics["not_localized_prediction_count"] == 1
    assert manifest.primary_metric_name == "precision"
    assert manifest.primary_metric_value == 0.75


def test_manifest_bytes_are_a_pure_function_of_what_was_scored():
    request = _request()
    one = build_final_evaluation_manifest(
        request=request, accumulator=_accumulator(), sample_shards=_shards()
    )
    two = build_final_evaluation_manifest(
        request=request,
        accumulator=_accumulator(),
        sample_shards=list(reversed(_shards())),
    )
    assert one.to_canonical_bytes() == two.to_canonical_bytes()
    assert one.checksum() == two.checksum()
    # No self-location and no clock: nothing in the bytes depends on when or
    # where the manifest was written.
    document = one.model_dump(mode="json")
    assert not {"created_at", "evaluation_manifest_uri", "metrics_uri"} & set(document)


def test_a_different_result_is_a_different_revision():
    request = _request()
    one = build_final_evaluation_manifest(
        request=request, accumulator=_accumulator(), sample_shards=_shards()
    )
    changed = _shards()
    changed[0] = changed[0].model_copy(update={"checksum": "sha256:" + "c" * 64})
    two = build_final_evaluation_manifest(
        request=request, accumulator=_accumulator(), sample_shards=changed
    )
    assert one.checksum() != two.checksum()


def test_canonical_bytes_round_trip_and_reject_anything_else():
    manifest = build_final_evaluation_manifest(
        request=_request(), accumulator=_accumulator(), sample_shards=_shards()
    )
    data = manifest.to_canonical_bytes()
    assert load_canonical_evaluation_manifest(data) == manifest
    with pytest.raises(EvaluationManifestError, match="canonical"):
        load_canonical_evaluation_manifest(data.replace(b",", b", ", 1))
