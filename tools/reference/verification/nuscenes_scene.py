"""nuScenes-sourced Scenes verified from the raw source (ADR-015 §12, R5).

Expectations are derived from the raw nuScenes v1.0 tables and files with this
module's own json reader and the documented rules (ADR-015 §6.2–§6.6, M3.0 D3,
scene-domain.md "nuScenes Scenes"), implemented here a second time and never
imported from SceneOps:

```text
content        inventory = selected files sorted by path, {path, size_bytes, sha256};
               content_checksum = sha256(canonical_json(files)); content_id =
               content-<sha256(canonical_json({content_id_schema,
               content_checksum}))[:32]>                         ADR-015 §5
unit           one per native scene; unit_key = scene.token; unit_id =
               f(scene, content, content_id, unit_key); scene.name enters nothing
                                                                 §3, §6.3
readings       every sample_data whose sample is one of the scene's samples (key
               frames and sweeps); a scene with a selected reading whose file is
               not preserved is never built                      §6.7
time           nuscenes.timestamp, origin unix_epoch, resolution 1 000 ns;
               t = timestamp_us · 1 000 for readings, ego poses and samples;
               window [min, max + 1) over every reading of the scene, every
               channel                                           §6.2
channels       sensor.channel verbatim, modality = sensor.modality, frame = the
               channel; frames global (world), ego (ego), one sensor frame each
calibrations   cal-<calibrated_sensor.token>: ego -> channel, translation and
               rotation [w, x, y, z] verbatim, camera_intrinsic for a camera only
ego poses      pose-<ego_pose.token>: global -> ego at ego_pose.timestamp
observations   <channel>-<rank:08d>, rank in (timestamp, token) order per channel;
               PayloadRef {uri = sceneops://sources/contents/<content_id>/files/
               <filename> (ADR-016 spelling), sha256, size, media type, selector
               null}; media type camera .jpg image/jpeg, lidar .pcd.bin
               application/x.nuscenes.lidar-pcd-bin, radar .pcd
               application/x.nuscenes.radar-pcd                  §6.5, D3
               calibration_id / ego_pose_id from the reading's own tokens, image
               size for a camera, native_ref = sample_data.token  §6.6
keyframes      keyframe-<rank:06d> per sample in (timestamp, token) order, at
               sample.timestamp, the key-frame reading of each channel, native_ref
               = sample.token                                    §6.6
producer       sceneops.nuscenes_scene_builder v2; fingerprint over the normalized
               build configuration {channels sorted} and {source_kind: content,
               content_id, content_checksum}                     ADR-007 §15.2
```

The platform state is read back read-only (FastAPI + object store) and held to
the derivation; every negative control applies a consistent fault to a copy of
a passing observation and must be detected.
"""

from __future__ import annotations

import copy
import fnmatch
import json
import urllib.parse
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import rules
from .findings import Findings
from .nuscenes_source import file_sha256

MANIFEST_SCHEMA = "sceneops.scene_manifest/v5"
CONTENT_ID_SCHEMA = "sceneops.content_id/v1"
CLOCK = "nuscenes.timestamp"
CLOCK_DOMAINS = [{"clock": CLOCK, "origin": "unix_epoch", "resolution_ns": 1000}]
PRODUCER_ID = "sceneops.nuscenes_scene_builder"
SEMANTICS_VERSION = 2
WORLD, EGO = "global", "ego"
MEDIA = {
    "camera": (".jpg", "jpg", "image/jpeg"),
    "lidar": (".pcd.bin", "pcd", "application/x.nuscenes.lidar-pcd-bin"),
    "radar": (".pcd", "pcd", "application/x.nuscenes.radar-pcd"),
}
TABLES = ("scene", "sample", "sample_data", "ego_pose", "calibrated_sensor", "sensor")
OBSERVATION_SCHEMA = "sceneops.nuscenes_scene_observation/1"


# ── the raw source ───────────────────────────────────────────────────────────


def _matches(pattern: str, path: str) -> bool:
    """Whole-path match: ``*`` within a segment, ``**`` as a whole segment of
    zero or more segments (the declared selection dialect)."""

    def match(p: list[str], s: list[str]) -> bool:
        if not p:
            return not s
        if p[0] == "**":
            return any(match(p[1:], s[i:]) for i in range(len(s) + 1))
        return bool(s) and fnmatch.fnmatchcase(s[0], p[0]) and match(p[1:], s[1:])

    return match(pattern.split("/"), path.split("/"))


