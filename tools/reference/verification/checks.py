"""Observed platform state against the independent expectations.

The observation (``sceneops.reference_observation/1``, observation.py) is what
the platform holds: records, manifest bytes, payload and recording objects.
Every identity, checksum and composition is recomputed here from bytes and
pins by the documented formulas (rules), and every manifest's content is
compared with what the recording and the build configuration determine
(expectations). Nothing from the observation is trusted because another
part of the observation agrees with it.
"""

from __future__ import annotations

import json
from typing import Any

from . import rules
from .expectations import EpisodeExpectation, SceneExpectation
from .findings import Findings

SCENE_MANIFEST_SCHEMA = "sceneops.scene_manifest/v2"
EPISODE_MANIFEST_SCHEMA = "sceneops.episode_manifest/v1"
MANIFEST_KIND = {"scene": "scene_manifest", "episode": "episode_manifest"}

# MS-2 / scene-domain.md §1 "No membership", "No location": no manifest names a
# DatasetVersion, a unit id, a status, a location or an execution context.
FORBIDDEN_MANIFEST_KEYS = frozenset(
    {
        "dataset_id",
        "dataset_version",
        "scene_id",
        "episode_id",
        "status",
        "uri",
        "url",
        "job_id",
        "pipeline_run_id",
        "snapshot_id",
        "created_at",
        "registered_at",
    }
)
_ROLES = ("observations", "states", "actions", "events")


def _forbidden_keys(node: Any, where: str = "") -> list[str]:
    found = []
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{where}.{key}" if where else key
            # build_config is the producer's own configuration (field paths are
            # configuration, not locations)
            if key in FORBIDDEN_MANIFEST_KEYS and not where.endswith("build_config"):
                found.append(here)
            if key != "build_config":
                found += _forbidden_keys(value, here)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            found += _forbidden_keys(value, f"{where}[{i}]")
    return found


def subsumes(actual: Any, expected: Any) -> bool:
    """Every member the configuration file states appears in the normalized
    build_config with the same value; lists match element for element in any
    order (normalization sorts them)."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            k in actual and subsumes(actual[k], v) for k, v in expected.items()
        )
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            return False
        remaining = list(actual)
        for item in expected:
            match = next(
                (i for i, a in enumerate(remaining) if subsumes(a, item)), None
            )
            if match is None:
                return False
            remaining.pop(match)
        return True
    return actual == expected and type(actual) is type(expected)


# ── Manifests ────────────────────────────────────────────────────────────────


def load_manifest(
    f: Findings, obs: dict[str, Any], domain: str, revision: dict[str, Any]
) -> dict[str, Any] | None:
    """The revision's manifest: record, read-back bytes, canonical form."""
    artifact_id = revision["manifest_artifact_id"]
    entry = obs["manifests"].get(artifact_id)
    if not f.check(
        entry is not None,
        "manifest_missing",
        f"manifest {artifact_id} was not observed",
    ):
        return None
    record, text = entry["artifact"], entry.get("bytes")
    f.equal(
        record.get("kind"),
        MANIFEST_KIND[domain],
        "manifest_record",
        "manifest artifact kind",
    )
    if not f.check(
        text is not None,
        "manifest_missing",
        f"manifest {artifact_id} bytes are not in the store",
    ):
        return None
    data = text.encode("utf-8")
    actual = rules.checksum(data)
    f.equal(
        actual,
        revision["manifest_checksum"],
        "manifest_checksum",
        "sha256 of the stored manifest bytes vs the revision",
    )
    f.equal(
        record.get("checksum"),
        actual,
        "manifest_record",
        "manifest ArtifactRecord checksum vs the bytes",
    )
    f.equal(
        record.get("size_bytes"),
        len(data),
        "manifest_record",
        "manifest ArtifactRecord size vs the bytes",
    )
    f.equal(
        rules.manifest_artifact_id(
            unit_id=revision["unit_id"], manifest_checksum=revision["manifest_checksum"]
        ),
        artifact_id,
        "pin_artifact_id",
        "manifest_artifact_id vs f(unit_id, manifest_checksum)",
    )
    try:
        manifest = json.loads(data)
    except json.JSONDecodeError as exc:
        f.fail("manifest_canonical", f"manifest {artifact_id} is not JSON: {exc}")
        return None
    f.check(
        rules.canonical_json(manifest) == data,
        "manifest_canonical",
        f"manifest {artifact_id} bytes are not canonical JSON",
    )
    bad = _forbidden_keys(manifest)
    f.check(
        not bad, "manifest_forbidden_field", f"manifest {artifact_id} carries {bad[:5]}"
    )
    return manifest


