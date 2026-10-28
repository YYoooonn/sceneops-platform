"""Native reader of nuScenes v1.0 datasets (ADR-015 §6, §10; M3.0 D6).

Parses the preserved bytes of one SourceContent into typed native records,
through ``ContentFiles`` only, so every byte it reads is verified against the
content inventory first. It derives the format version from the bytes (the
one table directory, ``v1.0-<split>``) and refuses any other.

It decides nothing canonical: no unit key, clock, timestamp normalization,
modality mapping, coordinate frame, media type or manifest. Those belong to
the Scene builder (``sceneops_scenes.nuscenes_builder``), which sits on top of
this reader. Timestamps stay the source's integer microseconds.

Layout read (nuScenes v1.0)::

    v1.0-<split>/scene.json              scene: token, name, sample chain ends, nbr_samples
    v1.0-<split>/sample.json             sample: token, scene_token, timestamp, prev / next
    v1.0-<split>/sample_data.json        one row per sensor reading (key frames and sweeps):
                                         its sample, ego pose, calibrated sensor, timestamp,
                                         file, fileformat, is_key_frame, width / height
    v1.0-<split>/ego_pose.json           ego pose in the log's global frame
    v1.0-<split>/calibrated_sensor.json  sensor extrinsics in the ego frame (+ camera K)
    v1.0-<split>/sensor.json             channel and modality of each sensor

Sensor files, CAN, maps and the annotation tables are never read: a reading's
file is only named (``NuScenesSampleData.filename``), so a caller that
materializes content for the reader can leave them out.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from sceneops_core.sources import SourceContentInventory

from .content_files import ContentFiles

NUSCENES_TABLE_VERSION_PREFIX: Final = "v1.0-"
NUSCENES_TABLES: Final = (
    "calibrated_sensor",
    "ego_pose",
    "sample",
    "sample_data",
    "scene",
    "sensor",
)


class NuScenesFormatError(ValueError):
    """The content is not a readable nuScenes dataset of the supported layout."""


class UnsupportedNuScenesVersionError(NuScenesFormatError):
    """The content's table version is not one this reader supports."""


Vector = tuple[float, ...]


@dataclass(frozen=True)
class NuScenesSensor:
    token: str
    channel: str
    modality: str


@dataclass(frozen=True)
class NuScenesCalibratedSensor:
    token: str
    sensor_token: str
    translation: Vector
    rotation: Vector
    # Rows of the 3x3 camera matrix as stored, or () when the source has none.
    camera_intrinsic: tuple[Vector, ...]


@dataclass(frozen=True)
class NuScenesEgoPose:
    token: str
    timestamp: int
    translation: Vector
    rotation: Vector


@dataclass(frozen=True)
class NuScenesSample:
    token: str
    scene_token: str
    timestamp: int
    prev: str
    next: str


@dataclass(frozen=True)
class NuScenesSampleData:
    """One sensor reading, as the table holds it."""

    token: str
    sample_token: str
    ego_pose_token: str
    calibrated_sensor_token: str
    timestamp: int
    fileformat: str
    is_key_frame: bool
    width: int
    height: int
    filename: str


@dataclass(frozen=True)
class NuScenesScene:
    token: str
    name: str
    first_sample_token: str
    last_sample_token: str
    nbr_samples: int


@dataclass(frozen=True)
class NuScenesContent:
    version: str
    scenes: list[NuScenesScene]
    samples: Mapping[str, NuScenesSample]
    sample_data: Mapping[str, NuScenesSampleData]
    ego_poses: Mapping[str, NuScenesEgoPose]
    calibrated_sensors: Mapping[str, NuScenesCalibratedSensor]
    sensors: Mapping[str, NuScenesSensor]

    def scene(self, token: str) -> NuScenesScene | None:
        return next((s for s in self.scenes if s.token == token), None)

    def scene_samples(self, scene: NuScenesScene) -> list[NuScenesSample]:
        """The scene's samples in the source's sample order, following
        ``first_sample_token`` -> ``next``. The chain must stay in the scene,
        end at ``last_sample_token`` and hold ``nbr_samples`` samples."""
        out: list[NuScenesSample] = []
        token = scene.first_sample_token
        while token:
            sample = self.samples.get(token)
            if sample is None or sample.scene_token != scene.token:
                raise NuScenesFormatError(
                    f"scene {scene.token}: sample chain reaches {token!r}, which is "
                    "not a sample of the scene"
                )
            if len(out) > len(self.samples):
                raise NuScenesFormatError(f"scene {scene.token}: sample chain loops")
            out.append(sample)
            token = sample.next
        if not out or out[-1].token != scene.last_sample_token:
            raise NuScenesFormatError(
                f"scene {scene.token}: sample chain does not end at its "
                f"last_sample_token {scene.last_sample_token!r}"
            )
        if len(out) != scene.nbr_samples:
            raise NuScenesFormatError(
                f"scene {scene.token}: {len(out)} chained samples, the scene "
                f"declares {scene.nbr_samples}"
            )
        members = {
            s.token for s in self.samples.values() if s.scene_token == scene.token
        }
        if members != {s.token for s in out}:
            raise NuScenesFormatError(
                f"scene {scene.token}: samples of the scene are outside its chain"
            )
        return out

    def scene_readings(self, scene: NuScenesScene) -> list[NuScenesSampleData]:
        """Every sensor reading the source assigns to one of the scene's
        samples (key frames and sweeps), in table order."""
        samples = {s.token for s in self.scene_samples(scene)}
        return [r for r in self.sample_data.values() if r.sample_token in samples]

    def sensor_of(self, reading: NuScenesSampleData) -> NuScenesSensor:
        calibrated = self.calibrated_sensors[reading.calibrated_sensor_token]
        return self.sensors[calibrated.sensor_token]