@dataclass
class RawNuScenes:
    root: Path
    version: str
    files: list[dict[str, Any]]
    tables: dict[str, list[dict[str, Any]]]
    _index: dict[str, dict[str, dict[str, Any]]] = field(
        default_factory=dict, repr=False
    )

    @property
    def content_checksum(self) -> str:
        return rules.checksum(rules.canonical_json(self.files))

    @property
    def content_id(self) -> str:
        document = {
            "content_id_schema": CONTENT_ID_SCHEMA,
            "content_checksum": self.content_checksum,
        }
        return "content-" + rules.sha256_hex(rules.canonical_json(document))[:32]

    def index(self, table: str) -> dict[str, dict[str, Any]]:
        if table not in self._index:
            self._index[table] = {row["token"]: row for row in self.tables[table]}
        return self._index[table]

    def file(self, path: str) -> dict[str, Any] | None:
        return self._files().get(path)

    def _files(self) -> dict[str, dict[str, Any]]:
        if "#files" not in self._index:
            self._index["#files"] = {f["path"]: f for f in self.files}
        return self._index["#files"]

    def channel(self, reading: dict[str, Any]) -> dict[str, Any]:
        calibrated = self.index("calibrated_sensor")[reading["calibrated_sensor_token"]]
        return self.index("sensor")[calibrated["sensor_token"]]


def read_raw(root: Path, include: Iterable[str]) -> RawNuScenes:
    patterns = list(include)
    files = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        relative = path.relative_to(root).as_posix()
        if any(_matches(pattern, relative) for pattern in patterns):
            files.append(
                {
                    "path": relative,
                    "size_bytes": path.stat().st_size,
                    "sha256": file_sha256(path),
                }
            )
    (version,) = sorted(
        {
            f["path"].split("/")[0]
            for f in files
            if f["path"].count("/") == 1 and f["path"].endswith("/scene.json")
        }
    )
    tables = {
        name: json.loads((root / version / f"{name}.json").read_text())
        for name in TABLES
    }
    return RawNuScenes(root=root, version=version, files=files, tables=tables)


# ── the documented rules ─────────────────────────────────────────────────────


def normalized_config(config: dict[str, Any]) -> dict[str, Any]:
    return {"channels": sorted(config["channels"])}


def _floats(values: Iterable[Any]) -> list[float]:
    return [float(v) for v in values]


def _transform(parent: str, child: str, row: dict[str, Any]) -> dict[str, Any]:
    return {
        "parent_frame_id": parent,
        "child_frame_id": child,
        "translation_m": _floats(row["translation"]),
        "rotation_wxyz": _floats(row["rotation"]),
    }


@dataclass
class ExpectedUnit:
    unit_key: str
    unit_id: str
    name: str
    manifest: dict[str, Any]
    # Why no build is expected for this unit (raw facts the rules refuse).
    refusals: list[str] = field(default_factory=list)