def check_lineage(
    f: Findings,
    manifest: dict[str, Any],
    *,
    robot_run_id: str,
    recording_checksum: str,
    unit_key: str,
    window: dict[str, Any],
    revision: dict[str, Any],
    build_config_file: dict[str, Any],
    producer: dict[str, Any] | None,
) -> None:
    source = manifest.get("lineage", {}).get("source", {})
    f.equal(
        {
            k: source.get(k)
            for k in (
                "source_kind",
                "robot_run_id",
                "recording_artifact_id",
                "recording_checksum",
            )
        },
        {
            "source_kind": "recording",
            "robot_run_id": robot_run_id,
            "recording_artifact_id": rules.recording_artifact_id(robot_run_id),
            "recording_checksum": recording_checksum,
        },
        "source_binding",
        "lineage.source binding",
    )
    f.equal(source.get("unit_key"), unit_key, "unit_key", "lineage.source.unit_key")
    f.equal(
        {
            k: source.get(k)
            for k in ("source_clock", "start_timestamp_ns", "end_timestamp_ns")
        },
        window,
        "window",
        "lineage.source window",
    )
    f.equal(
        {
            "source_clock": revision.get("window_clock"),
            "start_timestamp_ns": revision.get("window_start_timestamp_ns"),
            "end_timestamp_ns": revision.get("window_end_timestamp_ns"),
        },
        window,
        "revision_projection",
        "revision window columns",
    )
    prod = manifest.get("lineage", {}).get("producer", {})
    recomputed = rules.producer_fingerprint(
        producer_id=prod.get("producer_id"),
        semantics_version=prod.get("semantics_version"),
        build_config=prod.get("build_config", {}),
        source=rules.recording_source_revision(
            robot_run_id=robot_run_id, recording_checksum=recording_checksum
        ),
    )
    f.equal(
        prod.get("producer_fingerprint"),
        recomputed,
        "producer_fingerprint",
        "fingerprint vs f(producer, build_config, source revision)",
    )
    f.equal(
        revision.get("producer_fingerprint"),
        recomputed,
        "producer_fingerprint",
        "revision producer_fingerprint",
    )
    f.check(
        subsumes(prod.get("build_config"), build_config_file),
        "producer_build_config",
        "the normalized build_config does not carry the reference build configuration",
    )
    if producer is not None:
        f.equal(
            {k: prod.get(k) for k in producer},
            producer,
            "producer_identity",
            "producer id / semantics version",
        )


def _payload_row(ref: dict[str, Any] | None) -> list[Any] | None:
    if ref is None:
        return None
    return [ref.get("checksum"), ref.get("size_bytes"), ref.get("media_type")]