# --- table parsing -------------------------------------------------------------


def _str(row: Mapping[str, Any], key: str, where: str, *, empty: bool = False) -> str:
    value = row.get(key)
    if not isinstance(value, str) or (not empty and not value):
        raise NuScenesFormatError(f"{where}: {key!r} is not a non-empty string")
    return value


def _int(row: Mapping[str, Any], key: str, where: str) -> int:
    value = row.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise NuScenesFormatError(f"{where}: {key!r} is not an integer")
    return value


def _bool(row: Mapping[str, Any], key: str, where: str) -> bool:
    value = row.get(key)
    if not isinstance(value, bool):
        raise NuScenesFormatError(f"{where}: {key!r} is not a boolean")
    return value


def _vector(value: Any, length: int, what: str) -> Vector:
    if (
        not isinstance(value, list)
        or len(value) != length
        or any(
            isinstance(v, bool)
            or not isinstance(v, int | float)
            or not math.isfinite(v)
            for v in value
        )
    ):
        raise NuScenesFormatError(f"{what} is not a list of {length} finite numbers")
    return tuple(float(v) for v in value)


def _camera_intrinsic(value: Any, what: str) -> tuple[Vector, ...]:
    if value == []:
        return ()
    if not isinstance(value, list) or len(value) != 3:
        raise NuScenesFormatError(f"{what} is neither [] nor a 3x3 matrix")
    return tuple(_vector(row, 3, what) for row in value)


def _rows(files: ContentFiles, version: str, name: str) -> list[dict[str, Any]]:
    path = f"{version}/{name}.json"
    try:
        rows = json.loads(files.local_path(path).read_bytes())
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NuScenesFormatError(f"{path} is not JSON: {exc}") from exc
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise NuScenesFormatError(f"{path} is not a list of rows")
    return rows


