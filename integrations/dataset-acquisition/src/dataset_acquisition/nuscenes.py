"""nuScenes -> acquisition events, as the vehicle's own ROS 2 stack would
have published them.

One source unit (a nuScenes scene name) selects the source records to
convert: every camera / lidar ``sample_data`` the dataset associates with
that scene's samples (key frames *and* sweeps), their ego poses, the scene's
CAN bus extract and two synthetic mission events. The unit name selects
input; it does not define a SceneOps Scene (§29.13). The recording carries
no boundary, keyframe flag, annotation or nuScenes token.

Topics, types and frames
------------------------

    /camera/<pos>/image/compressed  sensor_msgs/msg/CompressedImage  source JPEG bytes, frame cam_<pos>
    /camera/<pos>/camera_info       sensor_msgs/msg/CameraInfo       intrinsics, same header as the image
    /lidar/top/points               sensor_msgs/msg/PointCloud2      source .pcd.bin bytes, frame lidar_top
    /tf_static                      tf2_msgs/msg/TFMessage           base_link -> every sensor frame
    /tf                             tf2_msgs/msg/TFMessage           map -> base_link (ego pose)
    /vehicle/odom                   nav_msgs/msg/Odometry            CAN pose
    /vehicle/imu                    sensor_msgs/msg/Imu              CAN ms_imu
    /vehicle/status                 sensor_msgs/msg/BatteryState     CAN vehicle_monitor
    /vehicle/control                std_msgs/msg/String (JSON)       CAN vehicle_monitor
    /mission/status                 std_msgs/msg/String (JSON)       running / completed

``<pos>`` is the camera channel name without ``CAM_``, lower-cased
(``CAM_FRONT_LEFT`` -> ``front_left``). Radar is not converted.

Timing
------

Every source observation time stays in its message, unrewritten:
``sample_data.timestamp``, ``ego_pose.timestamp`` and CAN ``utime`` (all
integer microseconds since the Unix epoch) become ``Header.stamp`` in ns, or
the ``source_timestamp_ns`` JSON field of a String. The event's source time
is that same value. Calibration has no source time: ``/tf_static`` is
stamped and ordered at the first source time of the unit, before anything
that depends on it. The mission events sit at the unit's first and last
source times -- on the source timeline, never the conversion clock (R11).

Order is deterministic: by source time, then ``/tf_static`` and the
"running" event first and the "completed" event last, then topic, then
source order within the topic. Each topic numbers its messages 1, 2, ...
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import __version__
from . import ros2
from .events import AcquisitionError, AcquisitionEvent, MessageType

SOURCE_FORMAT = "nuscenes"

CAMERA = "camera"
LIDAR = "lidar"
POSE = "pose"
CAN = "can"
MISSION = "mission"
CHANNEL_GROUPS = (CAMERA, LIDAR, POSE, CAN, MISSION)

CAMERA_MODALITY = "camera"
LIDAR_MODALITY = "lidar"
# CAN bus extracts the CAN group converts (can_bus/<unit>_<name>.json).
CAN_MESSAGES = ("pose", "ms_imu", "vehicle_monitor")

BASE_FRAME = "base_link"
MAP_FRAME = "map"
CAN_ODOM_FRAME = "odom"
CAN_IMU_FRAME = "imu"

# Lidar .pcd.bin: little-endian float32 x, y, z, intensity, ring index.
_LIDAR_FIELDS = ("x", "y", "z", "intensity", "ring")
_FLOAT32 = 7  # sensor_msgs/msg/PointField.FLOAT32
_LIDAR_POINT_STEP = 4 * len(_LIDAR_FIELDS)

# Tie ranks among records at the same source time.
_FIRST, _OBSERVATION, _LAST = 0, 1, 2


def _us_to_ns(utime_us: int) -> int:
    return int(utime_us) * 1_000


def _quaternion(wxyz: list[float]) -> Any:
    # nuScenes stores quaternions as (w, x, y, z).
    w, x, y, z = wxyz
    return ros2.make("geometry_msgs/msg/Quaternion", x=x, y=y, z=z, w=w)


def _vector3(xyz: list[float]) -> Any:
    x, y, z = xyz
    return ros2.make("geometry_msgs/msg/Vector3", x=x, y=y, z=z)


def _transform(
    timestamp_ns: int,
    parent: str,
    child: str,
    translation: list[float],
    rotation: list[float],
) -> Any:
    return ros2.make(
        "geometry_msgs/msg/TransformStamped",
        header=ros2.header(timestamp_ns, parent),
        child_frame_id=child,
        transform=ros2.make(
            "geometry_msgs/msg/Transform",
            translation=_vector3(translation),
            rotation=_quaternion(rotation),
        ),
    )


def camera_position(channel: str) -> str:
    return channel.removeprefix("CAM_").lower()


def sensor_frame(channel: str) -> str:
    return channel.lower()


def camera_image_topic(channel: str) -> str:
    return f"/camera/{camera_position(channel)}/image/compressed"


def camera_info_topic(channel: str) -> str:
    return f"/camera/{camera_position(channel)}/camera_info"


def lidar_topic(channel: str) -> str:
    return f"/lidar/{channel.removeprefix('LIDAR_').lower()}/points"


# -- message builders (pure: source record in, ROS 2 message out) -----------


def build_compressed_image(sample_data: Mapping[str, Any], jpeg: bytes) -> Any:
    return ros2.make(
        "sensor_msgs/msg/CompressedImage",
        header=ros2.header(
            _us_to_ns(sample_data["timestamp"]), sensor_frame(sample_data["channel"])
        ),
        format="jpeg",
        data=ros2.uint8_array(jpeg),
    )


def build_camera_info(
    sample_data: Mapping[str, Any], calibrated_sensor: Mapping[str, Any]
) -> Any:
    """Pinhole intrinsics of the (already undistorted) nuScenes image:
    ``plumb_bob`` with zero distortion, identity rectification and
    ``P = [K | 0]``."""
    import numpy as np

    k = np.asarray(calibrated_sensor["camera_intrinsic"], dtype=np.float64)
    if k.shape != (3, 3):
        raise AcquisitionError(
            f"camera {sample_data['channel']} has no 3x3 intrinsic matrix"
        )
    p = np.zeros((3, 4), dtype=np.float64)
    p[:, :3] = k
    return ros2.make(
        "sensor_msgs/msg/CameraInfo",
        header=ros2.header(
            _us_to_ns(sample_data["timestamp"]), sensor_frame(sample_data["channel"])
        ),
        height=int(sample_data["height"]),
        width=int(sample_data["width"]),
        distortion_model="plumb_bob",
        d=np.zeros(5, dtype=np.float64),
        k=k.reshape(9),
        r=np.eye(3, dtype=np.float64).reshape(9),
        p=p.reshape(12),
    )


def build_point_cloud(sample_data: Mapping[str, Any], points: bytes) -> Any:
    if len(points) % _LIDAR_POINT_STEP:
        raise AcquisitionError(
            f"{sample_data['filename']}: {len(points)} bytes is not a whole number "
            f"of {_LIDAR_POINT_STEP}-byte points"
        )
    width = len(points) // _LIDAR_POINT_STEP
    return ros2.make(
        "sensor_msgs/msg/PointCloud2",
        header=ros2.header(
            _us_to_ns(sample_data["timestamp"]), sensor_frame(sample_data["channel"])
        ),
        height=1,
        width=width,
        fields=[
            ros2.make(
                "sensor_msgs/msg/PointField",
                name=name,
                offset=4 * i,
                datatype=_FLOAT32,
                count=1,
            )
            for i, name in enumerate(_LIDAR_FIELDS)
        ],
        is_bigendian=False,
        point_step=_LIDAR_POINT_STEP,
        row_step=len(points),
        data=ros2.uint8_array(points),
        # The source does not state that every point is valid.
        is_dense=False,
    )


def build_tf_static(
    timestamp_ns: int, calibrations: Mapping[str, Mapping[str, Any]]
) -> Any:
    """``calibrations``: sensor channel -> its calibrated_sensor record (the
    sensor's pose in the ego frame)."""
    return ros2.make(
        "tf2_msgs/msg/TFMessage",
        transforms=[
            _transform(
                timestamp_ns,
                BASE_FRAME,
                sensor_frame(channel),
                calibrations[channel]["translation"],
                calibrations[channel]["rotation"],
            )
            for channel in sorted(calibrations, key=sensor_frame)
        ],
    )


def build_ego_tf(ego_pose: Mapping[str, Any]) -> Any:
    return ros2.make(
        "tf2_msgs/msg/TFMessage",
        transforms=[
            _transform(
                _us_to_ns(ego_pose["timestamp"]),
                MAP_FRAME,
                BASE_FRAME,
                ego_pose["translation"],
                ego_pose["rotation"],
            )
        ],
    )


def build_odometry(pose: Mapping[str, Any]) -> Any:
    x, y, z = pose["pos"]
    return ros2.make(
        "nav_msgs/msg/Odometry",
        header=ros2.header(_us_to_ns(pose["utime"]), CAN_ODOM_FRAME),
        pose=ros2.make(
            "geometry_msgs/msg/PoseWithCovariance",
            pose=ros2.make(
                "geometry_msgs/msg/Pose",
                position=ros2.make("geometry_msgs/msg/Point", x=x, y=y, z=z),
                orientation=_quaternion(pose["orientation"]),
            ),
        ),
        twist=ros2.make(
            "geometry_msgs/msg/TwistWithCovariance",
            twist=ros2.make("geometry_msgs/msg/Twist", linear=_vector3(pose["vel"])),
        ),
    )


def build_imu(ms_imu: Mapping[str, Any]) -> Any:
    return ros2.make(
        "sensor_msgs/msg/Imu",
        header=ros2.header(_us_to_ns(ms_imu["utime"]), CAN_IMU_FRAME),
        orientation=_quaternion(ms_imu["q"]),
        angular_velocity=_vector3(ms_imu["rotation_rate"]),
        linear_acceleration=_vector3(ms_imu["linear_accel"]),
    )


def build_status(vehicle_monitor: Mapping[str, Any]) -> Any:
    return ros2.make(
        "sensor_msgs/msg/BatteryState",
        header=ros2.header(_us_to_ns(vehicle_monitor["utime"])),
        percentage=float(vehicle_monitor["battery_level"]) / 100.0,
        present=True,
    )


def build_control(vehicle_monitor: Mapping[str, Any]) -> Any:
    # Observed vehicle feedback, not a command. std_msgs/String carries no
    # Header, so the CAN observation time travels as a JSON field.
    return ros2.make(
        "std_msgs/msg/String",
        data=json.dumps(
            {
                "steering": vehicle_monitor["steering"],
                "throttle": vehicle_monitor["throttle"],
                "brake": vehicle_monitor["brake"],
                "source_timestamp_ns": _us_to_ns(vehicle_monitor["utime"]),
            }
        ),
    )


def build_mission_status(
    mission_id: str, operation_state: str, timestamp_ns: int
) -> Any:
    return ros2.make(
        "std_msgs/msg/String",
        data=json.dumps(
            {
                "mission_id": mission_id,
                "operation_state": operation_state,
                "source_timestamp_ns": timestamp_ns,
            }
        ),
    )


# -- adapter -----------------------------------------------------------------


@dataclass(frozen=True)
class _Planned:
    time_ns: int
    rank: int
    topic: str
    order: int
    build: Callable[[], Any]


@dataclass(frozen=True)
class SourceAnnotation:
    """One ``sample_annotation``: a 3-D box in the global (map) frame,
    size ``[width, length, height]``, rotation ``[w, x, y, z]``."""

    token: str
    instance_token: str
    category: str
    translation: list[float]
    size_wlh: list[float]
    rotation_wxyz: list[float]
    attributes: list[str]


@dataclass(frozen=True)
class KeyframeAnnotations:
    sample_token: str
    anchor_channel: str
    anchor_timestamp_ns: int
    annotations: list[SourceAnnotation]


@dataclass(frozen=True)
class SourceFingerprint:
    sha256: str
    counts: dict[str, Any]
    blob_bytes: int


def _file_identity(path: Path) -> tuple[int, str]:
    """(size in bytes, ``sha256:`` of the content), streamed."""
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1 << 20), b""):
                digest.update(block)
                size += len(block)
    except OSError as exc:
        raise AcquisitionError(f"source file unreadable: {path}: {exc}") from exc
    return size, f"sha256:{digest.hexdigest()}"