def check_scene_manifest(
    f: Findings,
    manifest: dict[str, Any],
    exp: SceneExpectation,
    revision: dict[str, Any],
) -> list[dict[str, Any]]:
    """Content against the expectation; returns the manifest's payload refs
    with their topic and channel position."""
    f.equal(
        manifest.get("schema_version"),
        SCENE_MANIFEST_SCHEMA,
        "manifest_schema",
        "schema_version",
    )
    f.equal(
        sorted(
            [fr.get("frame_id"), fr.get("role")]
            for fr in manifest.get("coordinate_frames", [])
        ),
        exp.coordinate_frames,
        "scene_frames",
        "coordinate frames",
    )
    channels = {c.get("channel"): c for c in manifest.get("channels", [])}
    f.equal(sorted(channels), sorted(exp.channels), "scene_channels", "channels")
    for topic, spec in exp.channels.items():
        got = channels.get(topic, {})
        f.equal(
            {k: got.get(k) for k in spec}, spec, "scene_channels", f"channel {topic}"
        )
    cals = {c.get("calibration_id"): c for c in manifest.get("calibrations", [])}
    cal_by_channel = {c.get("channel"): c for c in cals.values()}
    f.equal(
        len(cal_by_channel),
        len(cals),
        "scene_calibration",
        "one calibration per channel",
    )
    for topic, spec in exp.calibrations.items():
        got = cal_by_channel.get(topic)
        if f.check(got is not None, "scene_calibration", f"no calibration for {topic}"):
            f.equal(
                {
                    "extrinsic": got.get("extrinsic"),
                    "camera_intrinsic": got.get("camera_intrinsic"),
                },
                spec,
                "scene_calibration",
                f"calibration of {topic}",
            )

    observations = manifest.get("observations", [])
    ids = [o.get("observation_id") for o in observations]
    f.equal(
        len(set(ids)), len(ids), "scene_observation_ids", "observation ids are unique"
    )
    refs = []
    for topic, expected_rows in exp.observations.items():
        got = [o for o in observations if o.get("channel") == topic]
        observed = [
            [
                o.get("timestamp_ns"),
                *(_payload_row(o.get("payload")) or [None] * 3),
                _image_size(o),
            ]
            for o in got
        ]
        expected = [
            [
                r["timestamp_ns"],
                r["payload"]["checksum"],
                r["payload"]["size_bytes"],
                r["payload"]["media_type"],
                r["image_size"],
            ]
            for r in expected_rows
        ]
        if not f.equal(
            len(observed),
            len(expected),
            "scene_observations",
            f"{topic} observation count",
        ):
            continue
        diff = next(
            (i for i, (a, b) in enumerate(zip(observed, expected)) if a != b), None
        )
        f.check(
            diff is None,
            "scene_observations",
            f"{topic} observation {diff} (canonical order): observed {observed[diff] if diff is not None else None}, expected {expected[diff] if diff is not None else None}",
        )
        cal = cal_by_channel.get(topic, {}).get("calibration_id")
        f.check(
            all(o.get("calibration_id") == cal for o in got),
            "scene_calibration",
            f"{topic} observations do not reference their channel's calibration",
        )
        f.check(
            all(o.get("ego_pose_id") is None for o in got),
            "scene_observations",
            f"{topic}: an observation names an ego pose the recording does not associate",
        )
        for o, r in zip(got, expected_rows):
            refs.append(
                {
                    "topic": topic,
                    "channel_index": r["channel_index"],
                    "ref": o.get("payload"),
                }
            )
    unknown = {o.get("channel") for o in observations} - set(exp.observations)
    f.check(
        not unknown,
        "scene_observations",
        f"observations on unconfigured channels {sorted(unknown)}",
    )

    poses = sorted(
        [
            [
                p.get("source_clock"),
                p.get("timestamp_ns"),
                p.get("transform", {}).get("parent_frame_id"),
                p.get("transform", {}).get("child_frame_id"),
                p.get("transform", {}).get("translation_m"),
                p.get("transform", {}).get("rotation_wxyz"),
            ]
            for p in manifest.get("poses", [])
        ],
        key=lambda r: (r[0], r[1], r[4], r[5]),
    )
    expected_poses = [
        [
            p["source_clock"],
            p["timestamp_ns"],
            p["transform"]["parent_frame_id"],
            p["transform"]["child_frame_id"],
            p["transform"]["translation_m"],
            p["transform"]["rotation_wxyz"],
        ]
        for p in exp.poses
    ]
    if f.equal(len(poses), len(expected_poses), "scene_poses", "pose count"):
        f.check(
            poses == expected_poses,
            "scene_poses",
            "poses differ from the recording's transforms",
        )
    f.equal(
        len(manifest.get("groups", [])),
        0,
        "scene_groups",
        "keyframe groups (a recording carries none)",
    )

    window = exp.window
    on_clock = [
        o.get("timestamp_ns")
        for o in observations
        if channels.get(o.get("channel"), {}).get("source_clock")
        == window["source_clock"]
    ]
    on_clock += [
        p.get("timestamp_ns")
        for p in manifest.get("poses", [])
        if p.get("source_clock") == window["source_clock"]
    ]
    f.check(
        all(
            isinstance(t, int)
            and window["start_timestamp_ns"] <= t < window["end_timestamp_ns"]
            for t in on_clock
        ),
        "window",
        "a timestamp on the segmentation clock lies outside [start, end)",
    )
    f.equal(
        {
            "observed_channels": sorted(revision.get("observed_channels", [])),
            "observation_count": revision.get("observation_count"),
            "keyframe_count": revision.get("keyframe_count"),
        },
        {
            "observed_channels": sorted(
                t for t, rows in exp.observations.items() if rows
            ),
            "observation_count": sum(len(rows) for rows in exp.observations.values()),
            "keyframe_count": 0,
        },
        "revision_projection",
        "revision projection columns",
    )
    return refs


