"""Tests for frustum lifting filter in detection evaluation utils."""

from __future__ import annotations

from unittest.mock import MagicMock


from sceneops_worker.evaluation.utils import evaluate_sample, is_evaluable_prediction


# ── is_evaluable_prediction ───────────────────────────────────────────────────


def test_is_evaluable_no_lifting_status():
    """Old-style predictions (no lifting_status) are evaluable."""
    assert is_evaluable_prediction({"category_name": "vehicle.car"}) is True


def test_is_evaluable_succeeded():
    assert is_evaluable_prediction({"lifting_status": "succeeded"}) is True


def test_is_evaluable_not_applicable():
    assert is_evaluable_prediction({"lifting_status": "not_applicable"}) is True


def test_is_evaluable_failed():
    assert is_evaluable_prediction({"lifting_status": "failed"}) is False


# ── evaluate_sample ───────────────────────────────────────────────────────────


def _make_sample(annotations: list[MagicMock]) -> MagicMock:
    sample = MagicMock()
    sample.scene_id = "scene-001"
    sample.sample_id = "sample-001"
    sample.annotations = annotations
    return sample


def _make_annotation(category: str, translation: list[float]) -> MagicMock:
    ann = MagicMock()
    ann.category = category
    ann.translation = translation
    ann.annotation_id = f"ann-{id(ann)}"
    return ann


def _pred(
    category: str,
    translation: list[float],
    lifting_status: str | None = None,
    pred_id: str = "pred-001",
) -> dict:
    p = {
        "prediction_id": pred_id,
        "category_name": category,
        "translation": translation,
        "score": 0.9,
    }
    if lifting_status is not None:
        p["lifting_status"] = lifting_status
    return p


# ── failed predictions excluded from matching ─────────────────────────────────


def test_failed_prediction_not_counted_as_fp():
    """A failed-lift prediction at [0,0,0] must not inflate FP count."""
    gt = _make_annotation("vehicle.car", [10.0, 10.0, 0.0])
    sample = _make_sample([gt])

    failed_pred = _pred("vehicle.car", [0.0, 0.0, 0.0], lifting_status="failed")
    result = evaluate_sample(
        sample=sample,
        predictions=[failed_pred],
        match_distance_m=2.0,
    )

    assert result["tp"] == 0
    assert result["fp"] == 0  # failed excluded
    assert result["fn"] == 1  # GT unmatched
    assert result["lifting_failed_prediction_count"] == 1
    assert result["evaluable_prediction_count"] == 0
    assert result["prediction_count"] == 1


def test_failed_prediction_excluded_precision_recall():
    """Precision/recall uses evaluable_predictions only."""
    gt = _make_annotation("vehicle.car", [1.0, 0.0, 0.0])
    sample = _make_sample([gt])

    good_pred = _pred("vehicle.car", [1.0, 0.0, 0.0], lifting_status="succeeded")
    failed_pred = _pred(
        "vehicle.car", [0.0, 0.0, 0.0], lifting_status="failed", pred_id="pred-002"
    )

    result = evaluate_sample(
        sample=sample,
        predictions=[good_pred, failed_pred],
        match_distance_m=2.0,
    )

    # good_pred matches the GT → TP=1, FP=0 (failed excluded)
    assert result["tp"] == 1
    assert result["fp"] == 0
    assert result["fn"] == 0
    assert result["precision"] == 1.0
    assert result["recall"] == 1.0
    assert result["lifting_failed_prediction_count"] == 1
    assert result["evaluable_prediction_count"] == 1


# ── not_applicable and legacy predictions remain evaluable ────────────────────


def test_not_applicable_prediction_is_evaluable():
    """'not_applicable' predictions still participate in matching (as FP if unmatched)."""
    gt = _make_annotation("vehicle.car", [50.0, 50.0, 0.0])
    sample = _make_sample([gt])

    na_pred = _pred("vehicle.car", [0.0, 0.0, 0.0], lifting_status="not_applicable")
    result = evaluate_sample(
        sample=sample,
        predictions=[na_pred],
        match_distance_m=2.0,
    )

    assert result["fp"] == 1  # not_applicable is evaluated, just FP here
    assert result["lifting_failed_prediction_count"] == 0
    assert result["evaluable_prediction_count"] == 1


def test_legacy_prediction_no_status_is_evaluable():
    """Mock predictions without lifting_status are treated as evaluable."""
    gt = _make_annotation("vehicle.car", [1.0, 0.0, 0.0])
    sample = _make_sample([gt])

    legacy_pred = _pred("vehicle.car", [1.0, 0.0, 0.0])  # no lifting_status
    result = evaluate_sample(
        sample=sample,
        predictions=[legacy_pred],
        match_distance_m=2.0,
    )

    assert result["tp"] == 1
    assert result["fp"] == 0
    assert result["lifting_failed_prediction_count"] == 0


# ── lifting counts returned correctly ────────────────────────────────────────


def test_evaluate_sample_returns_lifting_counts():
    sample = _make_sample([])  # no GT

    preds = [
        _pred("vehicle.car", [0.0, 0.0, 0.0], lifting_status="succeeded", pred_id="p1"),
        _pred("vehicle.car", [0.0, 0.0, 0.0], lifting_status="failed", pred_id="p2"),
        _pred(
            "vehicle.car",
            [0.0, 0.0, 0.0],
            lifting_status="not_applicable",
            pred_id="p3",
        ),
        _pred("vehicle.car", [0.0, 0.0, 0.0], pred_id="p4"),  # legacy, no status
    ]

    result = evaluate_sample(sample=sample, predictions=preds, match_distance_m=2.0)

    assert result["prediction_count"] == 4
    assert result["lifting_failed_prediction_count"] == 1
    assert (
        result["evaluable_prediction_count"] == 3
    )  # succeeded + not_applicable + legacy
