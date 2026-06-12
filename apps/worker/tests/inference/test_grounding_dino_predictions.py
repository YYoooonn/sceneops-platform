"""Tests for frustum lifting status tracking in GroundingDINO prediction building."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch


from sceneops_worker.inference.detection.grounding_dino import _build_predictions


def _make_sample(sample_id: str = "sample-001") -> MagicMock:
    sample = MagicMock()
    sample.sample_id = sample_id
    return sample


def _make_lidar_sensor(filename: str = "lidar.pcd.bin") -> MagicMock:
    sensor = MagicMock()
    sensor.filename = filename
    return sensor


def _det(category: str = "vehicle.car", score: float = 0.9) -> dict:
    return {
        "category_name": category,
        "score": score,
        "bbox_2d": [10.0, 20.0, 50.0, 80.0],
    }


def _lift_result() -> dict:
    return {
        "translation": [1.0, 2.0, 0.5],
        "size": [4.5, 2.0, 1.8],
        "rotation": [1.0, 0.0, 0.0, 0.0],
        "lifting_method": "frustum_lidar",
        "cluster_point_count": 42,
    }


# ── lifting succeeded ──────────────────────────────────────────────────────────


def test_build_predictions_lifting_succeeded():
    sample = _make_sample()
    lidar = _make_lidar_sensor()

    with patch(
        "sceneops_worker.inference.detection.grounding_dino.frustum_lift",
        return_value=_lift_result(),
    ):
        preds = _build_predictions(
            sample=sample,
            detections_2d=[_det()],
            camera_sensor=MagicMock(),
            lidar_sensor=lidar,
            calibrated_sensor_index={},
            ego_pose_index={},
            raw_root="/data",
            max_image_size=800,
        )

    assert len(preds) == 1
    pred = preds[0]
    assert pred["lifting_status"] == "succeeded"
    assert pred["lifting_error"] is None
    assert pred["translation"] == [1.0, 2.0, 0.5]
    assert pred["lifting_method"] == "frustum_lidar"


# ── lifting failed (exception) ────────────────────────────────────────────────


def test_build_predictions_lifting_failed(caplog):
    sample = _make_sample()
    lidar = _make_lidar_sensor()

    with patch(
        "sceneops_worker.inference.detection.grounding_dino.frustum_lift",
        side_effect=ValueError("bad calibration"),
    ):
        with caplog.at_level(logging.WARNING):
            preds = _build_predictions(
                sample=sample,
                detections_2d=[_det()],
                camera_sensor=MagicMock(),
                lidar_sensor=lidar,
                calibrated_sensor_index={},
                ego_pose_index={},
                raw_root="/data",
                max_image_size=800,
            )

    assert len(preds) == 1
    pred = preds[0]
    assert pred["lifting_status"] == "failed"
    assert "bad calibration" in (pred["lifting_error"] or "")
    assert pred["translation"] == [0.0, 0.0, 0.0]

    assert any("frustum_lift failed" in r.message for r in caplog.records)


# ── no LiDAR sensor ───────────────────────────────────────────────────────────


def test_build_predictions_no_lidar():
    preds = _build_predictions(
        sample=_make_sample(),
        detections_2d=[_det()],
        camera_sensor=MagicMock(),
        lidar_sensor=None,
        calibrated_sensor_index={},
        ego_pose_index={},
        raw_root="/data",
        max_image_size=800,
    )

    assert len(preds) == 1
    assert preds[0]["lifting_status"] == "not_applicable"
    assert preds[0]["lifting_error"] is None


# ── frustum_lift returns None (not enough LiDAR points) ──────────────────────


def test_build_predictions_frustum_returns_none():
    sample = _make_sample()
    lidar = _make_lidar_sensor()

    with patch(
        "sceneops_worker.inference.detection.grounding_dino.frustum_lift",
        return_value=None,
    ):
        preds = _build_predictions(
            sample=sample,
            detections_2d=[_det()],
            camera_sensor=MagicMock(),
            lidar_sensor=lidar,
            calibrated_sensor_index={},
            ego_pose_index={},
            raw_root="/data",
            max_image_size=800,
        )

    assert preds[0]["lifting_status"] == "not_applicable"
    assert preds[0]["translation"] == [0.0, 0.0, 0.0]


# ── mixed detections ──────────────────────────────────────────────────────────


def test_build_predictions_mixed_status():
    """Second detection fails lifting; first succeeds."""
    sample = _make_sample()
    lidar = _make_lidar_sensor()
    call_count = [0]

    def side_effect(*_, **__):
        call_count[0] += 1
        if call_count[0] == 1:
            return _lift_result()
        raise RuntimeError("OOM")

    with patch(
        "sceneops_worker.inference.detection.grounding_dino.frustum_lift",
        side_effect=side_effect,
    ):
        preds = _build_predictions(
            sample=sample,
            detections_2d=[_det(), _det("human.pedestrian.adult")],
            camera_sensor=MagicMock(),
            lidar_sensor=lidar,
            calibrated_sensor_index={},
            ego_pose_index={},
            raw_root="/data",
            max_image_size=800,
        )

    assert preds[0]["lifting_status"] == "succeeded"
    assert preds[1]["lifting_status"] == "failed"
    statuses = [p["lifting_status"] for p in preds]
    assert statuses.count("succeeded") == 1
    assert statuses.count("failed") == 1