def derive(
    raw: RawNuScenes, config: dict[str, Any], unit_keys: Iterable[str] | None = None
) -> dict[str, ExpectedUnit]:
    """Expected unit id -> expected unit, for ``unit_keys`` (default all)."""
    content_id, content_checksum = raw.content_id, raw.content_checksum
    build_config = normalized_config(config)
    selected = set(build_config["channels"])
    producer = {
        "producer_id": PRODUCER_ID,
        "semantics_version": SEMANTICS_VERSION,
        "build_config": build_config,
        "producer_fingerprint": rules.producer_fingerprint(
            producer_id=PRODUCER_ID,
            semantics_version=SEMANTICS_VERSION,
            build_config=build_config,
            source={
                "source_kind": "content",
                "content_id": content_id,
                "content_checksum": content_checksum,
            },
        ),
    }
    sensors = {s["channel"]: s for s in raw.tables["sensor"]}
    channels = [
        {
            "channel": c,
            "modality": sensors[c]["modality"],
            "frame_id": c,
            "source_clock": CLOCK,
            "sensor_id": None,
        }
        for c in sorted(selected)
    ]
    frames = sorted(
        [
            {"frame_id": WORLD, "role": "world"},
            {"frame_id": EGO, "role": "ego"},
            *({"frame_id": c, "role": "sensor"} for c in selected),
        ],
        key=lambda f: f["frame_id"],
    )
    wanted = None if unit_keys is None else set(unit_keys)
    readings_by_sample: dict[str, list[dict[str, Any]]] = {}
    for reading in raw.tables["sample_data"]:
        readings_by_sample.setdefault(reading["sample_token"], []).append(reading)

    expected: dict[str, ExpectedUnit] = {}
    for scene in raw.tables["scene"]:
        unit_key = scene["token"]
        if wanted is not None and unit_key not in wanted:
            continue
        unit_id = rules.unit_id(
            domain="scene", source_kind="content", source_id=content_id, unit_key=unit_key
        )
        samples = [s for s in raw.tables["sample"] if s["scene_token"] == unit_key]
        readings = [r for s in samples for r in readings_by_sample.get(s["token"], [])]
        mine = [r for r in readings if raw.channel(r)["channel"] in selected]
        missing = sorted({r["filename"] for r in mine if raw.file(r["filename"]) is None})
        refusals = [f"{len(missing)} file(s) not preserved, e.g. {missing[:2]}"] if missing else []

        observations, calibrations, poses = [], {}, {}
        observation_of: dict[str, str] = {}
        by_channel: dict[str, list[dict[str, Any]]] = {}
        for r in mine:
            by_channel.setdefault(raw.channel(r)["channel"], []).append(r)
        for channel, items in sorted(by_channel.items()):
            modality = sensors[channel]["modality"]
            for rank, r in enumerate(sorted(items, key=lambda r: (r["timestamp"], r["token"]))):
                cs = raw.index("calibrated_sensor")[r["calibrated_sensor_token"]]
                calibrations[cs["token"]] = {
                    "calibration_id": f"cal-{cs['token']}",
                    "channel": channel,
                    "extrinsic": _transform(EGO, channel, cs),
                    "camera_intrinsic": [_floats(row) for row in cs["camera_intrinsic"]]
                    if modality == "camera"
                    else None,
                }
                pose = raw.index("ego_pose")[r["ego_pose_token"]]
                poses[pose["token"]] = {
                    "pose_id": f"pose-{pose['token']}",
                    "timestamp_ns": pose["timestamp"] * 1000,
                    "source_clock": CLOCK,
                    "transform": _transform(WORLD, EGO, pose),
                }
                entry = raw.file(r["filename"]) or {"sha256": None, "size_bytes": None}
                observation_id = f"{channel}-{rank:08d}"
                observation_of[r["token"]] = observation_id
                observations.append(
                    {
                        "observation_id": observation_id,
                        "channel": channel,
                        "timestamp_ns": r["timestamp"] * 1000,
                        "payload": {
                            "uri": rules.content_file_uri(content_id, r["filename"]),
                            "checksum": entry["sha256"],
                            "size_bytes": entry["size_bytes"],
                            "media_type": MEDIA[modality][2],
                            "selector": None,
                        },
                        "calibration_id": f"cal-{cs['token']}",
                        "ego_pose_id": f"pose-{pose['token']}",
                        "image_size": {"width_px": r["width"], "height_px": r["height"]}
                        if modality == "camera"
                        else None,
                        "native_ref": r["token"],
                    }
                )
        groups = []
        for rank, sample in enumerate(sorted(samples, key=lambda s: (s["timestamp"], s["token"]))):
            groups.append(
                {
                    "group_id": f"keyframe-{rank:06d}",
                    "kind": "keyframe",
                    "timestamp_ns": sample["timestamp"] * 1000,
                    "source_clock": CLOCK,
                    "observation_ids": sorted(
                        observation_of[r["token"]]
                        for r in readings_by_sample.get(sample["token"], [])
                        if r["is_key_frame"] and r["token"] in observation_of
                    ),
                    "native_ref": sample["token"],
                }
            )
        times = [r["timestamp"] * 1000 for r in readings]
        manifest = {
            "schema_version": MANIFEST_SCHEMA,
            "lineage": {
                "source": {
                    "source_kind": "content",
                    "content_id": content_id,
                    "content_checksum": content_checksum,
                    "source_clock": CLOCK,
                    "start_timestamp_ns": min(times) if times else None,
                    "end_timestamp_ns": max(times) + 1 if times else None,
                    "unit_key": unit_key,
                },
                "producer": producer,
            },
            "clock_domains": CLOCK_DOMAINS,
            "coordinate_frames": frames,
            "channels": channels,
            "calibrations": sorted(calibrations.values(), key=lambda c: c["calibration_id"]),
            "observations": sorted(
                observations,
                key=lambda o: (o["channel"], o["timestamp_ns"], o["observation_id"]),
            ),
            "poses": sorted(
                poses.values(), key=lambda p: (p["source_clock"], p["timestamp_ns"], p["pose_id"])
            ),
            "groups": groups,
        }
        expected[unit_id] = ExpectedUnit(unit_key, unit_id, scene["name"], manifest, refusals)
    return expected


def summary(raw: RawNuScenes, expected: dict[str, ExpectedUnit]) -> dict[str, Any]:
    """The facts the M3 plan states for the scene-0061 slice, from the derivation."""
    built = [u for u in expected.values() if not u.refusals]
    out: dict[str, Any] = {
        "content_id": raw.content_id,
        "file_count": len(raw.files),
        "total_bytes": sum(f["size_bytes"] for f in raw.files),
        "table_version": raw.version,
        "refused units": sorted(f"{u.name} {u.unit_key}" for u in expected.values() if u.refusals),
        "scenes": {},
    }
    for unit in built:
        m = unit.manifest
        modality = {c["channel"]: c["modality"] for c in m["channels"]}
        counts: dict[str, int] = {}
        for o in m["observations"]:
            counts[modality[o["channel"]]] = counts.get(modality[o["channel"]], 0) + 1
        source = m["lineage"]["source"]
        out["scenes"][unit.name] = {
            "unit_key": unit.unit_key,
            "observations": len(m["observations"]),
            "by_modality": dict(sorted(counts.items())),
            "channels": len(m["channels"]),
            "keyframe_groups": len(m["groups"]),
            "group_sizes": sorted({len(g["observation_ids"]) for g in m["groups"]}),
            "poses": len(m["poses"]),
            "window": [source["start_timestamp_ns"], source["end_timestamp_ns"]],
            "before_first_sample_ns": m["groups"][0]["timestamp_ns"] - source["start_timestamp_ns"],
            "after_last_sample_ns": source["end_timestamp_ns"] - 1 - m["groups"][-1]["timestamp_ns"],
        }
    return out


