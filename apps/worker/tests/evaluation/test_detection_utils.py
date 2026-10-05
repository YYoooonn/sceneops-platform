"""Center-distance matching of predictions against labels, and the
conditions under which a prediction is scored at all."""

from __future__ import annotations

import pytest

from sceneops_worker.evaluation.detection.utils import (
    FrameMismatchError,
    evaluate_sample,
    is_evaluable_prediction,
)
from tests.derived.labels_support import box_label


def _label(label_id, x, *, category="vehicle.car", frame="world"):
    return box_label(label_id, "run-001", 1, x=x, category=category, frame=frame)


def _pred(category, x, *, frame="world", lifting_status=None, pred_id="pred-001"):
    prediction = {
        "prediction_id": pred_id,
        "category_name": category,
        "frame_id": frame,
        "translation": [x, 2.0, 0.5],
        "score": 0.9,
    }
    if lifting_status is not None:
        prediction["lifting_status"] = lifting_status
    return prediction


def _evaluate(labels, predictions, **kwargs):
    return evaluate_sample(
        scene_id="scene-1",
        sample_id="smp-000000",
        labels=labels,
        predictions=predictions,
        match_distance_m=kwargs.pop("match_distance_m", 2.0),
    )


# ── what is scored ────────────────────────────────────────────────────────────


def test_only_localized_predictions_are_evaluable():
    assert is_evaluable_prediction(_pred("c", 1.0)) is True
    assert is_evaluable_prediction(_pred("c", 1.0, lifting_status="succeeded")) is True
    assert is_evaluable_prediction(_pred("c", 1.0, lifting_status="failed")) is False
    # A 2-D detection that could not be lifted names no frame.
    assert is_evaluable_prediction(_pred("c", 1.0, frame=None)) is False


def test_failed_and_unlocalized_predictions_never_inflate_false_positives():
    result = _evaluate(
        [_label("g", 10.0)],
        [
            _pred("vehicle.car", 0.0, lifting_status="failed", pred_id="p1"),
            _pred("vehicle.car", 0.0, frame=None, pred_id="p2"),
        ],
    )
    assert (result["tp"], result["fp"], result["fn"]) == (0, 0, 1)
    assert result["lifting_failed_prediction_count"] == 1
    assert result["not_localized_prediction_count"] == 1
    assert result["evaluable_prediction_count"] == 0
    assert result["prediction_count"] == 2


# ── matching ──────────────────────────────────────────────────────────────────


def test_a_prediction_at_the_label_center_matches():
    result = _evaluate([_label("g", 10.0)], [_pred("vehicle.car", 10.0)])
    assert (result["tp"], result["fp"], result["fn"]) == (1, 0, 0)
    assert result["matches"][0]["label_id"] == "g"
    assert result["precision"] == result["recall"] == 1.0


def test_distance_and_category_both_gate_a_match():
    far = _evaluate([_label("g", 10.0)], [_pred("vehicle.car", 14.0)])
    assert (far["tp"], far["fp"], far["fn"]) == (0, 1, 1)
    wrong = _evaluate([_label("g", 10.0)], [_pred("human.pedestrian", 10.0)])
    assert (wrong["tp"], wrong["fp"], wrong["fn"]) == (0, 1, 1)
    assert wrong["class_metrics"]["human.pedestrian"]["fp"] == 1
    assert wrong["class_metrics"]["vehicle.car"]["fn"] == 1


def test_each_label_is_matched_at_most_once():
    result = _evaluate(
        [_label("g", 10.0)],
        [
            _pred("vehicle.car", 10.0, pred_id="a"),
            _pred("vehicle.car", 10.5, pred_id="b"),
        ],
    )
    assert (result["tp"], result["fp"], result["fn"]) == (1, 1, 0)


def test_a_covered_sample_without_labels_scores_every_prediction_as_false_positive():
    result = _evaluate([], [_pred("vehicle.car", 10.0)])
    assert (result["tp"], result["fp"], result["fn"]) == (0, 1, 0)


# ── frames ────────────────────────────────────────────────────────────────────


def test_a_prediction_and_label_in_different_frames_are_never_compared():
    with pytest.raises(FrameMismatchError, match="no frame transform is applied"):
        _evaluate(
            [_label("g", 10.0, frame="world")],
            [_pred("vehicle.car", 10.0, frame="ego")],
        )