def _image_size(o: dict[str, Any]) -> list[int] | None:
    size = o.get("image_size")
    return None if size is None else [size.get("width_px"), size.get("height_px")]


def check_episode_manifest(
    f: Findings, manifest: dict[str, Any], exp_unit: Any, revision: dict[str, Any]
) -> list[dict[str, Any]]:
    f.equal(
        manifest.get("schema_version"),
        EPISODE_MANIFEST_SCHEMA,
        "manifest_schema",
        "schema_version",
    )
    streams = {
        s.get("topic"): {
            "role": s.get("role"),
            "schema_name": s.get("schema_name"),
            "source_clock": s.get("source_clock"),
            "fields": sorted(
                [fd.get("name"), fd.get("path")] for fd in s.get("fields", [])
            ),
            "has_payload": s.get("has_payload"),
        }
        for s in manifest.get("streams", [])
    }
    f.equal(streams, exp_unit.streams, "episode_streams", "streams")
    refs = []
    all_ids = []
    for role in _ROLES:
        rows = manifest.get(role, [])
        all_ids += [r.get("occurrence_id") for r in rows]
        expected_by_topic = exp_unit.occurrences.get(role, {})
        for topic in sorted(set(expected_by_topic) | {r.get("topic") for r in rows}):
            got = [r for r in rows if r.get("topic") == topic]
            expected_rows = expected_by_topic.get(topic, [])
            observed = [
                [
                    r.get("timestamp_ns"),
                    r.get("values", {}),
                    _payload_row(r.get("payload")),
                ]
                for r in got
            ]
            expected = [
                [r["timestamp_ns"], r["values"], _payload_row(r["payload"])]
                for r in expected_rows
            ]
            if not f.equal(
                len(observed),
                len(expected),
                "episode_occurrences",
                f"{role} {topic} count",
            ):
                continue
            diff = next(
                (
                    i
                    for i, (a, b) in enumerate(zip(observed, expected))
                    if not _same(a, b)
                ),
                None,
            )
            f.check(
                diff is None,
                "episode_occurrences",
                f"{role} {topic} occurrence {diff}: observed {observed[diff] if diff is not None else None}, expected {expected[diff] if diff is not None else None}",
            )
            for r, e in zip(got, expected_rows):
                if r.get("payload") is not None:
                    refs.append(
                        {
                            "topic": topic,
                            "channel_index": e["channel_index"],
                            "ref": r.get("payload"),
                        }
                    )
    f.equal(
        len(set(all_ids)),
        len(all_ids),
        "episode_occurrence_ids",
        "occurrence ids are unique",
    )
    projection = {
        f"{role[:-1]}_topics": sorted(
            t for t, rows in exp_unit.occurrences.get(role, {}).items() if rows
        )
        for role in _ROLES
    }
    projection.update(
        {
            f"{role[:-1]}_count": sum(
                len(rows) for rows in exp_unit.occurrences.get(role, {}).values()
            )
            for role in _ROLES
        }
    )
    f.equal(
        {
            k: sorted(revision.get(k, [])) if k.endswith("_topics") else revision.get(k)
            for k in projection
        },
        projection,
        "revision_projection",
        "revision projection columns",
    )
    return refs


def _same(a: Any, b: Any) -> bool:
    """Equal values of equal JSON type: an int is not a float (episode-domain.md §4:
    values are kept as decoded)."""
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    return type(a) is type(b) and a == b


# ── Payloads ─────────────────────────────────────────────────────────────────