# ── the platform state ───────────────────────────────────────────────────────


def empty_observation() -> dict[str, Any]:
    return {
        "schema": OBSERVATION_SCHEMA,
        "snapshot": None,
        "content": None,
        "inventory": None,
        "content_objects": {},
        "units": {},
        "manifests": {},
        "dataset_version": None,
    }


def _referenced_paths(manifest: dict[str, Any]) -> set[str]:
    """The preserved files of the Scene's own content its payloads name."""
    content_id = manifest.get("lineage", {}).get("source", {}).get("content_id")
    paths = (
        rules.content_file_path_of(o.get("payload"), content_id)
        for o in manifest.get("observations", [])
    )
    return {p for p in paths if p is not None}


def collect(
    api: Callable[[str], Any],
    store: Any,
    *,
    snapshot_id: str,
    dataset_key: str | None = None,
) -> dict[str, Any]:
    """Read-only: the snapshot, its content and inventory record, every Scene of
    the content with its revisions and manifest bytes, the stored size of every
    referenced content file, and optionally one DatasetVersion."""
    from .observation import _paged, snake

    obs = empty_observation()
    snapshot = snake((api(f"/source-snapshots/{snapshot_id}") or {}).get("sourceSnapshot"))
    obs["snapshot"] = snapshot
    if not snapshot:
        return obs
    content_id = snapshot["content_id"]
    obs["content"] = snake((api(f"/source-contents/{content_id}") or {}).get("sourceContent"))
    if obs["content"]:
        obs["inventory"] = snake(
            (api(f"/artifacts/{obs['content']['inventory_artifact_id']}") or {}).get("artifact")
        )
    referenced: set[str] = set()
    for unit in _paged(api, f"/scenes?content_id={urllib.parse.quote(content_id)}", "scenes"):
        unit = snake(unit)
        detail = snake(api(f"/scenes/{unit['scene_id']}") or {})
        obs["units"][unit["scene_id"]] = {
            "record": unit,
            "revisions": detail.get("revisions", []),
        }
        for revision in detail.get("revisions", []):
            artifact_id = revision["manifest_artifact_id"]
            artifact = snake((api(f"/artifacts/{artifact_id}") or {}).get("artifact")) or {}
            data = store.get(artifact["uri"]) if artifact.get("uri") else None
            obs["manifests"][artifact_id] = {
                "artifact": artifact,
                "bytes": None if data is None else data.decode("utf-8"),
            }
            if data is not None:
                referenced |= _referenced_paths(json.loads(data))
    inventory_uri = (obs["inventory"] or {}).get("uri")
    if inventory_uri:
        base = inventory_uri.removesuffix("inventory.json") + "files/"
        obs["content_objects"] = {path: store.head(base + path) for path in sorted(referenced)}
    if dataset_key:
        dataset_id, version = dataset_key.split("/", 1)
        base = f"/datasets/{urllib.parse.quote(dataset_id)}/versions/{urllib.parse.quote(version)}"
        row = api(base)
        if row:
            obs["dataset_version"] = {
                "key": dataset_key,
                "version": snake(row["version"]),
                "members": [
                    {
                        "domain": "scene",
                        "unit_id": r["scene_id"],
                        "manifest_artifact_id": r["manifest_artifact_id"],
                        "manifest_checksum": r["manifest_checksum"],
                    }
                    for r in map(snake, _paged(api, f"{base}/scenes", "scenes"))
                ],
            }
    return obs


def projection(manifest: dict[str, Any]) -> dict[str, Any]:
    """The revision columns a manifest determines (scene-domain.md)."""
    source = manifest["lineage"]["source"]
    return {
        "producer_fingerprint": manifest["lineage"]["producer"]["producer_fingerprint"],
        "window_clock": source["source_clock"],
        "window_start_timestamp_ns": source["start_timestamp_ns"],
        "window_end_timestamp_ns": source["end_timestamp_ns"],
        "observed_channels": sorted({o["channel"] for o in manifest["observations"]}),
        "observation_count": len(manifest["observations"]),
        "keyframe_count": sum(1 for g in manifest["groups"] if g["kind"] == "keyframe"),
    }


