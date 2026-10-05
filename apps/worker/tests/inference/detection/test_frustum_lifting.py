"""Frustum lifting and GroundingDINO prediction assembly over pinned,
resolved samples (ADR-007 §33.5). The geometry uses only what the Scene and
its sample view state, and the frame of every lifted box is explicit."""

from __future__ import annotations

import numpy as np
import pytest

from sceneops_core.sample_views import (
    MemberPolicy,
    PosePolicy,
    SampleAnchorPolicy,
    SampleViewPolicy,
    SceneRevisionRef,
    SceneSampleResolver,
    build_scene_sample_view,
)
from sceneops_core.scenes.testing import build_scene_manifest
from sceneops_worker.inference.detection.base import DetectionSampleInput
from sceneops_worker.inference.detection.frustum_lifting import frustum_lift
from sceneops_worker.inference.detection.grounding_dino import _build_predictions

MS = 1_000_000
K = np.array([[1266.4, 0.0, 816.3], [0.0, 1266.4, 491.5], [0.0, 0.0, 1.0]])
CAMERA_TRANSLATION = np.array([1.7, 0.0, 1.5])
LIDAR_TRANSLATION = np.array([0.9, 0.0, 1.8])
# Camera optical frame -> ego: z forward, x right, y down.
R_CAM_TO_EGO = np.array([[0.0, 0.0, 1.0], [-1.0, 0.0, 0.0], [0.0, -1.0, 0.0]])


def _resolved_sample():
    manifest = build_scene_manifest(annotations_per_keyframe=0)
    ref = SceneRevisionRef(
        scene_id="scene-1",
        manifest_artifact_id="scene-manifest-1",
        manifest_checksum=manifest.checksum(),
    )
    view = build_scene_sample_view(
        scene=ref,
        manifest=manifest,
        policy=SampleViewPolicy(
            anchor=SampleAnchorPolicy(channel="CAM_FRONT"),
            members=[MemberPolicy(channel="LIDAR_TOP", tolerance_ns=10 * MS)],
            pose=PosePolicy(
                parent_frame_id="world", child_frame_id="ego", tolerance_ns=MS
            ),
        ),
    )
    return SceneSampleResolver(view, manifest).resolve(view.samples[0])


def _scene_points(center_ego, *, n=60, spread=0.2, seed=0):
    rng = np.random.default_rng(seed)
    cluster = np.asarray(center_ego) + rng.normal(0.0, spread, size=(n, 3))
    far = np.array([[40.0, -20.0, 1.0], [25.0, 15.0, 2.0]])
    return (
        np.vstack([cluster, far]) - LIDAR_TRANSLATION
    )  # lidar frame (identity rotation)


def _bbox_for(center_ego, *, half_px=60.0, scale=0.5):
    cam = R_CAM_TO_EGO.T @ (np.asarray(center_ego) - CAMERA_TRANSLATION)
    uvw = K @ cam
    u, v = uvw[0] / uvw[2], uvw[1] / uvw[2]
    # bbox_2d is in resized-image pixels (long edge 800 for a 1600 px image).
    return [
        (u - half_px) * scale,
        (v - half_px) * scale,
        (u + half_px) * scale,
        (v + half_px) * scale,
    ]


def test_lifts_to_the_world_frame_through_the_associated_pose():
    sample = _resolved_sample()
    center_ego = np.array([10.0, 1.0, 1.0])
    lifted = frustum_lift(
        bbox_2d=_bbox_for(center_ego),
        camera=sample.anchor,
        lidar=sample.member("LIDAR_TOP"),
        lidar_xyz=_scene_points(center_ego),
        pose=sample.pose,
    )
    assert lifted is not None
    assert lifted["frame_id"] == "world"
    # Pose: ego translated by (timestamp_s, 0, 0) in the world.
    expected = center_ego + np.array([sample.pose.transform.translation_m[0], 0.0, 0.0])
    np.testing.assert_allclose(lifted["translation"], expected, atol=0.4)
    assert lifted["lifting_method"] == "frustum_lidar"
    assert lifted["cluster_point_count"] >= 40
    assert len(lifted["rotation"]) == 4


def test_without_a_pose_the_box_stays_in_the_calibration_ego_frame():
    sample = _resolved_sample()
    center_ego = np.array([10.0, 1.0, 1.0])
    lifted = frustum_lift(
        bbox_2d=_bbox_for(center_ego),
        camera=sample.anchor,
        lidar=sample.member("LIDAR_TOP"),
        lidar_xyz=_scene_points(center_ego),
        pose=None,
    )
    assert lifted["frame_id"] == "ego"
    np.testing.assert_allclose(lifted["translation"], center_ego, atol=0.4)