def check_payloads(
    f: Findings,
    obs: dict[str, Any],
    robot_run_id: str,
    refs: list[dict[str, Any]],
    *,
    deep: bool,
) -> None:
    """Every payload reference resolves to an OBSERVATION_PAYLOAD record with exactly
    its integrity metadata, owned by the RobotRun, whose object is present with
    that size (and, deep, those bytes). One recorded message and extraction is one
    payload artifact, shared by every unit that references it."""
    by_message: dict[tuple[str, int], set[str]] = {}
    by_id: dict[str, set[tuple[str, int]]] = {}
    for item in refs:
        ref = item["ref"] or {}
        key = (item["topic"], item["channel_index"])
        by_message.setdefault(key, set()).add(ref.get("artifact_id"))
        by_id.setdefault(ref.get("artifact_id"), set()).add(key)
    for artifact_id, messages in by_id.items():
        f.equal(
            len(messages),
            1,
            "payload_identity",
            f"payload {artifact_id} is referenced for {len(messages)} messages",
        )
    for key, ids in by_message.items():
        f.equal(
            len(ids),
            1,
            "payload_identity",
            f"message {key} is referenced through {len(ids)} payload artifacts",
        )
    checked = set()
    for item in refs:
        ref = item["ref"] or {}
        artifact_id = ref.get("artifact_id")
        if artifact_id in checked:
            continue
        checked.add(artifact_id)
        entry = obs["payloads"].get(artifact_id)
        if not f.check(
            entry is not None,
            "payload_record_missing",
            f"payload {artifact_id} has no ArtifactRecord",
        ):
            continue
        record = entry["artifact"]
        f.equal(
            {
                k: record.get(k)
                for k in (
                    "kind",
                    "checksum",
                    "size_bytes",
                    "media_type",
                    "owner_type",
                    "owner_id",
                )
            },
            {
                "kind": "observation_payload",
                "checksum": ref.get("checksum"),
                "size_bytes": ref.get("size_bytes"),
                "media_type": ref.get("media_type"),
                "owner_type": "robot_run",
                "owner_id": robot_run_id,
            },
            "payload_record",
            f"payload record {artifact_id}",
        )
        stored = entry.get("object")
        if not f.check(
            stored is not None,
            "payload_object_missing",
            f"payload {artifact_id} object is not in the store",
        ):
            continue
        f.equal(
            stored.get("size_bytes"),
            ref.get("size_bytes"),
            "payload_object_size",
            f"payload {artifact_id} object size",
        )
        if deep:
            f.equal(
                stored.get("checksum"),
                ref.get("checksum"),
                "payload_object_checksum",
                f"payload {artifact_id} stored bytes",
            )


# ── Units, revisions, RobotRuns, DatasetVersions ─────────────────────────────


def unit_revisions(
    f: Findings,
    obs: dict[str, Any],
    domain: str,
    unit_id: str,
    robot_run_id: str,
    unit_key: str,
) -> list[dict[str, Any]]:
    unit = obs["units"][domain].get(unit_id)
    if not f.check(
        unit is not None,
        "unit_missing",
        f"{domain} {unit_id} ({unit_key}) is not registered",
    ):
        return []
    record = unit["record"]
    f.equal(
        {k: record.get(k) for k in ("source_kind", "robot_run_id", "unit_key")},
        {
            "source_kind": "recording",
            "robot_run_id": robot_run_id,
            "unit_key": unit_key,
        },
        "unit_identity",
        f"{domain} unit binding",
    )
    f.equal(
        rules.unit_id(
            domain=domain,
            source_kind=record.get("source_kind"),
            source_id=record.get("robot_run_id"),
            unit_key=record.get("unit_key"),
        ),
        unit_id,
        "unit_identity",
        f"{domain} id vs f(domain, source_kind, robot_run_id, unit_key)",
    )
    revisions = unit["revisions"]
    f.check(bool(revisions), "revision_missing", f"{domain} {unit_id} has no revision")
    fingerprints = [r.get("producer_fingerprint") for r in revisions]
    f.equal(
        len(set(fingerprints)),
        len(fingerprints),
        "revision_fingerprint",
        f"{domain} {unit_id}: one checksum per producer fingerprint",
    )
    for r in revisions:
        f.equal(r.get("unit_id"), unit_id, "revision_unit", f"revision of {unit_id}")
    return revisions


def run_units(obs: dict[str, Any], domain: str, robot_run_id: str) -> set[str]:
    return {
        uid
        for uid, u in obs["units"][domain].items()
        if u["record"].get("robot_run_id") == robot_run_id
    }