def synthetic_observation(
    raw: RawNuScenes,
    expected: dict[str, ExpectedUnit],
    *,
    snapshot: dict[str, Any],
    dataset_key: str | None = None,
) -> dict[str, Any]:
    """The platform state the rules describe (test double): proves the checks,
    not the platform."""
    obs = empty_observation()
    content_id = raw.content_id
    obs["snapshot"] = {**snapshot, "content_id": content_id}
    obs["content"] = {
        "content_id": content_id,
        "format": "nuscenes",
        "content_checksum": raw.content_checksum,
        "inventory_artifact_id": f"art-sourceinventory-{content_id}",
        "file_count": len(raw.files),
        "total_bytes": sum(f["size_bytes"] for f in raw.files),
    }
    obs["inventory"] = {
        "artifact_id": f"art-sourceinventory-{content_id}",
        "uri": f"s3://synthetic/sources/contents/{content_id}/inventory.json",
    }
    members = []
    for unit in expected.values():
        if unit.refusals:
            continue
        data = rules.canonical_json(unit.manifest)
        checksum = rules.checksum(data)
        artifact_id = rules.manifest_artifact_id(unit_id=unit.unit_id, manifest_checksum=checksum)
        obs["manifests"][artifact_id] = {
            "artifact": {
                "artifact_id": artifact_id,
                "kind": "scene_manifest",
                "checksum": checksum,
                "size_bytes": len(data),
                "owner_type": "scene",
                "owner_id": unit.unit_id,
            },
            "bytes": data.decode("utf-8"),
        }
        identity = {
            "scene_id": unit.unit_id,
            "source_kind": "content",
            "robot_run_id": None,
            "content_id": content_id,
            "unit_key": unit.unit_key,
        }
        obs["units"][unit.unit_id] = {
            "record": identity,
            "revisions": [
                {
                    **identity,
                    "manifest_artifact_id": artifact_id,
                    "manifest_checksum": checksum,
                    **projection(unit.manifest),
                }
            ],
        }
        members.append(
            {
                "domain": "scene",
                "unit_id": unit.unit_id,
                "manifest_artifact_id": artifact_id,
                "manifest_checksum": checksum,
            }
        )
        for o in unit.manifest["observations"]:
            path = rules.content_file_path_of(o["payload"], raw.content_id)
            obs["content_objects"][path] = o["payload"]["size_bytes"]
    if dataset_key:
        obs["dataset_version"] = {
            "key": dataset_key,
            "version": {
                "composition_checksum": rules.composition_checksum(
                    (m["domain"], m["unit_id"], m["manifest_checksum"]) for m in members
                ),
                "scene": {"scene_count": len(members)},
                "episode": None,
            },
            "members": members,
        }
    return obs


# ── checks ───────────────────────────────────────────────────────────────────


def _first_difference(observed: list[Any], expected: list[Any]) -> str:
    for i, (a, b) in enumerate(zip(observed, expected, strict=False)):
        if a != b:
            return f"first difference at {i}: observed {a!r}, expected {b!r}"[:400]
    return f"{len(observed)} observed, {len(expected)} expected"


def _sequence(f: Findings, observed: list[Any], expected: list[Any], code: str, what: str) -> bool:
    return f.check(observed == expected, code, f"{what}: " + _first_difference(observed, expected))


def check_manifest(f: Findings, manifest: dict[str, Any], unit: ExpectedUnit) -> None:
    exp = unit.manifest
    f.equal(manifest.get("schema_version"), MANIFEST_SCHEMA, "manifest_schema", "schema_version")
    source = manifest.get("lineage", {}).get("source", {})
    exp_source = exp["lineage"]["source"]
    binding = ("source_kind", "content_id", "content_checksum", "unit_key")
    f.equal(
        {k: source.get(k) for k in binding},
        {k: exp_source[k] for k in binding},
        "source_binding",
        "lineage source binding",
    )
    window = ("source_clock", "start_timestamp_ns", "end_timestamp_ns")
    f.equal([source.get(k) for k in window], [exp_source[k] for k in window], "window", "scene window")
    f.equal(manifest.get("lineage", {}).get("producer"), exp["lineage"]["producer"], "producer_fingerprint", "producer")
    f.equal(manifest.get("clock_domains"), exp["clock_domains"], "clock_domains", "clock domains")
    f.equal(manifest.get("coordinate_frames"), exp["coordinate_frames"], "coordinate_frames", "coordinate frames")
    f.equal(manifest.get("channels"), exp["channels"], "scene_channels", "channels")
    f.equal(manifest.get("calibrations"), exp["calibrations"], "calibrations", "calibrations")

    observed = manifest.get("observations") or []
    for channel in sorted({o["channel"] for o in exp["observations"]} | {o.get("channel") for o in observed}):
        got = [o for o in observed if o.get("channel") == channel]
        want = [o for o in exp["observations"] if o["channel"] == channel]
        keyed = [(o.get("observation_id"), o.get("timestamp_ns"), o.get("native_ref")) for o in got]
        exp_keyed = [(o["observation_id"], o["timestamp_ns"], o["native_ref"]) for o in want]
        if not _sequence(f, keyed, exp_keyed, "scene_observations", f"{channel} ids, timestamps and native refs"):
            continue
        _sequence(f, [o.get("payload") for o in got], [o["payload"] for o in want], "source_file_ref", f"{channel} payloads")
        links = ("calibration_id", "ego_pose_id", "image_size")
        _sequence(
            f,
            [[o.get(k) for k in links] for o in got],
            [[o[k] for k in links] for o in want],
            "observation_links",
            f"{channel} calibration, ego pose and image size links",
        )
    _sequence(f, manifest.get("poses") or [], exp["poses"], "poses", "ego poses")
    _sequence(f, manifest.get("groups") or [], exp["groups"], "keyframe_groups", "keyframe groups")
    known = {"schema_version", "lineage", "clock_domains", "coordinate_frames", "channels",
             "calibrations", "observations", "poses", "groups"}  # fmt: skip
    f.equal(sorted(set(manifest) - known), [], "manifest_forbidden_field", "manifest members")


