"""Small, real ROS 2 (CDR / ros2msg) MCAP recordings for Episode-builder
tests, shaped like the step-6 acquisition output.

    /camera/front/image/compressed  sensor_msgs/msg/CompressedImage   observations at t0, t3, t8
    /vehicle/odom                   nav_msgs/msg/Odometry             states at t1, t2, t2, t7
                                                                      (t2 is recorded twice)
    /vehicle/control                std_msgs/msg/String (JSON)        actions at t2, t5, t9
    /mission/status                 std_msgs/msg/String (JSON)        running at t0, completed at t9

Source timestamps (``Header.stamp`` / the JSON ``source_timestamp_ns``) are
on the source clock; ``log_time`` lags them by ``LOG_LAG_NS``. Messages are
written in source-time order unless a test reorders them.
"""

from __future__ import annotations

import json
from typing import Any

from sceneops_recording.testing.ros2_recordings import (  # noqa: E402
    MSGDEFS,
    _SEP,
    _HEADER,
    _QUATERNION,
    _TIME,
    _VECTOR3,
    CAMERA_INFO_TOPIC,
    CAMERA_TOPIC,
    JPEG,
    Recording,
    camera_info,
    compressed_image,
    header,
    tf_message,
    transform,
)

MSGDEFS.update(
    {
        "std_msgs/msg/String": "string data\n",
        "nav_msgs/msg/Odometry": (
            "std_msgs/Header header\nstring child_frame_id\n"
            "geometry_msgs/PoseWithCovariance pose\n"
            "geometry_msgs/TwistWithCovariance twist\n"
            + _SEP
            + "MSG: std_msgs/Header\n"
            + _HEADER
            + _SEP
            + "MSG: builtin_interfaces/Time\n"
            + _TIME
            + _SEP
            + "MSG: geometry_msgs/PoseWithCovariance\nPose pose\nfloat64[36] covariance\n"
            + _SEP
            + "MSG: geometry_msgs/Pose\nPoint position\nQuaternion orientation\n"
            + _SEP
            + "MSG: geometry_msgs/Point\nfloat64 x\nfloat64 y\nfloat64 z\n"
            + _SEP
            + "MSG: geometry_msgs/Quaternion\n"
            + _QUATERNION
            + _SEP
            + "MSG: geometry_msgs/TwistWithCovariance\nTwist twist\nfloat64[36] covariance\n"
            + _SEP
            + "MSG: geometry_msgs/Twist\nVector3 linear\nVector3 angular\n"
            + _SEP
            + "MSG: geometry_msgs/Vector3\n"
            + _VECTOR3
        ),
    }
)

S = 1_000_000_000
T0 = 1_700_000_000 * S  # epoch-based source time, like CAN utime
LOG_LAG_NS = 1_000
ODOM = "/vehicle/odom"
CONTROL = "/vehicle/control"
MISSION = "/mission/status"
CLOCK = "vehicle.source_time"
MISSION_ID = "mission-1"

OBSERVATION_TIMES = (0, 3, 8)
STATE_TIMES = (1, 2, 2, 7)
ACTION_TIMES = (2, 5, 9)


def odometry(ns: int, x: float) -> dict[str, Any]:
    vector = {"x": 0.0, "y": 0.0, "z": 0.0}
    return {
        "header": header(ns, "odom"),
        "child_frame_id": "base_link",
        "pose": {
            "pose": {
                "position": {"x": x, "y": 2.0 * x, "z": 0.0},
                "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
            },
            "covariance": [0.0] * 36,
        },
        "twist": {
            "twist": {"linear": {"x": x / 10, "y": 0.0, "z": 0.0}, "angular": vector},
            "covariance": [0.0] * 36,
        },
    }


def json_string(**payload: Any) -> dict[str, str]:
    return {"data": json.dumps(payload)}