def check_robot_run(
    f: Findings, obs: dict[str, Any], robot_run_id: str, robot_id: str
) -> dict[str, Any] | None:
    run = obs["robot_runs"].get(robot_run_id)
    if not f.check(
        run is not None,
        "robot_run_missing",
        f"RobotRun {robot_run_id} is not registered",
    ):
        return None
    record = run["record"]
    f.equal(record.get("robot_id"), robot_id, "robot_run_identity", "robot id")
    f.equal(
        record.get("recording_artifact_id"),
        rules.recording_artifact_id(robot_run_id),
        "robot_run_identity",
        "recording artifact id",
    )
    recording = run.get("recording_artifact") or {}
    f.equal(
        recording.get("kind"),
        "robot_run_recording",
        "robot_run_recording",
        "recording artifact kind",
    )
    stored = run.get("recording_object")
    if f.check(
        stored is not None,
        "robot_run_recording",
        f"the recording of {robot_run_id} is not in the store",
    ):
        f.equal(
            stored.get("size_bytes"),
            recording.get("size_bytes"),
            "robot_run_recording",
            "recording object size",
        )
        if stored.get("checksum") is not None:
            f.equal(
                stored.get("checksum"),
                recording.get("checksum"),
                "robot_run_recording",
                "recording object bytes",
            )
    return run


def check_dataset_version(
    f: Findings,
    obs: dict[str, Any],
    key: str,
    expected_members: dict[str, str],
    expected_summary: dict[str, Any],
) -> None:
    """A contract DatasetVersion: exactly the expected units, each pinned at a
    registered revision whose pin is self-consistent, composition_checksum
    recomputed from the pins, summaries equal to what the expected revisions
    project. expected_members: unit_id -> domain."""
    version = obs["dataset_versions"].get(key)
    if not f.check(
        version is not None,
        "dataset_version_missing",
        f"DatasetVersion {key} is not composed",
    ):
        return
    members = version["members"]
    got = {m.get("unit_id"): m for m in members}
    f.equal(
        len(got), len(members), "composition_members", "at most one member per unit"
    )
    f.equal(
        sorted(got), sorted(expected_members), "composition_members", "member units"
    )
    for unit_id, member in got.items():
        domain = expected_members.get(unit_id, member.get("domain"))
        f.equal(
            member.get("domain"),
            domain,
            "composition_members",
            f"member {unit_id} domain",
        )
        f.equal(
            member.get("manifest_artifact_id"),
            rules.manifest_artifact_id(
                unit_id=unit_id, manifest_checksum=member.get("manifest_checksum", "")
            ),
            "pin_artifact_id",
            f"member {unit_id} pin",
        )
        unit = obs["units"].get(domain, {}).get(unit_id)
        registered = {
            (r.get("manifest_artifact_id"), r.get("manifest_checksum"))
            for r in (unit or {}).get("revisions", [])
        }
        f.check(
            (member.get("manifest_artifact_id"), member.get("manifest_checksum"))
            in registered,
            "member_pin",
            f"member {unit_id} pins no registered revision of its unit",
        )
    row = version["version"]
    f.equal(
        row.get("composition_checksum"),
        rules.composition_checksum(
            (m.get("domain"), m.get("unit_id"), m.get("manifest_checksum"))
            for m in members
        ),
        "composition_checksum",
        f"{key} composition_checksum vs f(member pins)",
    )
    f.equal(
        {k: _summary_value(row, k) for k in expected_summary},
        expected_summary,
        "composition_summary",
        f"{key} summaries",
    )


def _summary_value(row: dict[str, Any], key: str) -> Any:
    scene, episode = row.get("scene") or {}, row.get("episode") or {}
    if key == "observed_channels":
        return sorted(scene.get("observed_channels", []))
    if key == "episode_count":
        return episode.get("episode_count")
    return scene.get(key)


def summary_for(
    scene_exps: list[SceneExpectation], episode_exps: list[EpisodeExpectation]
) -> dict[str, Any]:
    return {
        "scene_count": len(scene_exps),
        "observation_count": sum(
            sum(len(r) for r in e.observations.values()) for e in scene_exps
        ),
        "keyframe_count": 0,
        "observed_channels": sorted(
            {t for e in scene_exps for t, r in e.observations.items() if r}
        ),
        "episode_count": sum(len(e.units) for e in episode_exps),
    }