def verify(
    f: Findings,
    obs: dict[str, Any],
    raw: RawNuScenes,
    expected: dict[str, ExpectedUnit],
    *,
    declaration: dict[str, Any],
    all_units: bool,
) -> None:
    """``expected``: the derivation of the verified units; ``all_units``: the
    content's unit set must be exactly the buildable part of ``expected``
    (otherwise it must contain it)."""
    snapshot = obs.get("snapshot") or {}
    if not f.check(bool(snapshot), "snapshot", "the snapshot is not registered"):
        return
    f.equal(
        {k: snapshot.get(k) for k in ("format", "upstream", "upstream_revision", "content_id")},
        {**{k: declaration[k] for k in ("format", "upstream", "upstream_revision")}, "content_id": raw.content_id},
        "snapshot",
        "snapshot declaration and its content",
    )
    content = obs.get("content") or {}
    f.equal(
        [content.get(k) for k in ("content_id", "content_checksum", "file_count", "total_bytes")],
        [raw.content_id, raw.content_checksum, len(raw.files), sum(x["size_bytes"] for x in raw.files)],
        "content",
        "registered content vs the raw tree",
    )
    units = obs.get("units", {})
    buildable = sorted(u for u, e in expected.items() if not e.refusals)
    if all_units:
        f.equal(sorted(units), buildable, "unit_set", "Scene units of the content")
    else:
        f.check(set(buildable) <= set(units), "unit_set", f"missing units {sorted(set(buildable) - set(units))[:5]}")
    # JSON tokens: a declaration value, a descriptor member name or a scene
    # name, quoted, so a value never matches inside another string.
    forbidden = [
        json.dumps(token)
        for token in (
            snapshot.get("snapshot_id"),
            declaration["upstream"],
            declaration["upstream_revision"],
            *declaration.get("annotations", {}).values(),
            "snapshot_id",
            "upstream",
            "upstream_revision",
            "license",
        )
        if token
    ]
    for unit_id, unit in sorted(expected.items()):
        g = f.scoped(unit_key=unit.unit_key)
        if unit.refusals:
            g.check(unit_id not in units, "refused_unit", f"unit {unit.name} was built: {unit.refusals}")
            continue
        observed = units.get(unit_id)
        if observed is None:
            continue
        record = observed["record"]
        identity = ("scene_id", "source_kind", "robot_run_id", "content_id", "unit_key")
        g.equal(
            {k: record.get(k) for k in identity},
            {"scene_id": unit_id, "source_kind": "content", "robot_run_id": None,
             "content_id": raw.content_id, "unit_key": unit.unit_key},  # fmt: skip
            "unit_identity",
            "unit record",
        )
        revisions = observed["revisions"]
        if not g.equal(len(revisions), 1, "revision_count", "revisions per unit"):
            continue
        revision = revisions[0]
        entry = obs["manifests"].get(revision.get("manifest_artifact_id")) or {}
        data = entry.get("bytes")
        if not g.check(data is not None, "manifest_object", "manifest bytes not readable"):
            continue
        raw_bytes = data.encode("utf-8")
        checksum = rules.checksum(raw_bytes)
        artifact = entry.get("artifact", {})
        g.equal(
            [artifact.get("kind"), artifact.get("checksum"), artifact.get("size_bytes"), revision.get("manifest_checksum")],
            ["scene_manifest", checksum, len(raw_bytes), checksum],
            "manifest_checksum",
            "manifest record, revision and stored bytes",
        )
        g.equal(
            revision.get("manifest_artifact_id"),
            rules.manifest_artifact_id(unit_id=unit_id, manifest_checksum=checksum),
            "revision_pin",
            "manifest artifact id",
        )
        manifest = json.loads(raw_bytes)
        g.equal(raw_bytes, rules.canonical_json(manifest), "manifest_canonical", "canonical bytes")
        named = [t for t in [*forbidden, json.dumps(unit.name)] if t in data]
        g.check(not named, "manifest_forbidden_field", f"the manifest names snapshot, descriptor or display data: {named}")
        check_manifest(g, manifest, unit)
        g.equal(
            {k: revision.get(k) for k in projection(unit.manifest)},
            projection(unit.manifest),
            "revision_projection",
            "revision projection",
        )

    for path, stored in sorted((obs.get("content_objects") or {}).items()):
        entry = raw.file(path)
        f.check(entry is not None, "source_file_ref", f"{path} is not a raw file")
        f.equal(stored, entry and entry["size_bytes"], "source_file_object", f"stored size of {path}")

    version = obs.get("dataset_version")
    if version is not None:
        members = version["members"]
        pins = sorted((m["unit_id"], m["manifest_checksum"]) for m in members)
        registered = sorted(
            (unit_id, r["manifest_checksum"])
            for unit_id, u in units.items()
            if unit_id in buildable
            for r in u["revisions"]
        )
        f.equal(pins, registered, "composition_members", "DatasetVersion members")
        f.equal(
            version["version"].get("composition_checksum"),
            rules.composition_checksum((m["domain"], m["unit_id"], m["manifest_checksum"]) for m in members),
            "composition_checksum",
            "composition checksum from the pins",
        )
        f.equal(
            (version["version"].get("scene") or {}).get("scene_count"),
            len(buildable),
            "composition_summary",
            "scene_count",
        )