def test_a_rotated_pose_rotates_the_box_orientation_with_it():
    sample = _resolved_sample()
    center_ego = np.array([10.0, 1.0, 1.0])
    quarter_turn = (np.cos(np.pi / 4), 0.0, 0.0, np.sin(np.pi / 4))
    posed = sample.pose.model_copy(
        update={
            "transform": sample.pose.transform.model_copy(
                update={"rotation_wxyz": quarter_turn}
            )
        }
    )
    lifted = frustum_lift(
        bbox_2d=_bbox_for(center_ego),
        camera=sample.anchor,
        lidar=sample.member("LIDAR_TOP"),
        lidar_xyz=_scene_points(center_ego),
        pose=posed,
    )
    # The translation is rotated about z by 90 degrees (x -> y).
    assert lifted["translation"][1] == pytest.approx(
        center_ego[0] + posed.transform.translation_m[1], abs=0.5
    )
    w, x, y, z = lifted["rotation"]
    assert abs(w * w + x * x + y * y + z * z - 1.0) < 1e-6


def test_a_box_with_no_points_in_the_frustum_is_not_lifted():
    sample = _resolved_sample()
    lifted = frustum_lift(
        bbox_2d=[0.0, 0.0, 5.0, 5.0],
        camera=sample.anchor,
        lidar=sample.member("LIDAR_TOP"),
        lidar_xyz=_scene_points(np.array([10.0, 1.0, 1.0])),
        pose=sample.pose,
    )
    assert lifted is None


def test_calibrations_must_share_an_ego_frame():
    sample = _resolved_sample()
    other = sample.member("LIDAR_TOP")
    other = other.__class__(
        channel=other.channel,
        modality=other.modality,
        observation=other.observation,
        calibration=other.calibration.model_copy(
            update={
                "extrinsic": other.calibration.extrinsic.model_copy(
                    update={"parent_frame_id": "world"}
                )
            }
        ),
        time_delta_ns=other.time_delta_ns,
    )
    assert (
        frustum_lift(
            bbox_2d=_bbox_for(np.array([10.0, 1.0, 1.0])),
            camera=sample.anchor,
            lidar=other,
            lidar_xyz=_scene_points(np.array([10.0, 1.0, 1.0])),
            pose=None,
        )
        is None
    )


def _input(sample, *, with_lidar=True) -> DetectionSampleInput:
    return DetectionSampleInput(
        scene_id="scene-1",
        sample_id="smp-000000",
        camera_channel="CAM_FRONT",
        image_uri="file:///img.jpg",
        camera=sample.anchor,
        lidar=sample.member("LIDAR_TOP") if with_lidar else None,
        lidar_uri="file:///lidar" if with_lidar else None,
        pose=sample.pose,
    )


def _detection(bbox, category="vehicle.car"):
    return {"category_name": category, "score": 0.9, "bbox_2d": bbox}


def test_predictions_carry_the_frame_and_lifting_status():
    sample = _resolved_sample()
    center = np.array([10.0, 1.0, 1.0])
    preds = _build_predictions(
        sample=_input(sample),
        detections_2d=[_detection(_bbox_for(center)), _detection([0, 0, 5, 5])],
        lidar_xyz=_scene_points(center),
        max_image_size=800,
    )
    lifted, unlifted = preds
    assert (lifted["lifting_status"], lifted["frame_id"]) == ("succeeded", "world")
    # No points in the frustum: the 2-D detection stays, unlocalized and not evaluable.
    assert (unlifted["lifting_status"], unlifted["frame_id"]) == (
        "not_applicable",
        None,
    )
    assert lifted["prediction_id"] == "smp-000000-gdino-0000"


def test_undecodable_lidar_marks_predictions_failed_not_dropped():
    sample = _resolved_sample()
    preds = _build_predictions(
        sample=_input(sample),
        detections_2d=[_detection([1, 2, 3, 4])],
        lidar_xyz=None,
        lidar_error="no lidar decoder for payload media_type 'x/y'",
        max_image_size=800,
    )
    assert preds[0]["lifting_status"] == "failed"
    assert "no lidar decoder" in preds[0]["lifting_error"]


def test_without_a_lidar_observation_lifting_is_not_applicable():
    sample = _resolved_sample()
    preds = _build_predictions(
        sample=_input(sample, with_lidar=False),
        detections_2d=[_detection([1, 2, 3, 4])],
        lidar_xyz=None,
        max_image_size=800,
    )
    assert preds[0]["lifting_status"] == "not_applicable"
    assert preds[0]["frame_id"] is None