@dataclass(frozen=True)
class NuScenesSelection:
    dataroot: Path
    version: str
    source_unit: str
    channel_groups: frozenset[str] = frozenset(CHANNEL_GROUPS)

    def __post_init__(self) -> None:
        unknown = self.channel_groups - set(CHANNEL_GROUPS)
        if unknown or not self.channel_groups:
            raise AcquisitionError(
                f"channel groups must be a non-empty subset of {CHANNEL_GROUPS}, "
                f"got {sorted(self.channel_groups)}"
            )


class NuScenesAdapter:
    """``DatasetAdapter`` for one nuScenes scene. Loads tables with the
    official nuscenes-devkit; reads image and lidar files as raw bytes."""

    def __init__(self, selection: NuScenesSelection, *, nusc: Any = None) -> None:
        self.selection = selection
        if nusc is None:
            from nuscenes.nuscenes import NuScenes

            nusc = NuScenes(
                version=selection.version,
                dataroot=str(selection.dataroot),
                verbose=False,
            )
        self._nusc = nusc
        scenes = [s for s in nusc.scene if s["name"] == selection.source_unit]
        if len(scenes) != 1:
            raise AcquisitionError(
                f"source unit {selection.source_unit!r} not found in nuScenes "
                f"{selection.version}"
            )
        self._scene = scenes[0]

    def origin(self) -> dict[str, str]:
        return {
            "tool": "sceneops-dataset-acquisition",
            "tool_version": __version__,
            "source_format": SOURCE_FORMAT,
            "source_version": self.selection.version,
            "source_unit": self.selection.source_unit,
            "channel_groups": ",".join(
                g for g in CHANNEL_GROUPS if g in self.selection.channel_groups
            ),
        }

    # -- source records ------------------------------------------------------

    def _unit_sample_data(self) -> list[dict[str, Any]]:
        """Every sample_data (key frames and sweeps, all sensors) the dataset
        associates with this scene's samples, in (timestamp, token) order."""
        sample_tokens = set()
        token = self._scene["first_sample_token"]
        while token:
            sample_tokens.add(token)
            token = self._nusc.get("sample", token)["next"]
        records = [
            sd for sd in self._nusc.sample_data if sd["sample_token"] in sample_tokens
        ]
        return sorted(records, key=lambda sd: (sd["timestamp"], sd["token"]))

    def _calibration(
        self, records: list[dict[str, Any]], channel: str
    ) -> dict[str, Any]:
        tokens = {
            sd["calibrated_sensor_token"] for sd in records if sd["channel"] == channel
        }
        if len(tokens) != 1:
            raise AcquisitionError(
                f"{self.selection.source_unit}: {channel} has {len(tokens)} calibrations; "
                f"a recording carries one static calibration per sensor"
            )
        return self._nusc.get("calibrated_sensor", tokens.pop())

    def _read(self, relative: str) -> bytes:
        return (self.selection.dataroot / relative).read_bytes()

    def _can_messages(self, message_name: str) -> list[dict[str, Any]]:
        from nuscenes.can_bus.can_bus_api import NuScenesCanBus

        try:
            can_bus = NuScenesCanBus(dataroot=str(self.selection.dataroot))
            messages = can_bus.get_messages(
                self.selection.source_unit, message_name, print_warnings=False
            )
        except Exception as exc:
            raise AcquisitionError(
                f"{self.selection.source_unit}: CAN bus {message_name!r} unavailable: {exc}"
            ) from exc
        return sorted(messages, key=lambda m: m["utime"])

    # -- planning --------------------------------------------------------------

    def _plan(self) -> list[_Planned]:
        groups = self.selection.channel_groups
        records = self._unit_sample_data()
        plan: list[_Planned] = []

        def add(
            time_ns: int, topic: str, build: Callable[[], Any], rank: int = _OBSERVATION
        ) -> None:
            plan.append(_Planned(time_ns, rank, topic, len(plan), build))

        calibrations: dict[str, dict[str, Any]] = {}
        for sd in records:
            modality, channel = sd["sensor_modality"], sd["channel"]
            time_ns = _us_to_ns(sd["timestamp"])
            if modality == "camera" and CAMERA in groups:
                if channel not in calibrations:
                    calibrations[channel] = self._calibration(records, channel)
                calibration = calibrations[channel]
                add(
                    time_ns,
                    camera_info_topic(channel),
                    lambda sd=sd, c=calibration: build_camera_info(sd, c),
                )
                add(
                    time_ns,
                    camera_image_topic(channel),
                    lambda sd=sd: build_compressed_image(
                        sd, self._read(sd["filename"])
                    ),
                )
            elif modality == "lidar" and LIDAR in groups:
                if channel not in calibrations:
                    calibrations[channel] = self._calibration(records, channel)
                add(
                    time_ns,
                    lidar_topic(channel),
                    lambda sd=sd: build_point_cloud(sd, self._read(sd["filename"])),
                )

        if POSE in groups:
            # The vehicle's localization output: one ego pose per sample_data
            # of the unit, every sensor included, whether converted or not.
            seen: set[str] = set()
            for sd in records:
                if sd["ego_pose_token"] in seen:
                    continue
                seen.add(sd["ego_pose_token"])
                ego_pose = self._nusc.get("ego_pose", sd["ego_pose_token"])
                add(
                    _us_to_ns(ego_pose["timestamp"]),
                    "/tf",
                    lambda e=ego_pose: build_ego_tf(e),
                )

        if CAN in groups:
            for pose in self._can_messages("pose"):
                add(
                    _us_to_ns(pose["utime"]),
                    "/vehicle/odom",
                    lambda m=pose: build_odometry(m),
                )
            for imu in self._can_messages("ms_imu"):
                add(_us_to_ns(imu["utime"]), "/vehicle/imu", lambda m=imu: build_imu(m))
            for monitor in self._can_messages("vehicle_monitor"):
                time_ns = _us_to_ns(monitor["utime"])
                add(time_ns, "/vehicle/status", lambda m=monitor: build_status(m))
                add(time_ns, "/vehicle/control", lambda m=monitor: build_control(m))

        if not plan and not (MISSION in groups and records):
            raise AcquisitionError(
                f"{self.selection.source_unit}: selection {sorted(groups)} yields no messages"
            )
        start_ns = min(
            (p.time_ns for p in plan), default=_us_to_ns(records[0]["timestamp"])
        )
        end_ns = max(
            (p.time_ns for p in plan), default=_us_to_ns(records[-1]["timestamp"])
        )

        if calibrations:
            add(
                start_ns,
                "/tf_static",
                lambda: build_tf_static(start_ns, calibrations),
                _FIRST,
            )
        if MISSION in groups:
            mission_id = f"mission-{self.selection.source_unit}"
            add(
                start_ns,
                "/mission/status",
                lambda: build_mission_status(mission_id, "running", start_ns),
                _FIRST,
            )
            add(
                end_ns,
                "/mission/status",
                lambda: build_mission_status(mission_id, "completed", end_ns),
                _LAST,
            )

        return sorted(plan, key=lambda p: (p.time_ns, p.rank, p.topic, p.order))

    def plan_topic_counts(self) -> dict[str, int]:
        """Messages per topic the selection publishes, from the plan alone (no
        payload is read). Batch and replay sinks consume the same plan."""
        return dict(sorted(Counter(p.topic for p in self._plan()).items()))

    def source_fingerprint(self) -> SourceFingerprint:
        """Content identity of every source input the selection reads: the
        unit's table rows, the bytes of each converted image and point-cloud
        file, and the CAN bus extracts. It is independent of the tool, so a
        changed source is detected before any conversion."""
        groups = self.selection.channel_groups
        nusc = self._nusc
        records = self._unit_sample_data()
        converted = {
            modality
            for modality, group in ((CAMERA_MODALITY, CAMERA), (LIDAR_MODALITY, LIDAR))
            if group in groups
        }

        sample_rows, token = [], self._scene["first_sample_token"]
        while token:
            row = nusc.get("sample", token)
            sample_rows.append(row)
            token = row["next"]

        blobs = []
        for sd in records:
            if sd["sensor_modality"] not in converted:
                continue
            blobs.append(
                [
                    sd["filename"],
                    *_file_identity(self.selection.dataroot / sd["filename"]),
                ]
            )
        calibration_tokens = sorted(
            {
                sd["calibrated_sensor_token"]
                for sd in records
                if sd["sensor_modality"] in converted
            }
        )
        calibrations = [nusc.get("calibrated_sensor", t) for t in calibration_tokens]
        sensors = [
            nusc.get("sensor", t)
            for t in sorted({c["sensor_token"] for c in calibrations})
        ]
        ego_poses = []
        if POSE in groups:
            ego_tokens = sorted({sd["ego_pose_token"] for sd in records})
            ego_poses = [nusc.get("ego_pose", t) for t in ego_tokens]

        can_files: dict[str, list[Any]] = {}
        if CAN in groups:
            for name in CAN_MESSAGES:
                path = (
                    self.selection.dataroot
                    / "can_bus"
                    / f"{self.selection.source_unit}_{name}.json"
                )
                size, digest = _file_identity(path)
                can_files[name] = [
                    path.name,
                    size,
                    digest,
                    len(self._can_messages(name)),
                ]

        body = {
            "scene": self._scene,
            "log": nusc.get("log", self._scene["log_token"]),
            "samples": sample_rows,
            "sample_data": [sd for sd in records if sd["sensor_modality"] in converted],
            "calibrated_sensors": calibrations,
            "sensors": sensors,
            "ego_poses": ego_poses,
            "blobs": blobs,
            "can": can_files,
        }
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        counts: dict[str, Any] = {
            "samples": len(sample_rows),
            "camera_frames": sum(
                1
                for sd in records
                if sd["sensor_modality"] == CAMERA_MODALITY and CAMERA in groups
            ),
            "lidar_sweeps": sum(
                1
                for sd in records
                if sd["sensor_modality"] == LIDAR_MODALITY and LIDAR in groups
            ),
            "ego_poses": len(ego_poses),
            "can_messages": {name: row[3] for name, row in can_files.items()},
        }
        return SourceFingerprint(
            sha256=f"sha256:{hashlib.sha256(encoded).hexdigest()}",
            counts=counts,
            blob_bytes=sum(b[1] for b in blobs),
        )

    def channels(self) -> dict[str, MessageType]:
        """Topic -> message type for every topic the selection publishes
        (the first planned message of each topic is built to read its
        type)."""
        first: dict[str, _Planned] = {}
        for planned in self._plan():
            first.setdefault(planned.topic, planned)
        return {
            topic: ros2.message_type(planned.build().__msgtype__)
            for topic, planned in sorted(first.items())
        }

    def keyframe_annotations(self, anchor_channel: str) -> list[KeyframeAnnotations]:
        """The unit's source annotations (post-acquisition labeling), one entry
        per sample, in sample order. Each is anchored at the ``anchor_channel``
        key frame ``sample_data`` the sample points at, i.e. at an observation
        the recording carries. Annotation tables never enter the recording."""
        nusc = self._nusc
        entries: list[KeyframeAnnotations] = []
        token = self._scene["first_sample_token"]
        while token:
            sample = nusc.get("sample", token)
            if anchor_channel not in sample["data"]:
                raise AcquisitionError(
                    f"{self.selection.source_unit}: sample {token} has no "
                    f"{anchor_channel!r} data to anchor labels on"
                )
            anchor = nusc.get("sample_data", sample["data"][anchor_channel])
            annotations = []
            for ann_token in sample["anns"]:
                ann = nusc.get("sample_annotation", ann_token)
                instance = nusc.get("instance", ann["instance_token"])
                category = nusc.get("category", instance["category_token"])
                annotations.append(
                    SourceAnnotation(
                        token=ann["token"],
                        instance_token=ann["instance_token"],
                        category=category["name"],
                        translation=list(ann["translation"]),
                        size_wlh=list(ann["size"]),
                        rotation_wxyz=list(ann["rotation"]),
                        attributes=sorted(
                            {
                                nusc.get("attribute", t)["name"]
                                for t in ann["attribute_tokens"]
                            }
                        ),
                    )
                )
            entries.append(
                KeyframeAnnotations(
                    sample_token=token,
                    anchor_channel=anchor_channel,
                    anchor_timestamp_ns=_us_to_ns(anchor["timestamp"]),
                    annotations=annotations,
                )
            )
            token = sample["next"]
        return entries

    def events(self) -> Iterator[AcquisitionEvent]:
        sequences: dict[str, int] = {}
        for planned in self._plan():
            sequence = sequences.get(planned.topic, 0) + 1
            sequences[planned.topic] = sequence
            yield ros2.event(
                planned.topic,
                planned.build(),
                source_time_ns=planned.time_ns,
                sequence=sequence,
            )
