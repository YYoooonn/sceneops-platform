from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import numpy as np

from sceneops_core.datasets.schemas import SampleSensorManifest

MIN_FRUSTUM_POINTS = 3  # fewer → skip lifting, keep placeholder
MIN_CLUSTER_POINTS = 5  # fewer → use all frustum points (no DBSCAN pruning)


def frustum_lift(
    *,
    bbox_2d: list[float],
    camera_sensor: SampleSensorManifest,
    lidar_sensor: SampleSensorManifest,
    raw_root: str,
    max_image_size: int = 800,
    dbscan_eps: float = 0.5,
    dbscan_min_samples: int = 3,
) -> dict[str, Any] | None:
    """Lift a 2D bbox to 3D using the LIDAR_TOP point cloud (frustum projection).

    Pipeline:
      1. Load LIDAR_TOP .pcd.bin
      2. LiDAR frame → ego frame   (lidar calibrated_sensor)
      3. Ego frame → camera frame  (camera calibrated_sensor, inverse)
      4. Project to image with K; keep points inside bbox_2d
      5. DBSCAN on ego-frame frustum points → largest cluster
      6. Fit axis-aligned bounding box; yaw from 2-D PCA
      7. Centroid: ego frame → global frame  (camera ego_pose)

    bbox_2d is in resized-image pixel coordinates (long edge ≤ max_image_size).
    K is for the original image, so bbox is back-scaled before projection.

    Returns None when fewer than MIN_FRUSTUM_POINTS points fall inside the frustum.
    """
    # ── 0. Load and parse inputs ──────────────────────────────────────────
    lidar_path = _resolve_path(raw_root, lidar_sensor.filename)
    pts_lidar = _load_lidar(lidar_path)  # (N, 3)

    K = np.array(camera_sensor.calibrated_sensor.camera_intrinsic, dtype=np.float64)
    orig_w = camera_sensor.width or int(K[0, 2] * 2)
    orig_h = camera_sensor.height or int(K[1, 2] * 2)

    # ── 1. Scale bbox: resized image coords → original image coords ───────
    # GroundingDINO runs on a resized image (long edge = max_image_size).
    # K is calibrated for the original resolution, so we invert the resize.
    scale = min(max_image_size / max(orig_w, orig_h), 1.0)
    x1, y1, x2, y2 = [c / scale for c in bbox_2d]

    # ── 2. LiDAR → ego frame ─────────────────────────────────────────────
    R_l2e = _quat_to_rot(lidar_sensor.calibrated_sensor.rotation)
    t_l2e = np.array(lidar_sensor.calibrated_sensor.translation, dtype=np.float64)
    pts_ego = (R_l2e @ pts_lidar.T).T + t_l2e  # (N, 3)

    # ── 3. Ego → camera frame ─────────────────────────────────────────────
    # calibrated_sensor gives us T_cam→ego, so T_ego→cam = T_cam→ego^{-1}
    R_c2e = _quat_to_rot(camera_sensor.calibrated_sensor.rotation)
    t_c2e = np.array(camera_sensor.calibrated_sensor.translation, dtype=np.float64)
    R_e2c = R_c2e.T  # rotation inverse
    pts_cam = (R_e2c @ (pts_ego - t_c2e).T).T  # (N, 3)

    # ── 4. Frustum filter ─────────────────────────────────────────────────
    in_front = pts_cam[:, 2] > 0.1  # z > 0: in front of camera
    pts_cam_fwd = pts_cam[in_front]
    pts_ego_fwd = pts_ego[in_front]

    if pts_cam_fwd.shape[0] == 0:
        return None

    # Ground plane removal: road surface sits at z≈-0.5 to 0m in ego frame
    # (ego origin = vehicle body; LiDAR at z=+1.84m). Car/pedestrian centers
    # are typically at z > 0. Threshold of 0.0 keeps object returns while
    # rejecting most asphalt points. Works best for objects within ~30m where
    # LiDAR density is sufficient to separate object from ground cluster.
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

    # Yaw: principal axis of x-y distribution via PCA.
    # TODO: replace with oriented bounding box (e.g. min-area rectangle) for
    # better heading accuracy, especially for elongated objects like buses.
    yaw = _pca_yaw(cluster_pts[:, :2])
    rotation = _yaw_to_quat(yaw)

    # ── 7. Ego → global frame ─────────────────────────────────────────────
    # Use CAM_FRONT ego_pose (same keyframe timestamp; vehicle frame is shared).
    R_e2g = _quat_to_rot(camera_sensor.ego_pose.rotation)
    t_e2g = np.array(camera_sensor.ego_pose.translation, dtype=np.float64)
    centroid_global = (R_e2g @ centroid_ego) + t_e2g

    return {
        "translation": centroid_global.tolist(),
        "size": size,
        "rotation": rotation,
        "lifting_method": "frustum_lidar",
        "cluster_point_count": int(cluster_pts.shape[0]),
        "frustum_point_count": int(pts_frustum.shape[0]),
    }


# ── Helpers ───────────────────────────────────────────────────────────────


def _load_lidar(path: Path) -> np.ndarray:
    """Load nuScenes .pcd.bin → (N, 3) float32 xyz."""
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
    principal = vecs[:, -1]  # largest eigenvector
    return float(np.arctan2(principal[1], principal[0]))


def _yaw_to_quat(yaw: float) -> list[float]:
    """Yaw angle (radians, around z-axis) → [w, x, y, z] quaternion."""
    h = yaw / 2.0
    return [float(np.cos(h)), 0.0, 0.0, float(np.sin(h))]


def _resolve_path(raw_root: str, filename: str) -> Path:
    parsed = urlparse(raw_root)
    base = Path(parsed.path) if parsed.scheme == "file" else Path(raw_root)
    if parsed.scheme not in ("file", ""):
        raise ValueError(
            f"frustum_lift supports local raw_root only. Got: {raw_root!r}\n"
            "TODO: add MinIO/S3 download for remote GPU deployments."
        )
    return base / filename