# ── negative controls ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Control:
    name: str
    fault: str
    codes: frozenset[str]
    apply: Callable[[dict[str, Any]], None]


def rewrite(obs: dict[str, Any], mutate: Callable[[dict[str, Any]], None]) -> None:
    """A consistent fault on the first unit's manifest."""
    rewrite_unit(obs, min(obs["units"]), mutate)


def rewrite_unit(obs: dict[str, Any], unit_id: str, mutate: Callable[[dict[str, Any]], None]) -> None:
    """Apply ``mutate`` to the unit's manifest and recompute everything the
    platform derives from the bytes (checksum, artifact id, revision
    projection, DatasetVersion pin and composition checksum), so the result
    is self-consistent."""
    revision = obs["units"][unit_id]["revisions"][0]
    entry = obs["manifests"].pop(revision["manifest_artifact_id"])
    manifest = json.loads(entry["bytes"])
    mutate(manifest)
    data = rules.canonical_json(manifest)
    checksum = rules.checksum(data)
    artifact_id = rules.manifest_artifact_id(unit_id=unit_id, manifest_checksum=checksum)
    entry["bytes"] = data.decode("utf-8")
    entry["artifact"] = {**entry["artifact"], "artifact_id": artifact_id, "checksum": checksum, "size_bytes": len(data)}
    obs["manifests"][artifact_id] = entry
    revision.update(manifest_artifact_id=artifact_id, manifest_checksum=checksum, **projection(manifest))
    version = obs.get("dataset_version")
    if version is not None:
        for member in version["members"]:
            if member["unit_id"] == unit_id:
                member.update(manifest_artifact_id=artifact_id, manifest_checksum=checksum)
        version["version"]["composition_checksum"] = rules.composition_checksum(
            (m["domain"], m["unit_id"], m["manifest_checksum"]) for m in version["members"]
        )


def _refingerprint(manifest: dict[str, Any]) -> None:
    source = manifest["lineage"]["source"]
    producer = manifest["lineage"]["producer"]
    producer["producer_fingerprint"] = rules.producer_fingerprint(
        producer_id=producer["producer_id"],
        semantics_version=producer["semantics_version"],
        build_config=producer["build_config"],
        source={k: source[k] for k in ("source_kind", "content_id", "content_checksum")},
    )


def _of(manifest: dict[str, Any], modality: str) -> list[dict[str, Any]]:
    channels = {c["channel"] for c in manifest["channels"] if c["modality"] == modality}
    return [o for o in manifest["observations"] if o["channel"] in channels]


