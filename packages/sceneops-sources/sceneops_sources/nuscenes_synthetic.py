"""A small nuScenes v1.0 tree for tests of the reader and of canonical
builders, in the real table layout: two scenes of one log each, three sensor
channels (a camera, the top lidar, a radar), key frames and sweeps, with knobs
for the faults builders must refuse. Sensor files hold opaque bytes: nothing
decodes them.

Scene ``s`` (0-based) has ``samples`` samples. Sample ``k`` of scene ``s`` is at
``BASE_US + s * 10**8 + k * 500_000`` µs; it has one key frame per channel at
the sample's own time (the lidar exactly, the camera and radar a few µs off)
and, for every channel, ``sweeps`` sweeps after it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

VERSION = "v1.0-mini"
BASE_US = 1_532_402_927_000_000
CHANNELS = {
    "CAM_FRONT": ("camera", "jpg", ".jpg"),
    "LIDAR_TOP": ("lidar", "pcd", ".pcd.bin"),
    "RADAR_FRONT": ("radar", "pcd", ".pcd"),
}
# Key frame offset from the sample time, µs, per channel.
KEY_FRAME_OFFSET_US = {"CAM_FRONT": 12, "LIDAR_TOP": 0, "RADAR_FRONT": -7}
CAMERA_K = [[1266.4, 0.0, 816.3], [0.0, 1266.4, 491.5], [0.0, 0.0, 1.0]]
INCLUDE = ("LICENSE", "samples/**", "sweeps/**", "v1.0-mini/*")


def token(*parts: object) -> str:
    """A deterministic 32-hex token, as nuScenes tokens look."""
    return hashlib.md5("/".join(map(str, parts)).encode()).hexdigest()


def log_name(scene: int) -> str:
    return f"n015-2018-07-24-11-22-45+08{scene:02d}"


def write_nuscenes_tree(
    root: Path,
    *,
    scenes: int = 2,
    samples: int = 3,
    sweeps: int = 1,
    version: str = VERSION,
    mutate_tables: Callable[[dict[str, list[dict[str, Any]]]], None] | None = None,
    omit_scene_files: tuple[int, ...] = (),
) -> Path:
    """Write the tree under ``root``. ``mutate_tables`` edits the tables before
    they are written; the files of the scenes in ``omit_scene_files`` are not
    written (a slice that preserves other scenes only)."""
    tables: dict[str, list[dict[str, Any]]] = {
        name: []
        for name in (
            "scene",
            "sample",
            "sample_data",
            "ego_pose",
            "calibrated_sensor",
            "sensor",
            "log",
        )
    }
    for channel, (modality, _, _) in CHANNELS.items():
        tables["sensor"].append(
            {
                "token": token("sensor", channel),
                "channel": channel,
                "modality": modality,
            }
        )
    files: dict[str, bytes] = {"LICENSE": b"synthetic nuScenes terms\n"}
    for s in range(scenes):
        log = token("log", s)
        tables["log"].append(
            {
                "token": log,
                "logfile": log_name(s),
                "vehicle": "n015",
                "date_captured": "2018-07-24",
                "location": "singapore-onenorth",
            }
        )
        calibrated = {}
        for i, channel in enumerate(CHANNELS):
            cs = token("calibrated_sensor", s, channel)
            calibrated[channel] = cs
            tables["calibrated_sensor"].append(
                {
                    "token": cs,
                    "sensor_token": token("sensor", channel),
                    "translation": [1.5 + i, 0.25 * i, 1.75],
                    "rotation": [1.0, 0.0, 0.0, 0.0],
                    "camera_intrinsic": CAMERA_K if channel == "CAM_FRONT" else [],
                }
            )
        scene_token = token("scene", s)
        sample_tokens = [token("sample", s, k) for k in range(samples)]
        tables["scene"].append(
            {
                "token": scene_token,
                "log_token": log,
                "nbr_samples": samples,
                "first_sample_token": sample_tokens[0],
                "last_sample_token": sample_tokens[-1],
                "name": f"scene-{s + 1:04d}",
                "description": "synthetic",
            }
        )
        for k, sample_token in enumerate(sample_tokens):
            sample_us = BASE_US + s * 10**8 + k * 500_000
            tables["sample"].append(
                {
                    "token": sample_token,
                    "timestamp": sample_us,
                    "prev": sample_tokens[k - 1] if k else "",
                    "next": sample_tokens[k + 1] if k + 1 < samples else "",
                    "scene_token": scene_token,
                }
            )
            for channel, (modality, fileformat, suffix) in CHANNELS.items():
                for sweep in range(sweeps + 1):
                    key_frame = sweep == 0
                    us = sample_us + KEY_FRAME_OFFSET_US[channel] + sweep * 100_000
                    folder = "samples" if key_frame else "sweeps"
                    filename = (
                        f"{folder}/{channel}/{log_name(s)}__{channel}__{us}{suffix}"
                    )
                    reading = token("sample_data", s, k, channel, sweep)
                    tables["sample_data"].append(
                        {
                            "token": reading,
                            "sample_token": sample_token,
                            "ego_pose_token": reading,
                            "calibrated_sensor_token": calibrated[channel],
                            "timestamp": us,
                            "fileformat": fileformat,
                            "is_key_frame": key_frame,
                            "height": 900 if modality == "camera" else 0,
                            "width": 1600 if modality == "camera" else 0,
                            "filename": filename,
                            "prev": "",
                            "next": "",
                        }
                    )
                    tables["ego_pose"].append(
                        {
                            "token": reading,
                            "timestamp": us,
                            "rotation": [0.6, 0.0, 0.0, -0.8],
                            "translation": [400.0 + k, 1100.0 + sweep, 0.0],
                        }
                    )
                    if s not in omit_scene_files:
                        files[filename] = f"{channel} {us}\n".encode()
    if mutate_tables is not None:
        mutate_tables(tables)
    for name, rows in tables.items():
        files[f"{version}/{name}.json"] = json.dumps(rows, indent=1).encode()
    for path, data in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return root


__all__ = [
    "BASE_US",
    "CAMERA_K",
    "CHANNELS",
    "INCLUDE",
    "KEY_FRAME_OFFSET_US",
    "VERSION",
    "log_name",
    "token",
    "write_nuscenes_tree",
]
