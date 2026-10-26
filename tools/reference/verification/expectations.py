"""Expected canonical Scenes and Episodes of one recording, derived from the
recording's own messages and a build configuration by the documented rules.

Rules (docs/architecture/scene-domain.md §1, §6; episode-domain.md §2–§4;
ingress-boundaries.md §6, §7; ADR-007 §30.4–§30.6; ADR-015 §6.2):

Both domains
  clock domains  one declaration per clock the unit uses (the window's clock and
                 every channel / pose / stream clock), sorted by clock; a recording
                 clock has origin `source` and resolution 1 ns, whatever the clock

Scene (RecordingSceneBuildConfig)
  channel        topic verbatim; modality / sensor_id from the config; frame_id =
                 the messages' constant header frame_id; source_clock = the
                 channel's time policy clock (header_stamp: the declared clock)
  observation    one per message of the channel; timestamp_ns = header stamp;
                 canonical order: timestamp, then MCAP sequence, then file order
  payload        compressed_image: sha256 / size of CompressedImage.data, media type
                 from `format` (jpeg -> image/jpeg); ros2_message: sha256 / size of
                 the recorded CDR message, media type
                 application/x.ros2-cdr.<package>.msg.<type, lowercased>
  calibration    per channel: the static transform ego frame -> channel frame;
                 camera_intrinsic = CameraInfo K (3 x 3); image_size = CameraInfo
                 width / height
  frames         ego (build config), world (build config), every channel frame
                 (sensor)
  poses          every configured parent -> child transform at its own stamp, in the
                 pose source's clock
  groups         none (a recording carries no keyframe grouping)
  segmentation   whole_recording: one unit, segment `recording`, window
                 [earliest, latest + 1) over every included observation and pose
                 on the segmentation clock
  unit key       <policy>-<sha256(boundary document)[:16]>/<segment>; boundary
                 document = the segmentation configuration (policy, clock[, duration_ns])

Episode (RecordingEpisodeBuildConfig)
  stream         topic, role, recorded schema_name, source_clock, fields, has_payload
  occurrence     one per message whose timestamp is in the window (same clock id);
                 timestamp from the time policy (header_stamp | payload_field);
                 values = the selected field paths as decoded; observation payloads
                 as for Scenes; canonical order: timestamp, MCAP sequence, file order
  event_markers  markers in canonical order; a start opens a task key, an end closes
                 it; window [start, end + 1); segment task-<key>-<occurrence>,
                 occurrence = 0-based count of that key's earlier windows, three digits
  boundary doc   policy, event_topic, the marker source's time policy (source, clock,
                 field: null when unused), decoding, key_path, state_path, sorted
                 unique start_values / end_values
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .recording import (
    IMAGE_SCHEMA,
    Entry,
    RecordingFacts,
)
from .rules import digest, recording_unit_key

RECORDING_CLOCK_ORIGIN = "source"
RECORDING_CLOCK_RESOLUTION_NS = 1

_IMAGE_MEDIA_TYPES = {"jpeg": "image/jpeg", "jpg": "image/jpeg", "png": "image/png"}


class ExpectationError(ValueError):
    """The recording cannot be canonicalized under the configuration by the
    documented rules (the platform must fail the build too)."""


def recording_clock_domains(clocks: set[str]) -> list[dict[str, Any]]:
    return [
        {
            "clock": clock,
            "origin": RECORDING_CLOCK_ORIGIN,
            "resolution_ns": RECORDING_CLOCK_RESOLUTION_NS,
        }
        for clock in sorted(clocks)
    ]


def _canonical_order(entries: list[Entry], timestamp: Any) -> list[Entry]:
    return sorted(entries, key=lambda e: (timestamp(e), e.sequence, e.file_index))


def _media_type_for(entry: Entry, extraction: str) -> tuple[str, str, int]:
    if extraction == "compressed_image":
        if entry.schema_name != IMAGE_SCHEMA:
            raise ExpectationError(
                f"{entry.topic}: compressed_image needs {IMAGE_SCHEMA}"
            )
        fmt = (entry.image_format or "").lower()
        media = _IMAGE_MEDIA_TYPES.get(fmt) or ("image/jpeg" if "jpeg" in fmt else None)
        if media is None:
            raise ExpectationError(
                f"{entry.topic}: unsupported image format {entry.image_format!r}"
            )
        assert entry.image_checksum is not None and entry.image_size_bytes is not None
        return entry.image_checksum, media, entry.image_size_bytes
    if extraction == "ros2_message":
        # ADR-007 §30.4: the type is lowercased (media types are lowercase);
        # ROS 2 package names already are (REP 144).
        package, _, type_name = entry.schema_name.partition("/msg/")
        return (
            entry.message_checksum,
            f"application/x.ros2-cdr.{package}.msg.{type_name.lower()}",
            entry.message_size,
        )
    raise ExpectationError(f"unknown payload extraction {extraction!r}")


def _payload(entry: Entry, extraction: str) -> dict[str, Any]:
    sha, media, size = _media_type_for(entry, extraction)
    return {"checksum": sha, "size_bytes": size, "media_type": media}


def _header_time(entry: Entry) -> int:
    if entry.stamp_ns is None:
        raise ExpectationError(f"{entry.topic}: message has no header stamp")
    return entry.stamp_ns


@dataclass
class SceneExpectation:
    boundary: dict[str, Any]
    unit_key: str
    window: dict[str, Any]
    clock_domains: list[dict[str, Any]]
    coordinate_frames: list[list[str]]
    channels: dict[str, dict[str, Any]]
    observations: dict[str, list[dict[str, Any]]]
    poses: list[dict[str, Any]]
    calibrations: dict[str, dict[str, Any]]
    producer: dict[str, Any]

    def summary(self) -> dict[str, Any]:
        """The committed, compact form: every sequence pinned by a digest."""
        return {
            "boundary": self.boundary,
            "unit_key": self.unit_key,
            "window": self.window,
            "clock_domains": self.clock_domains,
            "coordinate_frames": self.coordinate_frames,
            "channels": {
                topic: {
                    **spec,
                    "observation_count": len(self.observations[topic]),
                    "observations_digest": digest(
                        _observation_row(o) for o in self.observations[topic]
                    ),
                }
                for topic, spec in self.channels.items()
            },
            "calibrations": self.calibrations,
            "pose_count": len(self.poses),
            "poses_digest": digest(_pose_row(p) for p in self.poses),
            "observation_count": sum(len(v) for v in self.observations.values()),
            "group_count": 0,
            "producer": self.producer,
        }


def _observation_row(o: dict[str, Any]) -> list[Any]:
    p = o["payload"]
    return [
        o["timestamp_ns"],
        p["checksum"],
        p["size_bytes"],
        p["media_type"],
        o.get("image_size"),
    ]


def _pose_row(p: dict[str, Any]) -> list[Any]:
    t = p["transform"]
    return [
        p["source_clock"],
        p["timestamp_ns"],
        t["parent_frame_id"],
        t["child_frame_id"],
        t["translation_m"],
        t["rotation_wxyz"],
    ]


def _channel_time(spec: dict[str, Any]) -> tuple[str, Any]:
    time = spec["time"]
    source = time["source"]
    if source == "header_stamp":
        return time["clock"], _header_time
    if source == "log_time":
        return "mcap_log_time", lambda e: e.log_time
    if source == "publish_time":
        return "mcap_publish_time", lambda e: e.publish_time
    raise ExpectationError(f"unknown time source {source!r}")


def expected_scene(facts: RecordingFacts, config: dict[str, Any]) -> SceneExpectation:
    segmentation = config["segmentation"]
    if segmentation["policy"] != "whole_recording":
        raise ExpectationError(
            "the reference verifier implements whole_recording Scene segmentation only"
        )
    seg_clock = segmentation["clock"]
    frames = config["frames"]
    static = {}
    for topic in config.get("calibration", {}).get(
        "static_transform_topics", ["/tf_static"]
    ):
        for entry in facts.topic(topic):
            for t in entry.transforms:
                known = static.setdefault(t.child_frame_id, t)
                if (
                    known.parent_frame_id,
                    known.translation_m,
                    known.rotation_wxyz,
                ) != (
                    t.parent_frame_id,
                    t.translation_m,
                    t.rotation_wxyz,
                ):
                    raise ExpectationError(
                        f"static transform of {t.child_frame_id!r} changes"
                    )

    channels: dict[str, dict[str, Any]] = {}
    observations: dict[str, list[dict[str, Any]]] = {}
    calibrations: dict[str, dict[str, Any]] = {}
    in_window: list[int] = []
    for spec in sorted(config["channels"], key=lambda c: c["topic"]):
        topic = spec["topic"]
        entries = facts.topic(topic)
        if not entries:
            raise ExpectationError(
                f"configured topic {topic!r} is absent from the recording"
            )
        clock, time_of = _channel_time(spec)
        frame_ids = {e.frame_id for e in entries}
        if len(frame_ids) != 1 or not next(iter(frame_ids)):
            raise ExpectationError(f"{topic}: frame ids {sorted(map(str, frame_ids))}")
        frame_id = frame_ids.pop()
        image_size = None
        intrinsic = None
        info_topic = spec.get("camera_info_topic")
        if info_topic:
            infos = facts.topic(info_topic)
            shapes = {
                (i.camera_info.width, i.camera_info.height)
                for i in infos
                if i.camera_info
            }
            ks = {tuple(i.camera_info.k) for i in infos if i.camera_info}
            if len(shapes) != 1 or len(ks) != 1:
                raise ExpectationError(f"{info_topic}: intrinsics or image size change")
            image_size = list(shapes.pop())
            k = list(ks.pop())
            intrinsic = [k[0:3], k[3:6], k[6:9]]
        extrinsic = static.get(frame_id)
        if extrinsic is None or extrinsic.parent_frame_id != frames["ego_frame_id"]:
            raise ExpectationError(
                f"{topic}: no static transform {frames['ego_frame_id']} -> {frame_id}"
            )
        calibrations[topic] = {
            "extrinsic": {
                "parent_frame_id": extrinsic.parent_frame_id,
                "child_frame_id": frame_id,
                "translation_m": extrinsic.translation_m,
                "rotation_wxyz": extrinsic.rotation_wxyz,
            },
            "camera_intrinsic": intrinsic,
        }
        channels[topic] = {
            "modality": spec["modality"],
            "sensor_id": spec.get("sensor_id"),
            "frame_id": frame_id,
            "source_clock": clock,
        }
        rows = []
        for entry in _canonical_order(entries, time_of):
            rows.append(
                {
                    "timestamp_ns": time_of(entry),
                    "payload": _payload(entry, spec["payload"]),
                    "image_size": image_size,
                    "channel_index": entry.channel_index,
                }
            )
        observations[topic] = rows
        if clock == seg_clock:
            in_window += [r["timestamp_ns"] for r in rows]

    poses = []
    for spec in config.get("poses", []):
        clock, _ = _channel_time(spec)
        for entry in facts.topic(spec["topic"]):
            for t in entry.transforms:
                if (t.parent_frame_id, t.child_frame_id) != (
                    spec["parent_frame_id"],
                    spec["child_frame_id"],
                ):
                    continue
                stamp = (
                    t.stamp_ns
                    if spec["time"]["source"] == "header_stamp"
                    else _channel_time(spec)[1](entry)
                )
                poses.append(
                    {
                        "timestamp_ns": stamp,
                        "source_clock": clock,
                        "transform": {
                            "parent_frame_id": t.parent_frame_id,
                            "child_frame_id": t.child_frame_id,
                            "translation_m": t.translation_m,
                            "rotation_wxyz": t.rotation_wxyz,
                        },
                    }
                )
                if clock == seg_clock:
                    in_window.append(stamp)
    poses.sort(
        key=lambda p: (
            p["source_clock"],
            p["timestamp_ns"],
            p["transform"]["translation_m"],
            p["transform"]["rotation_wxyz"],
        )
    )
    if not in_window:
        raise ExpectationError("nothing is placed on the segmentation clock")

    roles = {frames["ego_frame_id"]: "ego"}
    if frames.get("world_frame_id"):
        roles[frames["world_frame_id"]] = "world"
    for spec in channels.values():
        roles.setdefault(spec["frame_id"], "sensor")
    boundary = dict(segmentation)
    return SceneExpectation(
        boundary=boundary,
        unit_key=recording_unit_key(boundary=boundary, segment="recording"),
        window={
            "source_clock": seg_clock,
            "start_timestamp_ns": min(in_window),
            "end_timestamp_ns": max(in_window) + 1,
        },
        clock_domains=recording_clock_domains(
            {seg_clock}
            | {c["source_clock"] for c in channels.values()}
            | {p["source_clock"] for p in poses}
        ),
        coordinate_frames=sorted([frame, role] for frame, role in roles.items()),
        channels=channels,
        observations=observations,
        poses=poses,
        calibrations=calibrations,
        producer={
            "producer_id": "sceneops.recording_scene_builder",
            "semantics_version": 4,
        },
    )


# ── Episodes ─────────────────────────────────────────────────────────────────


def _value_at(entry: Entry, decoding: str, path: str) -> Any:
    if decoding == "json_string":
        node: Any = entry.json
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                raise ExpectationError(f"{entry.topic}: no field {path!r}")
            node = node[part]
        return node
    if path not in entry.values:
        raise ExpectationError(f"{entry.topic}: the verifier decodes no field {path!r}")
    return entry.values[path]


def _episode_time(entry: Entry, source: dict[str, Any], decoding: str) -> int:
    policy = source["time"]["source"]
    if policy == "header_stamp":
        return _header_time(entry)
    if policy == "payload_field":
        value = _value_at(entry, decoding, source["time"]["field"])
        if not isinstance(value, int) or isinstance(value, bool):
            raise ExpectationError(f"{entry.topic}: payload time is not an integer")
        return value
    if policy == "log_time":
        return entry.log_time
    if policy == "publish_time":
        return entry.publish_time
    raise ExpectationError(f"unknown time source {policy!r}")


def _clock_of(source: dict[str, Any]) -> str:
    policy = source["time"]["source"]
    return {"log_time": "mcap_log_time", "publish_time": "mcap_publish_time"}.get(
        policy, source["time"]["clock"]
    )


def _episode_boundary(config: dict[str, Any]) -> dict[str, Any]:
    segmentation = config["segmentation"]
    if segmentation["policy"] != "event_markers":
        return dict(segmentation)
    source = next(
        e for e in config.get("events", []) if e["topic"] == segmentation["event_topic"]
    )
    paths = {f["name"]: f["path"] for f in source["fields"]}
    time = source["time"]
    return {
        "policy": "event_markers",
        "event_topic": segmentation["event_topic"],
        "time": {
            "source": time["source"],
            "clock": time["clock"],
            "field": time.get("field"),
        },
        "decoding": source.get("decoding", "ros2"),
        "key_path": paths[segmentation["key_field"]],
        "state_path": paths[segmentation["state_field"]],
        "start_values": sorted(set(segmentation["start_values"])),
        "end_values": sorted(set(segmentation["end_values"])),
    }


@dataclass
class EpisodeUnitExpectation:
    segment: str
    unit_key: str
    window: dict[str, Any]
    clock_domains: list[dict[str, Any]]
    streams: dict[str, dict[str, Any]]
    occurrences: dict[str, dict[str, list[dict[str, Any]]]] = field(
        default_factory=dict
    )

    def summary(self) -> dict[str, Any]:
        return {
            "segment": self.segment,
            "unit_key": self.unit_key,
            "window": self.window,
            "clock_domains": self.clock_domains,
            "streams": self.streams,
            "occurrences": {
                role: {
                    topic: {
                        "count": len(rows),
                        "digest": digest(_occurrence_row(r) for r in rows),
                    }
                    for topic, rows in sorted(by_topic.items())
                }
                for role, by_topic in sorted(self.occurrences.items())
            },
        }


def _occurrence_row(r: dict[str, Any]) -> list[Any]:
    p = r.get("payload")
    return [
        r["timestamp_ns"],
        r["values"],
        None if p is None else [p["checksum"], p["size_bytes"], p["media_type"]],
    ]


@dataclass
class EpisodeExpectation:
    boundary: dict[str, Any]
    units: list[EpisodeUnitExpectation]

    def summary(self) -> dict[str, Any]:
        return {"boundary": self.boundary, "units": [u.summary() for u in self.units]}


_ROLE_LIST = {
    "observation": "observations",
    "state": "states",
    "action": "actions",
    "event": "events",
}


def expected_episodes(
    facts: RecordingFacts, config: dict[str, Any]
) -> EpisodeExpectation:
    segmentation = config["segmentation"]
    if segmentation["policy"] != "event_markers":
        raise ExpectationError(
            "the reference verifier implements event_markers Episode segmentation only"
        )
    boundary = _episode_boundary(config)
    marker_source = next(
        e for e in config["events"] if e["topic"] == segmentation["event_topic"]
    )
    marker_decoding = marker_source.get("decoding", "ros2")
    seg_clock = _clock_of(marker_source)
    paths = {f["name"]: f["path"] for f in marker_source["fields"]}
    markers = _canonical_order(
        facts.topic(segmentation["event_topic"]),
        lambda e: _episode_time(e, marker_source, marker_decoding),
    )
    open_starts: dict[str, int] = {}
    seen: dict[str, int] = {}
    windows: list[tuple[str, int, int]] = []
    for marker in markers:
        t = _episode_time(marker, marker_source, marker_decoding)
        key = _value_at(marker, marker_decoding, paths[segmentation["key_field"]])
        state = _value_at(marker, marker_decoding, paths[segmentation["state_field"]])
        if state in segmentation["start_values"]:
            if key in open_starts:
                raise ExpectationError(f"task {key!r} starts again before it ended")
            open_starts[key] = t
        elif state in segmentation["end_values"]:
            if key not in open_starts:
                raise ExpectationError(f"task {key!r} ends without having started")
            n = seen.get(key, 0)
            seen[key] = n + 1
            windows.append((f"task-{key}-{n:03d}", open_starts.pop(key), t + 1))
    if open_starts:
        raise ExpectationError(f"tasks {sorted(open_starts)} never end")
    if not windows:
        raise ExpectationError("the recording yields no Episode")

    sources = [(s, s["role"]) for s in config["streams"]] + [
        (e, "event") for e in config.get("events", [])
    ]
    units = []
    for segment, start, end in sorted(windows, key=lambda w: (w[1], w[0])):
        streams: dict[str, dict[str, Any]] = {}
        occurrences: dict[str, dict[str, list[dict[str, Any]]]] = {}
        for source, role in sorted(sources, key=lambda s: s[0]["topic"]):
            topic = source["topic"]
            decoding = source.get("decoding", "ros2")
            entries = facts.topic(topic)
            if not entries:
                raise ExpectationError(
                    f"configured topic {topic!r} is absent from the recording"
                )
            clock = _clock_of(source)
            fields = sorted(source.get("fields", []), key=lambda f: f["name"])
            payload = source.get("payload")
            streams[topic] = {
                "role": role,
                "schema_name": facts.schema_name(topic),
                "source_clock": clock,
                "fields": [[f["name"], f["path"]] for f in fields],
                "has_payload": payload is not None,
            }
            if clock != seg_clock and clock not in (
                "mcap_log_time",
                "mcap_publish_time",
            ):
                raise ExpectationError(
                    f"{topic} is not placeable on the segmentation clock {seg_clock!r}"
                )
            rows = []
            for entry in _canonical_order(
                entries, lambda e: _episode_time(e, source, decoding)
            ):
                t = _episode_time(entry, source, decoding)
                if not start <= t < end:
                    continue
                rows.append(
                    {
                        "timestamp_ns": t,
                        "values": {
                            f["name"]: _value_at(entry, decoding, f["path"])
                            for f in fields
                        },
                        "payload": None
                        if payload is None
                        else _payload(entry, payload),
                        "channel_index": entry.channel_index,
                    }
                )
            occurrences.setdefault(_ROLE_LIST[role], {})[topic] = rows
        units.append(
            EpisodeUnitExpectation(
                segment=segment,
                unit_key=recording_unit_key(boundary=boundary, segment=segment),
                window={
                    "source_clock": seg_clock,
                    "start_timestamp_ns": start,
                    "end_timestamp_ns": end,
                },
                clock_domains=recording_clock_domains(
                    {seg_clock} | {s["source_clock"] for s in streams.values()}
                ),
                streams=streams,
                occurrences=occurrences,
            )
        )
    return EpisodeExpectation(boundary=boundary, units=units)
