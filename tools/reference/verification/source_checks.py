"""Source integrity: the raw nuScenes scene, the locked recording prepared
from it, the corpus lock and the reference label artifact agree.

Expectations come from the raw tables and files by the source preparation's
documented mapping (nuscenes_source); observations come from the recording's
own messages (recording). The lock is checked against the recording bytes, so
a re-locked or substituted recording fails here, before anything canonical
is compared with it.
"""

from __future__ import annotations

import json
from typing import Any

from .findings import Findings
from .nuscenes_source import (
    CAN_TOPICS,
    LIDAR_TOPIC,
    NS_PER_US,
    NuScenesScene,
    camera_info_topic,
    camera_topic,
    file_sha256,
)
from .recording import RecordingFacts
from .rules import checksum


def check_lock(
    findings: Findings, facts: RecordingFacts, locked: dict[str, Any]
) -> None:
    f = findings.scoped(layer="source")
    f.equal(
        facts.checksum, locked["sha256"], "lock_recording_checksum", "recording sha256"
    )
    f.equal(
        facts.size_bytes, locked["size_bytes"], "lock_recording_size", "recording size"
    )
    f.equal(
        facts.message_count,
        locked["message_count"],
        "lock_message_count",
        "message count",
    )
    f.equal(
        facts.topic_counts,
        dict(sorted(locked["topic_counts"].items())),
        "lock_topic_counts",
        "per-topic counts",
    )


def _frame(channel: str) -> str:
    return channel.lower()


def check_recording_against_source(
    findings: Findings,
    facts: RecordingFacts,
    scene: NuScenesScene,
    *,
    hash_files: bool = True,
) -> None:
    f = findings.scoped(layer="source")
    by_topic = {t: facts.topic(t) for t in facts.topic_counts}

    sensors: list[tuple[str, str]] = [
        (c, camera_topic(c)) for c in scene.channels("camera")
    ]
    sensors += [(c, LIDAR_TOPIC[c]) for c in scene.channels("lidar")]
    for channel, topic in sensors:
        readings = scene.channel_readings(channel)
        entries = sorted(
            by_topic.get(topic, []), key=lambda e: (e.stamp_ns, e.channel_index)
        )
        f.equal(
            len(entries),
            len(readings),
            "source_message_count",
            f"{topic} message count",
        )
        f.equal(
            [e.stamp_ns for e in entries],
            [r.timestamp_ns for r in readings],
            "source_timestamp",
            f"{topic} header stamps (timestamp_us * 1000)",
        )
        f.check(
            {e.frame_id for e in entries} <= {_frame(channel)},
            "source_frame",
            f"{topic} frame ids {sorted({e.frame_id for e in entries})}, expected {_frame(channel)!r}",
        )
        if hash_files:
            f.equal(
                sorted((e.stamp_ns, e.image_checksum) for e in entries),
                sorted((r.timestamp_ns, file_sha256(r.path)) for r in readings),
                "source_payload_bytes",
                f"{topic} payload bytes (sha256 of the source file of each reading)",
            )
        if topic.startswith("/camera/"):
            info_topic = camera_info_topic(channel)
            infos = sorted(
                by_topic.get(info_topic, []),
                key=lambda e: (e.stamp_ns, e.channel_index),
            )
            f.equal(
                [e.stamp_ns for e in infos],
                [r.timestamp_ns for r in readings],
                "source_timestamp",
                f"{info_topic} stamps",
            )
            k = (
                [
                    v
                    for row in readings[0].calibrated_sensor["camera_intrinsic"]
                    for v in row
                ]
                if readings
                else []
            )
            for info, reading in zip(infos, readings):
                ci = info.camera_info
                assert ci is not None
                f.equal(
                    (ci.width, ci.height),
                    (reading.width, reading.height),
                    "source_image_size",
                    f"{info_topic} size",
                )
                f.equal(ci.k, k, "source_intrinsic", f"{info_topic} K")
                f.check(
                    all(v == 0.0 for v in ci.d),
                    "source_intrinsic",
                    f"{info_topic} distortion {ci.d}",
                )
                f.equal(
                    ci.p,
                    [
                        k[0],
                        k[1],
                        k[2],
                        0.0,
                        k[3],
                        k[4],
                        k[5],
                        0.0,
                        k[6],
                        k[7],
                        k[8],
                        0.0,
                    ],
                    "source_intrinsic",
                    f"{info_topic} P",
                )

    # /tf: one map -> base_link per sample_data of the unit, every sensor included.
    poses = sorted(
        (
            (
                t.stamp_ns,
                t.parent_frame_id,
                t.child_frame_id,
                t.translation_m,
                t.rotation_wxyz,
            )
            for e in by_topic.get("/tf", [])
            for t in e.transforms
        ),
        key=lambda p: (p[0], p[3], p[4]),
    )
    expected = sorted(
        (
            (
                r.timestamp_ns,
                "map",
                "base_link",
                r.ego_pose["translation"],
                r.ego_pose["rotation"],
            )
            for r in scene.readings
        ),
        key=lambda p: (p[0], p[3], p[4]),
    )
    f.equal(
        len(poses),
        len(expected),
        "source_message_count",
        "/tf transforms (one per sample_data)",
    )
    f.check(
        poses == expected,
        "source_pose",
        "/tf transforms differ from the scene's ego poses",
    )
    f.check(
        all(
            r.ego_pose["timestamp"] * NS_PER_US == r.timestamp_ns
            for r in scene.readings
        ),
        "source_pose",
        "an ego pose timestamp differs from its sample_data timestamp",
    )

    # /tf_static: base_link -> sensor frame per converted sensor.
    static = [t for e in by_topic.get("/tf_static", []) for t in e.transforms]
    expected_static = {}
    for channel, _topic in sensors:
        cs = {
            r.calibrated_sensor["token"]: r.calibrated_sensor
            for r in scene.channel_readings(channel)
        }
        f.equal(
            len(cs), 1, "source_calibration", f"{channel} calibrations in the scene"
        )
        only = next(iter(cs.values()))
        expected_static[_frame(channel)] = (
            "base_link",
            only["translation"],
            only["rotation"],
        )
    f.equal(
        {
            t.child_frame_id: (t.parent_frame_id, t.translation_m, t.rotation_wxyz)
            for t in static
        },
        expected_static,
        "source_calibration",
        "/tf_static transforms (calibrated_sensor of each converted sensor)",
    )

    # CAN extracts.
    for message, topics in CAN_TOPICS.items():
        rows = scene.can(message)
        stamps = sorted(r["utime"] * NS_PER_US for r in rows)
        for topic in topics:
            entries = by_topic.get(topic, [])
            observed = sorted(
                e.stamp_ns
                if e.stamp_ns is not None
                else (e.json or {}).get("source_timestamp_ns")
                for e in entries
            )
            f.equal(
                len(entries),
                len(rows),
                "source_message_count",
                f"{topic} count ({message} entries)",
            )
            f.equal(
                observed,
                stamps,
                "source_timestamp",
                f"{topic} source times (utime * 1000)",
            )
    controls = sorted(
        (
            (e.json or {}).get("source_timestamp_ns"),
            [(e.json or {}).get(k) for k in ("steering", "throttle", "brake")],
        )
        for e in by_topic.get("/vehicle/control", [])
    )
    expected_controls = sorted(
        (r["utime"] * NS_PER_US, [r["steering"], r["throttle"], r["brake"]])
        for r in scene.can("vehicle_monitor")
    )
    f.check(
        controls == expected_controls,
        "source_values",
        "/vehicle/control values differ from vehicle_monitor",
    )

    # Mission events at the unit's first and last source time.
    others = [e.log_time for e in facts.entries if e.topic != "/mission/status"]
    missions = sorted(by_topic.get("/mission/status", []), key=lambda e: e.file_index)
    states = [
        (
            (e.json or {}).get("operation_state"),
            (e.json or {}).get("source_timestamp_ns"),
            (e.json or {}).get("mission_id"),
        )
        for e in missions
    ]
    f.equal(
        states,
        [
            ("running", min(others), f"mission-{scene.name}"),
            ("completed", max(others), f"mission-{scene.name}"),
        ]
        if others
        else [],
        "source_mission",
        "mission events (running at the first, completed at the last source time)",
    )

    # Recording timing (README "Timing"): log_time = publish_time = source time.
    f.check(
        all(e.log_time == e.publish_time for e in facts.entries),
        "source_timing",
        "a message's publish_time differs from its log_time",
    )
    f.check(
        all(
            e.log_time == e.stamp_ns
            for e in facts.entries
            if e.stamp_ns is not None and e.topic != "/tf_static"
        ),
        "source_timing",
        "a stamped message's log_time differs from its header stamp",
    )


