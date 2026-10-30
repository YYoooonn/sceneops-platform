"""What a nuScenes v1.0-mini scene contains, read from the raw tables and files.

Verification-only: it derives the facts a recording prepared from the scene
must carry, by the source preparation's documented mapping
(integrations/dataset-acquisition/README.md "nuScenes mapping" and "Timing"),
and the facts its reference label artifact must carry
(docs/development/reference-corpus.md "Reference labels"). It is not a source
reader for canonicalization and shares no code with the acquisition tool.

    source unit          a scene name; its samples; every sample_data whose
                         sample belongs to the scene (key frames and sweeps)
    camera / lidar       one image / point cloud per sample_data of the channel,
                         header stamp = timestamp_us * 1000, payload = file bytes
    camera_info          one per image, same stamp; K = camera_intrinsic
    /tf                  one map -> base_link per sample_data of the unit (every
                         sensor, radar included) = its ego_pose
    /tf_static           base_link -> <sensor frame> = calibrated_sensor of every
                         converted (camera, lidar) sensor
    CAN                  /vehicle/odom = pose, /vehicle/imu = ms_imu,
                         /vehicle/status and /vehicle/control = vehicle_monitor,
                         one message per extract entry at utime * 1000
    mission              running at the unit's first source time, completed at its
                         last; mission_id = mission-<unit>
    labels               one sample per scene sample in sample order, anchored on
                         its LIDAR_TOP key frame; one label per sample_annotation
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any

NS_PER_US = 1000


def camera_topic(channel: str) -> str:
    return f"/camera/{channel.removeprefix('CAM_').lower()}/image/compressed"


def camera_info_topic(channel: str) -> str:
    return f"/camera/{channel.removeprefix('CAM_').lower()}/camera_info"


LIDAR_TOPIC = {"LIDAR_TOP": "/lidar/top/points"}
CAN_TOPICS = {
    "pose": ["/vehicle/odom"],
    "ms_imu": ["/vehicle/imu"],
    "vehicle_monitor": ["/vehicle/status", "/vehicle/control"],
}


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            h.update(block)
    return "sha256:" + h.hexdigest()


@dataclass(frozen=True)
class SensorReading:
    channel: str
    modality: str
    timestamp_ns: int
    path: Path
    width: int
    height: int
    ego_pose: dict[str, Any]
    calibrated_sensor: dict[str, Any]


@dataclass
class NuScenesTables:
    root: Path
    version: str = "v1.0-mini"
    _cache: dict[str, Any] = field(default_factory=dict)

    def table(self, name: str) -> list[dict[str, Any]]:
        if name not in self._cache:
            self._cache[name] = json.loads(
                (self.root / self.version / f"{name}.json").read_text()
            )
        return self._cache[name]

    def index(self, name: str) -> dict[str, dict[str, Any]]:
        key = f"{name}#index"
        if key not in self._cache:
            self._cache[key] = {row["token"]: row for row in self.table(name)}
        return self._cache[key]


class NuScenesScene:
    def __init__(self, tables: NuScenesTables, scene_name: str) -> None:
        self.tables = tables
        self.name = scene_name
        scenes = [s for s in tables.table("scene") if s["name"] == scene_name]
        if len(scenes) != 1:
            raise ValueError(f"nuScenes has {len(scenes)} scenes named {scene_name!r}")
        self.scene = scenes[0]

    @cached_property
    def samples(self) -> list[dict[str, Any]]:
        """The scene's samples in sample order (first_sample_token -> next)."""
        index = self.tables.index("sample")
        out, token = [], self.scene["first_sample_token"]
        while token:
            sample = index[token]
            if sample["scene_token"] != self.scene["token"]:
                raise ValueError(f"sample {token} leaves scene {self.name}")
            out.append(sample)
            token = sample["next"]
        if len(out) != self.scene["nbr_samples"]:
            raise ValueError(
                f"{self.name}: {len(out)} samples, the scene says {self.scene['nbr_samples']}"
            )
        return out

    @cached_property
    def readings(self) -> list[SensorReading]:
        sample_tokens = {s["token"] for s in self.samples}
        calibrated = self.tables.index("calibrated_sensor")
        sensors = self.tables.index("sensor")
        poses = self.tables.index("ego_pose")
        out = []
        for row in self.tables.table("sample_data"):
            if row["sample_token"] not in sample_tokens:
                continue
            cs = calibrated[row["calibrated_sensor_token"]]
            sensor = sensors[cs["sensor_token"]]
            out.append(
                SensorReading(
                    channel=sensor["channel"],
                    modality=sensor["modality"],
                    timestamp_ns=row["timestamp"] * NS_PER_US,
                    path=self.tables.root / row["filename"],
                    width=row["width"],
                    height=row["height"],
                    ego_pose=poses[row["ego_pose_token"]],
                    calibrated_sensor=cs,
                )
            )
        return out

    def channel_readings(self, channel: str) -> list[SensorReading]:
        return sorted(
            (r for r in self.readings if r.channel == channel),
            key=lambda r: (r.timestamp_ns, str(r.path)),
        )

    def channels(self, modality: str) -> list[str]:
        return sorted({r.channel for r in self.readings if r.modality == modality})

    def can(self, message: str) -> list[dict[str, Any]]:
        path = self.tables.root / "can_bus" / f"{self.name}_{message}.json"
        return json.loads(path.read_text())

    @cached_property
    def annotations_by_sample(self) -> dict[str, list[dict[str, Any]]]:
        tokens = {s["token"] for s in self.samples}
        out: dict[str, list[dict[str, Any]]] = {t: [] for t in tokens}
        for row in self.tables.table("sample_annotation"):
            if row["sample_token"] in tokens:
                out[row["sample_token"]].append(row)
        return out

    def keyframe(self, sample_token: str, channel: str) -> SensorReading:
        index = self.tables.index("sample_data")
        calibrated = self.tables.index("calibrated_sensor")
        sensors = self.tables.index("sensor")
        found = [
            row
            for row in self.tables.table("sample_data")
            if row["sample_token"] == sample_token
            and row["is_key_frame"]
            and sensors[calibrated[row["calibrated_sensor_token"]]["sensor_token"]][
                "channel"
            ]
            == channel
        ]
        if len(found) != 1:
            raise ValueError(
                f"sample {sample_token} has {len(found)} {channel} key frames"
            )
        row = index[found[0]["token"]]
        return next(
            r for r in self.readings if r.path == self.tables.root / row["filename"]
        )

    def expected_labels(self) -> list[dict[str, Any]]:
        """Per sample in sample order: its token, LIDAR_TOP key-frame anchor and
        labels keyed by annotation token."""
        instances = self.tables.index("instance")
        categories = self.tables.index("category")
        attributes = self.tables.index("attribute")
        out = []
        for sample in self.samples:
            anchor = self.keyframe(sample["token"], "LIDAR_TOP")
            labels = {}
            for ann in self.annotations_by_sample[sample["token"]]:
                instance = instances[ann["instance_token"]]
                labels[ann["token"]] = {
                    "label_id": ann["token"],
                    "instance_id": ann["instance_token"],
                    "category": categories[instance["category_token"]]["name"],
                    "attributes": sorted(
                        attributes[a]["name"] for a in ann["attribute_tokens"]
                    ),
                    "center_m": ann["translation"],
                    "size_wlh_m": ann["size"],
                    "rotation_wxyz": ann["rotation"],
                }
            out.append(
                {
                    "sample_token": sample["token"],
                    "anchor_timestamp_ns": anchor.timestamp_ns,
                    "labels": labels,
                }
            )
        return out
