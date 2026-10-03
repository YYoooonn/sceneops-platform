from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from sceneops_worker.inference.detection.uris import local_path_from_uri
from sceneops_worker.scenes.keyframes import KeyframeObservation

MIN_FRUSTUM_POINTS = 3  # fewer → skip lifting, keep placeholder
MIN_CLUSTER_POINTS = 5  # fewer → use all frustum points (no DBSCAN pruning)

# Lidar payload formats this lifter can decode, keyed by the payload's
# declared media type. A lidar observation in any other format is not
# lifted (the caller records it as a failed lift) rather than guessed at.
NUSCENES_LIDAR_PCD_BIN = "application/x.nuscenes.lidar-pcd-bin"


def frustum_lift(
    *,
    bbox_2d: list[float],
    camera: KeyframeObservation,
    lidar: KeyframeObservation,
    lidar_uri: str,
    max_image_size: int = 800,
    dbscan_eps: float = 0.5,
    dbscan_min_samples: int = 3,
) -> dict[str, Any] | None:
    """Lift a 2D bbox to 3D using the keyframe's lidar point cloud (frustum
    projection).

    Uses only what the canonical Scene states for the two observations: each
    one's calibration extrinsic (sensor frame in ego frame), the camera
    intrinsic and image size, and the ego pose the source associates with
    the camera observation.

    Pipeline:
      1. Load the lidar payload from ``lidar_uri`` (dispatched on its
         declared media type)
      2. LiDAR frame → ego frame   (lidar extrinsic)
      3. Ego frame → camera frame  (camera extrinsic, inverse)
      4. Project to image with K; keep points inside bbox_2d
      5. DBSCAN on ego-frame frustum points → largest cluster
      6. Fit axis-aligned bounding box; yaw from 2-D PCA
      7. Centroid: ego frame → world frame  (camera ego pose)

    bbox_2d is in resized-image pixel coordinates (long edge ≤ max_image_size).
    K is for the original image, so bbox is back-scaled before projection.

    Returns None when the observations lack the geometry lifting needs or
    fewer than MIN_FRUSTUM_POINTS points fall inside the frustum.
    """
    camera_cal = camera.calibration
    lidar_cal = lidar.calibration
    camera_ego = camera.ego_pose
    image_size = camera.observation.image_size

    if camera_cal is None or lidar_cal is None or camera_ego is None:
        return None
    if camera_cal.camera_intrinsic is None or image_size is None:
        return None

    pts_lidar = _load_lidar(lidar, lidar_uri)  # (N, 3)

    K = np.array(camera_cal.camera_intrinsic, dtype=np.float64)
    orig_w = image_size.width_px
    orig_h = image_size.height_px

    # ── 1. Scale bbox: resized image coords → original image coords ───────
    scale = min(max_image_size / max(orig_w, orig_h), 1.0)
    x1, y1, x2, y2 = [c / scale for c in bbox_2d]

    # ── 2. LiDAR → ego frame ─────────────────────────────────────────────
    R_l2e = _quat_to_rot(lidar_cal.extrinsic.rotation_wxyz)
    t_l2e = np.array(lidar_cal.extrinsic.translation_m, dtype=np.float64)
    pts_ego = (R_l2e @ pts_lidar.T).T + t_l2e  # (N, 3)

    # ── 3. Ego → camera frame ─────────────────────────────────────────────
    R_c2e = _quat_to_rot(camera_cal.extrinsic.rotation_wxyz)
    t_c2e = np.array(camera_cal.extrinsic.translation_m, dtype=np.float64)
    R_e2c = R_c2e.T
    pts_cam = (R_e2c @ (pts_ego - t_c2e).T).T  # (N, 3)

    # ── 4. Frustum filter ─────────────────────────────────────────────────
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

    uvz = (K @ pts_cam_fwd.T).T  # (M, 3)
    u = uvz[:, 0] / uvz[:, 2]
    v = uvz[:, 1] / uvz[:, 2]

    in_box = (u >= x1) & (u <= x2) & (v >= y1) & (v <= y2)
    pts_frustum = pts_ego_fwd[in_box]  # (P, 3) ego frame

    if pts_frustum.shape[0] < MIN_FRUSTUM_POINTS:
        return None

    # ── 5. DBSCAN: remove background / ground noise ───────────────────────
    cluster_pts = _largest_cluster(
        pts_frustum, eps=dbscan_eps, min_samples=dbscan_min_samples
    )

    # ── 6. Fit axis-aligned bounding box in ego frame ─────────────────────
    centroid_ego = cluster_pts.mean(axis=0)
    size = (cluster_pts.max(axis=0) - cluster_pts.min(axis=0)).tolist()

    yaw = _pca_yaw(cluster_pts[:, :2])
    rotation = _yaw_to_quat(yaw)

    # ── 7. Ego → world frame ──────────────────────────────────────────────
    R_e2g = _quat_to_rot(camera_ego.transform.rotation_wxyz)
    t_e2g = np.array(camera_ego.transform.translation_m, dtype=np.float64)
    centroid_global = (R_e2g @ centroid_ego) + t_e2g

    return {
        "translation": centroid_global.tolist(),
        "size": size,
        "rotation": rotation,
        "lifting_method": "frustum_lidar",
        "cluster_point_count": int(cluster_pts.shape[0]),
        "frustum_point_count": int(pts_frustum.shape[0]),
    }


# ── Helpers ───────────────────────────────────────────────────────────────────


def _load_lidar(lidar: KeyframeObservation, uri: str) -> np.ndarray:
    """Decode a lidar payload into (N, 3) float64 xyz by its declared format.
    ``uri`` is where its payload artifact lives."""
    payload = lidar.observation.payload
    if payload.media_type != NUSCENES_LIDAR_PCD_BIN:
        raise ValueError(
            f"frustum_lift cannot decode lidar payload media_type "
            f"{payload.media_type!r}"
        )
    # float32 x, y, z, intensity, ring index.
    path = Path(local_path_from_uri(uri))
    pts = np.fromfile(path, dtype=np.float32).reshape(-1, 5)
    return pts[:, :3].astype(np.float64)


def _quat_to_rot(q: list[float]) -> np.ndarray:
    """[w, x, y, z] quaternion → 3×3 rotation matrix (float64)."""
    w, x, y, z = (float(v) for v in q)
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
            [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
            [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
        ],
        dtype=np.float64,
    )


def _largest_cluster(pts: np.ndarray, eps: float, min_samples: int) -> np.ndarray:
    """DBSCAN → largest non-noise cluster. Falls back to all pts if none found."""
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
    """Yaw angle (radians, around z-axis) → [w, x, y, z] quaternion."""
    h = yaw / 2.0
    return [float(np.cos(h)), 0.0, 0.0, float(np.sin(h))]
