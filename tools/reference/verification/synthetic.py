"""A synthetic observation: the platform state the documented rules describe,
written from the verifier's own expectations.

A test double, never evidence. It exists so the checks and the negative
controls can run without a platform: a correct synthetic state must pass, and
every corrupted copy of it must fail. Whether the *platform* produces this
state is decided only by verifying a collected observation
(``reference_verify.py verify``).
"""

from __future__ import annotations

from typing import Any

from . import rules
from .expectations import EpisodeExpectation, SceneExpectation
from .observation import empty_observation


def _payload_id(
    robot_run_id: str, topic: str, channel_index: int, media_type: str
) -> str:
    doc = {
        "robot_run_id": robot_run_id,
        "topic": topic,
        "channel_index": channel_index,
        "media_type": media_type,
    }
    return "payload-" + rules.sha256_hex(rules.canonical_json(doc))[:32]


def _slug(topic: str) -> str:
    return topic.strip("/").replace("/", ".")


def _ref(robot_run_id: str, topic: str, row: dict[str, Any]) -> dict[str, Any]:
    p = row["payload"]
    return {
        "kind": "artifact",
        "artifact_id": _payload_id(
            robot_run_id, topic, row["channel_index"], p["media_type"]
        ),
        "checksum": p["checksum"],
        "size_bytes": p["size_bytes"],
        "media_type": p["media_type"],
    }


def _producer(
    producer_id: str,
    semantics_version: int,
    build_config: dict[str, Any],
    robot_run_id: str,
    recording_checksum: str,
) -> dict[str, Any]:
    return {
        "producer_id": producer_id,
        "semantics_version": semantics_version,
        "build_config": build_config,
        "producer_fingerprint": rules.producer_fingerprint(
            producer_id=producer_id,
            semantics_version=semantics_version,
            build_config=build_config,
            source=rules.recording_source_revision(
                robot_run_id=robot_run_id, recording_checksum=recording_checksum
            ),
        ),
    }


def _lineage(
    robot_run_id: str,
    recording_checksum: str,
    unit_key: str,
    window: dict[str, Any],
    producer: dict[str, Any],
) -> dict[str, Any]:
    return {
        "source": {
            "source_kind": "recording",
            "robot_run_id": robot_run_id,
            "recording_artifact_id": rules.recording_artifact_id(robot_run_id),
            "recording_checksum": recording_checksum,
            **window,
            "unit_key": unit_key,
        },
        "producer": producer,
    }


def scene_manifest(
    exp: SceneExpectation,
    robot_run_id: str,
    recording_checksum: str,
    build_config: dict[str, Any],
) -> dict[str, Any]:
    cal_ids = {topic: f"cal-{_slug(topic)}" for topic in exp.calibrations}
    observations = []
    for topic, rows in exp.observations.items():
        for rank, row in enumerate(rows):
            size = row["image_size"]
            observations.append(
                {
                    "observation_id": f"{_slug(topic)}-{rank:08d}",
                    "channel": topic,
                    "timestamp_ns": row["timestamp_ns"],
                    "payload": _ref(robot_run_id, topic, row),
                    "calibration_id": cal_ids[topic],
                    "ego_pose_id": None,
                    "image_size": None
                    if size is None
                    else {"width_px": size[0], "height_px": size[1]},
                }
            )
    observations.sort(
        key=lambda o: (o["timestamp_ns"], o["channel"], o["observation_id"])
    )
    return {
        "schema_version": "sceneops.scene_manifest/v3",
        "lineage": _lineage(
            robot_run_id,
            recording_checksum,
            exp.unit_key,
            exp.window,
            _producer(
                exp.producer["producer_id"],
                exp.producer["semantics_version"],
                build_config,
                robot_run_id,
                recording_checksum,
            ),
        ),
        "clock_domains": exp.clock_domains,
        "coordinate_frames": [
            {"frame_id": f, "role": r} for f, r in exp.coordinate_frames
        ],
        "channels": [{"channel": t, **spec} for t, spec in exp.channels.items()],
        "calibrations": [
            {"calibration_id": cal_ids[t], "channel": t, **spec}
            for t, spec in exp.calibrations.items()
        ],
        "observations": observations,
        "poses": [{"pose_id": f"pose-{i:08d}", **p} for i, p in enumerate(exp.poses)],
        "groups": [],
    }


def episode_manifest(
    exp: Any, robot_run_id: str, recording_checksum: str, build_config: dict[str, Any]
) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "schema_version": "sceneops.episode_manifest/v3",
        "lineage": _lineage(
            robot_run_id,
            recording_checksum,
            exp.unit_key,
            exp.window,
            _producer(
                "sceneops.recording_episode_builder",
                4,
                build_config,
                robot_run_id,
                recording_checksum,
            ),
        ),
        "clock_domains": exp.clock_domains,
        "frame_timeline": None,
        "streams": [
            {
                "topic": t,
                "role": s["role"],
                "schema_name": s["schema_name"],
                "source_clock": s["source_clock"],
                "fields": [
                    {"name": n, "path": p, "component_names": None}
                    for n, p in s["fields"]
                ],
                "has_payload": s["has_payload"],
            }
            for t, s in exp.streams.items()
        ],
        "transitions": [],
    }
    for role in ("observations", "states", "actions", "events"):
        rows = []
        for topic, occurrences in sorted(exp.occurrences.get(role, {}).items()):
            for rank, row in enumerate(occurrences):
                rows.append(
                    {
                        "occurrence_id": f"{_slug(topic)}-{rank:08d}",
                        "topic": topic,
                        "timestamp_ns": row["timestamp_ns"],
                        "values": row["values"],
                        "payload": None
                        if row["payload"] is None
                        else _ref(robot_run_id, topic, row),
                        "native_ref": None,
                    }
                )
        manifest[role] = rows
    return manifest