def _shift(manifest: dict[str, Any]) -> None:
    cameras = _of(manifest, "camera")
    cameras[len(cameras) // 2]["timestamp_ns"] += 1000


def _origin(manifest: dict[str, Any]) -> None:
    manifest["clock_domains"][0]["origin"] = "source"


def _window(manifest: dict[str, Any]) -> None:
    manifest["lineage"]["source"]["start_timestamp_ns"] -= 1000


def _drop(manifest: dict[str, Any]) -> None:
    radar = _of(manifest, "radar")[-1]
    manifest["observations"].remove(radar)
    for group in manifest["groups"]:
        if radar["observation_id"] in group["observation_ids"]:
            group["observation_ids"].remove(radar["observation_id"])


def _swap_sensor_files(manifest: dict[str, Any]) -> None:
    """Two sensors' key frames of one sample exchange their files."""
    by_id = {o["observation_id"]: o for o in manifest["observations"]}
    a, b = (by_id[i] for i in manifest["groups"][0]["observation_ids"][:2])
    a["payload"], b["payload"] = b["payload"], a["payload"]


def _file_checksum(manifest: dict[str, Any]) -> None:
    _of(manifest, "lidar")[0]["payload"]["checksum"] = rules.checksum(b"another point cloud")


def _media_type(manifest: dict[str, Any]) -> None:
    for o in _of(manifest, "lidar"):
        o["payload"]["media_type"] = MEDIA["radar"][2]


def _ego_pose_link(manifest: dict[str, Any]) -> None:
    lidar = _of(manifest, "lidar")
    lidar[0]["ego_pose_id"] = lidar[1]["ego_pose_id"]


def _calibration_value(manifest: dict[str, Any]) -> None:
    manifest["calibrations"][0]["extrinsic"]["translation_m"][0] += 0.01


def _pose_value(manifest: dict[str, Any]) -> None:
    manifest["poses"][len(manifest["poses"]) // 2]["transform"]["translation_m"][1] += 0.5


def _native_token(manifest: dict[str, Any]) -> None:
    _of(manifest, "camera")[0]["native_ref"] = "0" * 32


def _group_token(manifest: dict[str, Any]) -> None:
    first, second = manifest["groups"][0], manifest["groups"][1]
    first["native_ref"], second["native_ref"] = second["native_ref"], first["native_ref"]


def _group_member(manifest: dict[str, Any]) -> None:
    manifest["groups"][-1]["observation_ids"].pop()


def _content_binding(manifest: dict[str, Any]) -> None:
    other_checksum = rules.checksum(b"other content")
    other_id = "content-" + rules.sha256_hex(
        rules.canonical_json({"content_id_schema": CONTENT_ID_SCHEMA, "content_checksum": other_checksum})
    )[:32]
    own_id = manifest["lineage"]["source"]["content_id"]
    manifest["lineage"]["source"].update(content_id=other_id, content_checksum=other_checksum)
    for o in manifest["observations"]:
        path = rules.content_file_path_of(o["payload"], own_id)
        o["payload"]["uri"] = rules.content_file_uri(other_id, path)
    _refingerprint(manifest)


def _missing_file(obs: dict[str, Any]) -> None:
    path = min(obs["content_objects"])
    obs["content_objects"][path] = None


def _snapshot_in_manifest(manifest: dict[str, Any]) -> None:
    manifest["lineage"]["snapshot_id"] = "snapshot-" + "0" * 32


def controls() -> list[Control]:
    def on_manifest(mutate):
        return lambda obs: rewrite(obs, mutate)

    return [
        Control("timestamp_drift", "one camera reading 1 µs off its source time",
                frozenset({"scene_observations"}), on_manifest(_shift)),
        Control("clock_origin", "the epoch clock declared origin source",
                frozenset({"clock_domains"}), on_manifest(_origin)),
        Control("window_shift", "the scene window starts 1 µs early",
                frozenset({"window"}), on_manifest(_window)),
        Control("dropped_reading", "the last radar reading dropped",
                frozenset({"scene_observations"}), on_manifest(_drop)),
        Control("sensor_file_swap", "two sensors' files of one sample exchanged",
                frozenset({"source_file_ref"}), on_manifest(_swap_sensor_files)),
        Control("source_file_checksum", "a lidar payload reference pins the sha256 of other bytes",
                frozenset({"source_file_ref"}), on_manifest(_file_checksum)),
        Control("media_type", "lidar files declared radar point clouds",
                frozenset({"source_file_ref"}), on_manifest(_media_type)),
        Control("ego_pose_link", "a lidar reading linked to another reading's ego pose",
                frozenset({"observation_links"}), on_manifest(_ego_pose_link)),
        Control("calibration_value", "a calibration translated by 1 cm",
                frozenset({"calibrations"}), on_manifest(_calibration_value)),
        Control("ego_pose_value", "an ego pose moved by 0.5 m",
                frozenset({"poses"}), on_manifest(_pose_value)),
        Control("native_token", "a reading's native_ref replaced",
                frozenset({"scene_observations"}), on_manifest(_native_token)),
        Control("sample_token_swap", "two keyframe groups' sample tokens exchanged",
                frozenset({"keyframe_groups"}), on_manifest(_group_token)),
        Control("keyframe_member", "a key frame dropped from its group",
                frozenset({"keyframe_groups"}), on_manifest(_group_member)),
        Control("content_binding", "bound to another content, fingerprint recomputed",
                frozenset({"source_binding", "producer_fingerprint"}), on_manifest(_content_binding)),
        Control("missing_native_file", "a referenced file absent from the store",
                frozenset({"source_file_object"}), _missing_file),
        Control("snapshot_in_manifest", "a snapshot id in the manifest",
                frozenset({"manifest_forbidden_field"}), on_manifest(_snapshot_in_manifest)),
    ]  # fmt: skip


def run_controls(obs: dict[str, Any], verify_copy: Callable[[dict[str, Any]], set[str]]) -> list[dict[str, Any]]:
    results = []
    for control in controls():
        corrupted = copy.deepcopy(obs)
        control.apply(corrupted)
        codes = verify_copy(corrupted)
        results.append(
            {
                "control": control.name,
                "fault": control.fault,
                "expected_codes": sorted(control.codes),
                "observed_codes": sorted(codes),
                "detected": bool(codes & control.codes),
            }
        )
    return results
