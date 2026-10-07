"""Frustum lifting: a 2-D detection + a lidar point cloud -> a 3-D box.

Uses only what the pinned Scene and sample view state: each channel's
calibration extrinsic, the camera intrinsic and image size, and the ego pose
the view associated with the sample. Frames are explicit: the box is
expressed in the pose's parent frame when the sample has a pose, otherwise
in the calibration's parent (ego) frame, and the result says which. Nothing
assumes a frame name.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from sceneops_core.sample_views import ResolvedMember
from sceneops_core.scenes.schemas.manifests import ScenePose

MIN_FRUSTUM_POINTS = 3  # fewer -> skip lifting, keep placeholder


def frustum_lift(
    *,
    bbox_2d: list[float],
    camera: ResolvedMember,
    lidar: ResolvedMember,
    lidar_xyz: np.ndarray,
    pose: ScenePose | None,
    max_image_size: int = 800,
    dbscan_eps: float = 0.5,
    dbscan_min_samples: int = 3,
) -> dict[str, Any] | None:
    """Lift a 2-D bbox to a 3-D box.

    Pipeline:
      1. lidar frame -> ego frame      (lidar extrinsic)
      2. ego frame -> camera frame     (camera extrinsic, inverse)
      3. project with K; keep points inside bbox_2d
      4. DBSCAN on ego-frame frustum points -> largest cluster
      5. axis-aligned extents in the ego frame; yaw from 2-D PCA
      6. optionally ego frame -> the pose's parent frame

    ``bbox_2d`` is in resized-image pixel coordinates (long edge <=
    ``max_image_size``); K is for the original image, so the box is
    back-scaled before projection.

    Returns None when the observations lack the geometry lifting needs, the
    calibrations do not share an ego frame, or fewer than MIN_FRUSTUM_POINTS
    points fall inside the frustum.
    """
    camera_cal = camera.calibration
    lidar_cal = lidar.calibration
    image_size = camera.observation.image_size
    if camera_cal is None or lidar_cal is None:
        return None
    if camera_cal.camera_intrinsic is None or image_size is None:
        return None

    ego_frame = lidar_cal.extrinsic.parent_frame_id
    if camera_cal.extrinsic.parent_frame_id != ego_frame:
        return None
    if pose is not None and pose.transform.child_frame_id != ego_frame:
        return None

    pts_lidar = np.asarray(lidar_xyz, dtype=np.float64)
    K = np.array(camera_cal.camera_intrinsic, dtype=np.float64)
    orig_w = image_size.width_px
    orig_h = image_size.height_px

    scale = min(max_image_size / max(orig_w, orig_h), 1.0)
    x1, y1, x2, y2 = (c / scale for c in bbox_2d)

    R_l2e = _quat_to_rot(lidar_cal.extrinsic.rotation_wxyz)
    t_l2e = np.array(lidar_cal.extrinsic.translation_m, dtype=np.float64)
    pts_ego = (R_l2e @ pts_lidar.T).T + t_l2e

    R_c2e = _quat_to_rot(camera_cal.extrinsic.rotation_wxyz)
    t_c2e = np.array(camera_cal.extrinsic.translation_m, dtype=np.float64)
    pts_cam = (R_c2e.T @ (pts_ego - t_c2e).T).T

    in_front = pts_cam[:, 2] > 0.1
    pts_cam_fwd = pts_cam[in_front]
    pts_ego_fwd = pts_ego[in_front]
    if pts_cam_fwd.shape[0] == 0:
        return None

    above_ground = pts_ego_fwd[:, 2] > 0.0
    pts_cam_fwd = pts_cam_fwd[above_ground]
    pts_ego_fwd = pts_ego_fwd[above_ground]
    if pts_cam_fwd.shape[0] == 0:
        return None

    uvz = (K @ pts_cam_fwd.T).T
    u = uvz[:, 0] / uvz[:, 2]
    v = uvz[:, 1] / uvz[:, 2]
    in_box = (u >= x1) & (u <= x2) & (v >= y1) & (v <= y2)
    pts_frustum = pts_ego_fwd[in_box]
    if pts_frustum.shape[0] < MIN_FRUSTUM_POINTS:
        return None

    cluster = _largest_cluster(
        pts_frustum, eps=dbscan_eps, min_samples=dbscan_min_samples
    )
    centroid_ego = cluster.mean(axis=0)
    size = (cluster.max(axis=0) - cluster.min(axis=0)).tolist()
    yaw_rotation = _yaw_to_quat(_pca_yaw(cluster[:, :2]))

    if pose is None:
        frame_id = ego_frame
        translation = centroid_ego
        rotation = yaw_rotation
    else:
        frame_id = pose.transform.parent_frame_id
        R_e2p = _quat_to_rot(pose.transform.rotation_wxyz)
        t_e2p = np.array(pose.transform.translation_m, dtype=np.float64)
        translation = (R_e2p @ centroid_ego) + t_e2p
        rotation = _quat_mul(list(pose.transform.rotation_wxyz), yaw_rotation)

    return {
        "frame_id": frame_id,
        "translation": translation.tolist(),
        "size": size,
        "rotation": rotation,
        "lifting_method": "frustum_lidar",
        "cluster_point_count": int(cluster.shape[0]),
        "frustum_point_count": int(pts_frustum.shape[0]),
    }


# ── Helpers ───────────────────────────────────────────────────────────────────


def _quat_to_rot(q) -> np.ndarray:
    """[w, x, y, z] quaternion -> 3x3 rotation matrix (float64)."""
    w, x, y, z = (float(v) for v in q)
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
            [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
            [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
        ],
        dtype=np.float64,
    )


def _quat_mul(a: list[float], b: list[float]) -> list[float]:
    """Hamilton product of two [w, x, y, z] quaternions (``a`` after ``b``)."""
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return [
        aw * bw - ax * bx - ay * by - az * bz,
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
    ]


def _largest_cluster(pts: np.ndarray, eps: float, min_samples: int) -> np.ndarray:
    """DBSCAN -> largest non-noise cluster. Falls back to all pts if none found."""
    from sklearn.cluster import DBSCAN

    if pts.shape[0] < min_samples:
        return pts

    labels = DBSCAN(eps=eps, min_samples=min_samples).fit_predict(pts)
    valid = labels[labels >= 0]
    if valid.size == 0:
        return pts

    best = int(np.bincount(valid).argmax())
    return pts[labels == best]


def _pca_yaw(pts_xy: np.ndarray) -> float:
    """Dominant heading angle from 2-D PCA (radians, around z-axis)."""
    if pts_xy.shape[0] < 2:
        return 0.0
    centered = pts_xy - pts_xy.mean(axis=0)
    _, vecs = np.linalg.eigh(centered.T @ centered)
    principal = vecs[:, -1]
    return float(np.arctan2(principal[1], principal[0]))


def _yaw_to_quat(yaw: float) -> list[float]:
    """Yaw angle (radians, around z-axis) -> [w, x, y, z] quaternion."""
    h = yaw / 2.0
    return [float(np.cos(h)), 0.0, 0.0, float(np.sin(h))]