def _register(
    obs: dict[str, Any],
    domain: str,
    unit_id: str,
    robot_run_id: str,
    unit_key: str,
    manifest: dict[str, Any],
    projection: dict[str, Any],
) -> dict[str, Any]:
    data = rules.canonical_json(manifest)
    checksum = rules.checksum(data)
    artifact_id = rules.manifest_artifact_id(
        unit_id=unit_id, manifest_checksum=checksum
    )
    obs["manifests"][artifact_id] = {
        "artifact": {
            "artifact_id": artifact_id,
            "kind": f"{domain}_manifest",
            "checksum": checksum,
            "size_bytes": len(data),
            "uri": f"s3://synthetic/{domain}s/{unit_id}/manifest-{checksum[7:]}.json",
        },
        "bytes": data.decode("utf-8"),
    }
    source = manifest["lineage"]["source"]
    revision = {
        "unit_id": unit_id,
        "source_kind": "recording",
        "robot_run_id": robot_run_id,
        "unit_key": unit_key,
        "manifest_artifact_id": artifact_id,
        "manifest_checksum": checksum,
        "producer_fingerprint": manifest["lineage"]["producer"]["producer_fingerprint"],
        "window_clock": source["source_clock"],
        "window_start_timestamp_ns": source["start_timestamp_ns"],
        "window_end_timestamp_ns": source["end_timestamp_ns"],
        **projection,
    }
    obs["units"][domain][unit_id] = {
        "record": {
            f"{domain}_id": unit_id,
            "source_kind": "recording",
            "robot_run_id": robot_run_id,
            "unit_key": unit_key,
        },
        "revisions": [revision],
    }
    for role in ("observations", "states", "actions", "events"):
        for row in manifest.get(role, []):
            ref = row.get("payload")
            if ref is not None:
                obs["payloads"][ref["artifact_id"]] = {
                    "artifact": {
                        **{
                            k: ref[k]
                            for k in (
                                "artifact_id",
                                "checksum",
                                "size_bytes",
                                "media_type",
                            )
                        },
                        "kind": "observation_payload",
                        "owner_type": "robot_run",
                        "owner_id": robot_run_id,
                    },
                    "object": {
                        "size_bytes": ref["size_bytes"],
                        "checksum": ref["checksum"],
                    },
                }
    return {
        "domain": domain,
        "unit_id": unit_id,
        "manifest_artifact_id": artifact_id,
        "manifest_checksum": checksum,
    }


def add_robot_run(
    obs: dict[str, Any],
    *,
    robot_run_id: str,
    robot_id: str,
    recording: dict[str, Any],
    scene: SceneExpectation,
    episodes: EpisodeExpectation,
    scene_config: dict[str, Any],
    episode_config: dict[str, Any],
) -> list[dict[str, Any]]:
    """One registered RobotRun with its Scene and Episode revisions; returns the pins."""
    checksum = recording["sha256"]
    obs["robot_runs"][robot_run_id] = {
        "record": {
            "run_id": robot_run_id,
            "robot_id": robot_id,
            "recording_artifact_id": rules.recording_artifact_id(robot_run_id),
        },
        "recording_artifact": {
            "artifact_id": rules.recording_artifact_id(robot_run_id),
            "kind": "robot_run_recording",
            "checksum": checksum,
            "size_bytes": recording["size_bytes"],
        },
        "recording_object": {
            "size_bytes": recording["size_bytes"],
            "checksum": checksum,
        },
    }
    pins = []
    scene_id = rules.unit_id(
        domain="scene",
        source_kind="recording",
        source_id=robot_run_id,
        unit_key=scene.unit_key,
    )
    pins.append(
        _register(
            obs,
            "scene",
            scene_id,
            robot_run_id,
            scene.unit_key,
            scene_manifest(scene, robot_run_id, checksum, scene_config),
            {
                "observed_channels": sorted(
                    t for t, r in scene.observations.items() if r
                ),
                "observation_count": sum(len(r) for r in scene.observations.values()),
                "keyframe_count": 0,
            },
        )
    )
    for unit in episodes.units:
        episode_id = rules.unit_id(
            domain="episode",
            source_kind="recording",
            source_id=robot_run_id,
            unit_key=unit.unit_key,
        )
        projection: dict[str, Any] = {}
        for role in ("observations", "states", "actions", "events"):
            by_topic = unit.occurrences.get(role, {})
            projection[f"{role[:-1]}_topics"] = sorted(
                t for t, r in by_topic.items() if r
            )
            projection[f"{role[:-1]}_count"] = sum(len(r) for r in by_topic.values())
        pins.append(
            _register(
                obs,
                "episode",
                episode_id,
                robot_run_id,
                unit.unit_key,
                episode_manifest(unit, robot_run_id, checksum, episode_config),
                projection,
            )
        )
    return pins


def add_dataset_version(
    obs: dict[str, Any], key: str, pins: list[dict[str, Any]], summary: dict[str, Any]
) -> None:
    obs["dataset_versions"][key] = {
        "version": {
            "dataset_id": key.split("/")[0],
            "version": key.split("/")[1],
            "composition_checksum": rules.composition_checksum(
                (p["domain"], p["unit_id"], p["manifest_checksum"]) for p in pins
            ),
            "scene": {
                k: summary[k]
                for k in (
                    "scene_count",
                    "observation_count",
                    "keyframe_count",
                    "observed_channels",
                )
            },
            "episode": {"episode_count": summary["episode_count"]},
        },
        "members": [dict(p) for p in pins],
    }


def empty() -> dict[str, Any]:
    return empty_observation()