def check_labels(
    findings: Findings, data: bytes, locked: dict[str, Any], scene: NuScenesScene
) -> None:
    """The locked reference label artifact against the scene's annotations."""
    f = findings.scoped(layer="source")
    f.equal(
        checksum(data),
        locked["sha256"],
        "lock_labels_checksum",
        "label artifact sha256",
    )
    f.equal(len(data), locked["size_bytes"], "lock_labels_size", "label artifact size")
    artifact = json.loads(data)
    observed = [
        {
            "sample_token": s["sample_token"],
            "anchor_timestamp_ns": s["anchor_timestamp_ns"],
            "labels": {
                label["label_id"]: {**label, "attributes": sorted(label["attributes"])}
                for label in s["labels"]
            },
        }
        for s in artifact["samples"]
    ]
    expected = scene.expected_labels()
    f.equal(
        len(observed),
        len(expected),
        "source_label_samples",
        "label samples (one per scene sample)",
    )
    f.equal(
        [s["sample_token"] for s in observed],
        [s["sample_token"] for s in expected],
        "source_label_samples",
        "sample order",
    )
    f.equal(
        [s["anchor_timestamp_ns"] for s in observed],
        [s["anchor_timestamp_ns"] for s in expected],
        "source_label_anchor",
        "label anchors (the sample's LIDAR_TOP key frame)",
    )
    mismatched = [
        o["sample_token"]
        for o, e in zip(observed, expected)
        if o["labels"] != e["labels"]
    ]
    f.check(
        not mismatched,
        "source_label_content",
        f"labels differ from sample_annotation in samples {mismatched[:5]}",
    )
    f.equal(
        sum(len(s["labels"]) for s in observed),
        locked["label_count"],
        "lock_label_count",
        "label count",
    )
    f.equal(
        len(observed), locked["sample_count"], "lock_label_count", "label sample count"
    )
    f.equal(
        artifact.get("anchor_topic"),
        "/lidar/top/points",
        "source_label_anchor",
        "anchor topic",
    )