def _by_token(items: list[Any], table: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in items:
        if item.token in out:
            raise NuScenesFormatError(f"{table} lists token {item.token!r} twice")
        out[item.token] = item
    return out


def _table_version(inventory: SourceContentInventory) -> str:
    versions = sorted(
        {
            entry.path.split("/")[0]
            for entry in inventory.files
            if entry.path.count("/") == 1 and entry.path.endswith("/scene.json")
        }
    )
    if len(versions) != 1:
        raise NuScenesFormatError(
            f"the content holds {len(versions)} nuScenes table directories "
            f"{versions}; exactly one is read"
        )
    (version,) = versions
    if not version.startswith(NUSCENES_TABLE_VERSION_PREFIX):
        raise UnsupportedNuScenesVersionError(
            f"nuScenes table version {version!r} is not supported; this reader "
            f"reads {NUSCENES_TABLE_VERSION_PREFIX}<split>"
        )
    paths = {entry.path for entry in inventory.files}
    missing = [n for n in NUSCENES_TABLES if f"{version}/{n}.json" not in paths]
    if missing:
        raise NuScenesFormatError(f"{version} has no table(s) {missing}")
    return version


def nuscenes_read_paths(inventory: SourceContentInventory) -> list[str]:
    """Every file of ``inventory`` this reader may read: the tables it parses.
    Sensor files and anything else are never read."""
    version = _table_version(inventory)
    return [f"{version}/{name}.json" for name in NUSCENES_TABLES]


def read_nuscenes_content(files: ContentFiles) -> NuScenesContent:
    """The native structure of a nuScenes v1.0 content, with every token
    reference between the tables read checked to resolve."""
    version = _table_version(files.inventory)

    def rows(name: str) -> list[dict[str, Any]]:
        return _rows(files, version, name)

    sensors = _by_token(
        [
            NuScenesSensor(
                token=_str(r, "token", "sensor"),
                channel=_str(r, "channel", "sensor"),
                modality=_str(r, "modality", "sensor"),
            )
            for r in rows("sensor")
        ],
        "sensor",
    )
    calibrated = _by_token(
        [
            NuScenesCalibratedSensor(
                token=(token := _str(r, "token", "calibrated_sensor")),
                sensor_token=_str(r, "sensor_token", f"calibrated_sensor {token}"),
                translation=_vector(
                    r.get("translation"), 3, f"calibrated_sensor {token} translation"
                ),
                rotation=_vector(
                    r.get("rotation"), 4, f"calibrated_sensor {token} rotation"
                ),
                camera_intrinsic=_camera_intrinsic(
                    r.get("camera_intrinsic"),
                    f"calibrated_sensor {token} camera_intrinsic",
                ),
            )
            for r in rows("calibrated_sensor")
        ],
        "calibrated_sensor",
    )
    ego_poses = _by_token(
        [
            NuScenesEgoPose(
                token=(token := _str(r, "token", "ego_pose")),
                timestamp=_int(r, "timestamp", f"ego_pose {token}"),
                translation=_vector(
                    r.get("translation"), 3, f"ego_pose {token} translation"
                ),
                rotation=_vector(r.get("rotation"), 4, f"ego_pose {token} rotation"),
            )
            for r in rows("ego_pose")
        ],
        "ego_pose",
    )
    scenes = [
        NuScenesScene(
            token=(token := _str(r, "token", "scene")),
            name=_str(r, "name", f"scene {token}"),
            first_sample_token=_str(r, "first_sample_token", f"scene {token}"),
            last_sample_token=_str(r, "last_sample_token", f"scene {token}"),
            nbr_samples=_int(r, "nbr_samples", f"scene {token}"),
        )
        for r in rows("scene")
    ]
    scene_tokens = _by_token(scenes, "scene")
    samples = _by_token(
        [
            NuScenesSample(
                token=(token := _str(r, "token", "sample")),
                scene_token=_str(r, "scene_token", f"sample {token}"),
                timestamp=_int(r, "timestamp", f"sample {token}"),
                prev=_str(r, "prev", f"sample {token}", empty=True),
                next=_str(r, "next", f"sample {token}", empty=True),
            )
            for r in rows("sample")
        ],
        "sample",
    )
    sample_data = _by_token(
        [
            NuScenesSampleData(
                token=(token := _str(r, "token", "sample_data")),
                sample_token=_str(r, "sample_token", f"sample_data {token}"),
                ego_pose_token=_str(r, "ego_pose_token", f"sample_data {token}"),
                calibrated_sensor_token=_str(
                    r, "calibrated_sensor_token", f"sample_data {token}"
                ),
                timestamp=_int(r, "timestamp", f"sample_data {token}"),
                fileformat=_str(r, "fileformat", f"sample_data {token}"),
                is_key_frame=_bool(r, "is_key_frame", f"sample_data {token}"),
                width=_int(r, "width", f"sample_data {token}"),
                height=_int(r, "height", f"sample_data {token}"),
                filename=_str(r, "filename", f"sample_data {token}"),
            )
            for r in rows("sample_data")
        ],
        "sample_data",
    )

    def require(table: str, token: str, known: Mapping[str, Any], where: str) -> None:
        if token not in known:
            raise NuScenesFormatError(f"{where} references unknown {table} {token!r}")

    for cs in calibrated.values():
        require("sensor", cs.sensor_token, sensors, f"calibrated_sensor {cs.token}")
    for sample in samples.values():
        require("scene", sample.scene_token, scene_tokens, f"sample {sample.token}")
    for reading in sample_data.values():
        where = f"sample_data {reading.token}"
        require("sample", reading.sample_token, samples, where)
        require("ego_pose", reading.ego_pose_token, ego_poses, where)
        require("calibrated_sensor", reading.calibrated_sensor_token, calibrated, where)
    return NuScenesContent(
        version=version,
        scenes=scenes,
        samples=samples,
        sample_data=sample_data,
        ego_poses=ego_poses,
        calibrated_sensors=calibrated,
        sensors=sensors,
    )


__all__ = [
    "NUSCENES_TABLES",
    "NUSCENES_TABLE_VERSION_PREFIX",
    "NuScenesCalibratedSensor",
    "NuScenesContent",
    "NuScenesEgoPose",
    "NuScenesFormatError",
    "NuScenesSample",
    "NuScenesSampleData",
    "NuScenesScene",
    "NuScenesSensor",
    "UnsupportedNuScenesVersionError",
    "nuscenes_read_paths",
    "read_nuscenes_content",
]