def episode_recording(*, mission: bool = True, extra_markers=()) -> Recording:
    rec = Recording()
    items: list[tuple[int, int, str, str, dict]] = []
    if mission:
        items.append(
            (
                0,
                0,
                MISSION,
                "std_msgs/msg/String",
                json_string(
                    mission_id=MISSION_ID,
                    operation_state="running",
                    source_timestamp_ns=T0,
                ),
            )
        )
        items.append(
            (
                9,
                9,
                MISSION,
                "std_msgs/msg/String",
                json_string(
                    mission_id=MISSION_ID,
                    operation_state="completed",
                    source_timestamp_ns=T0 + 9 * S,
                ),
            )
        )
    for t, state, key in extra_markers:
        items.append(
            (
                t,
                5,
                MISSION,
                "std_msgs/msg/String",
                json_string(
                    mission_id=key,
                    operation_state=state,
                    source_timestamp_ns=T0 + t * S,
                ),
            )
        )
    # L1 R9: the camera's calibration is recorded before its first image.
    items.append(
        (
            0,
            -1,
            "/tf_static",
            "tf2_msgs/msg/TFMessage",
            tf_message(
                transform(
                    T0,
                    "base_link",
                    "cam_front",
                    (1.7, 0.0, 1.5),
                    (-0.5, 0.5, -0.5, 0.5),
                )
            ),
        )
    )
    for i, t in enumerate(OBSERVATION_TIMES):
        items.append(
            (
                t,
                1,
                CAMERA_INFO_TOPIC,
                "sensor_msgs/msg/CameraInfo",
                camera_info(T0 + t * S),
            )
        )
        items.append(
            (
                t,
                1,
                CAMERA_TOPIC,
                "sensor_msgs/msg/CompressedImage",
                compressed_image(T0 + t * S, data=JPEG + bytes([i])),
            )
        )
    for i, t in enumerate(STATE_TIMES):
        items.append(
            (t, 2, ODOM, "nav_msgs/msg/Odometry", odometry(T0 + t * S, float(t)))
        )
    for i, t in enumerate(ACTION_TIMES):
        items.append(
            (
                t,
                3,
                CONTROL,
                "std_msgs/msg/String",
                json_string(
                    steering=t / 10,
                    throttle=0.5,
                    brake=0,
                    source_timestamp_ns=T0 + t * S,
                ),
            )
        )
    sequences: dict[str, int] = {}
    for t, _, topic, schema, message in sorted(items, key=lambda i: (i[0], i[1])):
        sequences[topic] = sequences.get(topic, 0) + 1
        rec.add(
            topic, schema, message, T0 + t * S + LOG_LAG_NS, sequence=sequences[topic]
        )
    return rec


def with_odometry(rec: Recording, stamps_ns) -> Recording:
    """Add odometry at ``stamps_ns`` (log_time = stamp + 5) and keep the
    recording in receive-time order, as a recorder writes it."""
    for i, t in enumerate(stamps_ns):
        rec.add(
            ODOM, "nav_msgs/msg/Odometry", odometry(t, float(i)), t + 5, sequence=i + 1
        )
    rec.messages.sort(key=lambda m: m.log_time)
    return rec


def _payload_time(clock: str = CLOCK) -> dict:
    return {"source": "payload_field", "clock": clock, "field": "source_timestamp_ns"}


def episode_build_config(
    *,
    segmentation: dict | None = None,
    clock: str = CLOCK,
    with_camera: bool = True,
    with_events: bool = True,
    action_fields: tuple[str, ...] = ("steering", "throttle", "brake"),
) -> dict:
    header_time = {"source": "header_stamp", "clock": clock}
    streams = [
        {
            "topic": ODOM,
            "role": "state",
            "time": header_time,
            "fields": [
                {"name": "x", "path": "pose.pose.position.x"},
                {"name": "y", "path": "pose.pose.position.y"},
                {"name": "vx", "path": "twist.twist.linear.x"},
            ],
        },
        {
            "topic": CONTROL,
            "role": "action",
            "decoding": "json_string",
            "time": _payload_time(clock),
            "fields": [{"name": f, "path": f} for f in action_fields],
        },
    ]
    if with_camera:
        streams.append(
            {
                "topic": CAMERA_TOPIC,
                "role": "observation",
                "time": header_time,
                "payload": "compressed_image",
            }
        )
    events = (
        [
            {
                "topic": MISSION,
                "decoding": "json_string",
                "time": _payload_time(clock),
                "fields": [
                    {"name": "mission_id", "path": "mission_id"},
                    {"name": "state", "path": "operation_state"},
                ],
            }
        ]
        if with_events
        else []
    )
    return {
        "streams": streams,
        "events": events,
        "segmentation": segmentation
        or {
            "policy": "event_markers",
            "event_topic": MISSION,
            "key_field": "mission_id",
            "state_field": "state",
            "start_values": ["running"],
            "end_values": ["completed"],
        },
    }
