"""LeRobot-sourced Episodes verified from the raw source (ADR-015 §12, R5).

Expectations are derived from the raw LeRobot v3.0 tree with this module's own
pyarrow / json reader and the documented rules, implemented here a second time
and never imported from SceneOps:

```text
content        inventory = files sorted by path, {path, size_bytes, sha256} each;
               content_checksum = sha256(canonical_json(files));
               content_id = content-<sha256(canonical_json({content_id_schema,
               content_checksum}))[:32]>                     ADR-015 §5
unit           one per native episode; unit_key = str(episode_index); unit_id =
               f(episode, content, content_id, unit_key)     ADR-015 §3, §6.3
time           lerobot.frame_time, origin unit, resolution 1 ns; frame k at
               round_half_even(k · 10⁹ / fps), computed here on exact rationals;
               window [0, t(length − 1) + 1); frame_timeline {clock, fps, length};
               a build is expected only when every recorded timestamp is exactly
               float32(frame_index / fps) and frames are dense from 0   §6.2, §6.6
streams        topic = feature key, schema_name = dtype, fields [{value, feature,
               component_names}] (a vector's source names, flattened from a
               one-key dict); video features: PayloadRef {uri =
               sceneops://sources/contents/<content_id>/files/<path> (ADR-016
               spelling), sha256, size_bytes, video/mp4, selector {video_frame,
               round_half_even((from_timestamp + k / fps) · 10⁹)}}; one occurrence
               per frame, id <feature>-<k:08d>,
               native_ref = str(index)                       §6.5, §6.6, D3, D8
task events    topic task_index: one at every change of task_index (frame 0
               first), values {task, task_index} from meta/tasks.parquet   D13
transitions    selected next.* features: one occurrence per frame, value verbatim,
               no event derived                              D9
producer       sceneops.lerobot_episode_builder v2; fingerprint over the normalized
               build configuration and {source_kind: content, content_id,
               content_checksum}                             ADR-007 §15.2, §4
```

The platform state is read back read-only (FastAPI + object store) and held to
the derivation; every negative control applies a consistent fault to a copy of
a passing observation and must be detected.
"""

from __future__ import annotations

import copy
import fnmatch
import hashlib
import json
import struct
import urllib.parse
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from . import rules
from .findings import Findings

MANIFEST_SCHEMA = "sceneops.episode_manifest/v4"
CONTENT_ID_SCHEMA = "sceneops.content_id/v1"
CLOCK = "lerobot.frame_time"
CLOCK_DOMAINS = [{"clock": CLOCK, "origin": "unit", "resolution_ns": 1}]
PRODUCER_ID = "sceneops.lerobot_episode_builder"
SEMANTICS_VERSION = 2
VIDEO_MEDIA_TYPE = "video/mp4"
TASK_TOPIC = "task_index"
ROLE_LISTS = {
    "observation": "observations",
    "state": "states",
    "action": "actions",
    "event": "events",
    "transition": "transitions",
}
OBSERVATION_SCHEMA = "sceneops.lerobot_episode_observation/1"


def float32(value: float) -> float:
    return struct.unpack("<f", struct.pack("<f", value))[0]


def frame_time(k: int, fps: int) -> int:
    """round_half_even(k · 10⁹ / fps): Python rounds a Fraction half to even."""
    return round(Fraction(k * 10**9, fps))


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


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _rows(path: Path) -> list[dict[str, Any]]:
    return pq.read_table(path).to_pylist()


@dataclass
class RawLeRobot:
    root: Path
    files: list[dict[str, Any]]
    info: dict[str, Any]
    tasks: dict[int, str]
    episodes: list[dict[str, Any]]
    _frames: dict[str, list[dict[str, Any]]] = field(default_factory=dict, repr=False)

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

    def file(self, path: str) -> dict[str, Any] | None:
        return next((f for f in self.files if f["path"] == path), None)

    def frames(self, episode: dict[str, Any]) -> list[dict[str, Any]]:
        path = self.info["data_path"].format(
            chunk_index=episode["data/chunk_index"],
            file_index=episode["data/file_index"],
        )
        if path not in self._frames:
            self._frames[path] = _rows(self.root / path)
        lo, hi = episode["dataset_from_index"], episode["dataset_to_index"]
        return [r for r in self._frames[path] if lo <= r["index"] < hi]


def read_raw(root: Path, include: Iterable[str]) -> RawLeRobot:
    patterns = list(include)
    files = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        relative = path.relative_to(root).as_posix()
        if any(_matches(pattern, relative) for pattern in patterns):
            files.append(
                {
                    "path": relative,
                    "size_bytes": path.stat().st_size,
                    "sha256": _sha256_file(path),
                }
            )
    info = json.loads((root / "meta/info.json").read_text())
    tasks_table = pq.read_table(root / "meta/tasks.parquet")
    pandas = json.loads((tasks_table.schema.metadata or {})[b"pandas"])
    task_column = (
        "task" if "task" in tasks_table.column_names else pandas["index_columns"][0]
    )
    tasks = dict(
        zip(
            tasks_table.column("task_index").to_pylist(),
            tasks_table.column(task_column).to_pylist(),
            strict=True,
        )
    )
    episodes = []
    for entry in files:
        if entry["path"].startswith("meta/episodes/"):
            episodes += _rows(root / entry["path"])
    return RawLeRobot(
        root=root,
        files=files,
        info=info,
        tasks=tasks,
        episodes=sorted(episodes, key=lambda e: e["episode_index"]),
    )


# ── the documented rules ─────────────────────────────────────────────────────


def normalized_config(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "features": sorted(
            ({"feature": f["feature"], "role": f["role"]} for f in config["features"]),
            key=lambda f: f["feature"],
        ),
        "task_events": config.get("task_events", True),
    }


def _component_names(spec: dict[str, Any]) -> list[str] | None:
    if list(spec["shape"]) in ([], [1]):
        return None
    names = spec.get("names")
    if isinstance(names, dict):
        (names,) = names.values()
    return names


@dataclass
class ExpectedUnit:
    unit_key: str
    unit_id: str
    manifest: dict[str, Any]
    # Why no build is expected for this unit (raw facts the rules refuse).
    refusals: list[str] = field(default_factory=list)


def derive(
    raw: RawLeRobot, config: dict[str, Any], episodes: Iterable[int] | None = None
) -> dict[str, ExpectedUnit]:
    """Expected unit id -> expected unit, for ``episodes`` (default all)."""
    fps = raw.info["fps"]
    features = raw.info["features"]
    content_id, content_checksum = raw.content_id, raw.content_checksum
    build_config = normalized_config(config)
    source_revision = {
        "source_kind": "content",
        "content_id": content_id,
        "content_checksum": content_checksum,
    }
    producer = {
        "producer_id": PRODUCER_ID,
        "semantics_version": SEMANTICS_VERSION,
        "build_config": build_config,
        "producer_fingerprint": rules.producer_fingerprint(
            producer_id=PRODUCER_ID,
            semantics_version=SEMANTICS_VERSION,
            build_config=build_config,
            source=source_revision,
        ),
    }
    selected = {f["feature"]: f["role"] for f in config["features"]}
    streams = [
        {
            "topic": key,
            "role": role,
            "schema_name": features[key]["dtype"],
            "source_clock": CLOCK,
            "fields": []
            if features[key]["dtype"] == "video"
            else [
                {
                    "name": "value",
                    "path": key,
                    "component_names": _component_names(features[key]),
                }
            ],
            "has_payload": features[key]["dtype"] == "video",
        }
        for key, role in selected.items()
    ]
    if build_config["task_events"]:
        streams.append(
            {
                "topic": TASK_TOPIC,
                "role": "event",
                "schema_name": features[TASK_TOPIC]["dtype"],
                "source_clock": CLOCK,
                "fields": [
                    {"name": name, "path": name, "component_names": None}
                    for name in ("task", "task_index")
                ],
                "has_payload": False,
            }
        )
    streams.sort(key=lambda s: s["topic"])
    wanted = None if episodes is None else set(episodes)

    expected: dict[str, ExpectedUnit] = {}
    for episode in raw.episodes:
        index = episode["episode_index"]
        if wanted is not None and index not in wanted:
            continue
        unit_key = str(index)
        unit_id = rules.unit_id(
            domain="episode",
            source_kind="content",
            source_id=content_id,
            unit_key=unit_key,
        )
        frames = raw.frames(episode)
        refusals = [
            f"frame {k}: frame_index {r['frame_index']}, timestamp {r['timestamp']!r}"
            for k, r in enumerate(frames)
            if r["frame_index"] != k or r["timestamp"] != float32(k / fps)
        ]
        lists: dict[str, list[dict[str, Any]]] = {
            name: [] for name in ROLE_LISTS.values()
        }
        previous_task = None
        for k, row in enumerate(frames):
            t = frame_time(k, fps)
            for key, role in selected.items():
                occurrence = {
                    "occurrence_id": f"{key}-{k:08d}",
                    "topic": key,
                    "timestamp_ns": t,
                    "values": {},
                    "payload": None,
                    "native_ref": str(row["index"]),
                }
                if features[key]["dtype"] == "video":
                    path = raw.info["video_path"].format(
                        video_key=key,
                        chunk_index=episode[f"videos/{key}/chunk_index"],
                        file_index=episode[f"videos/{key}/file_index"],
                    )
                    entry = raw.file(path) or {
                        "path": path,
                        "sha256": None,
                        "size_bytes": None,
                    }
                    position = Fraction(
                        episode[f"videos/{key}/from_timestamp"]
                    ) + Fraction(k, fps)
                    occurrence["payload"] = {
                        "uri": rules.content_file_uri(content_id, path),
                        "checksum": entry["sha256"],
                        "size_bytes": entry["size_bytes"],
                        "media_type": VIDEO_MEDIA_TYPE,
                        "selector": {
                            "kind": "video_frame",
                            "timestamp_ns": round(position * 10**9),
                        },
                    }
                else:
                    occurrence["values"] = {"value": row[key]}
                lists[ROLE_LISTS[role]].append(occurrence)
            if build_config["task_events"] and row["task_index"] != previous_task:
                lists["events"].append(
                    {
                        "occurrence_id": f"{TASK_TOPIC}-{k:08d}",
                        "topic": TASK_TOPIC,
                        "timestamp_ns": t,
                        "values": {
                            "task": raw.tasks[row["task_index"]],
                            "task_index": row["task_index"],
                        },
                        "payload": None,
                        "native_ref": str(row["index"]),
                    }
                )
            previous_task = row["task_index"]
        length = episode["length"]
        manifest = {
            "schema_version": MANIFEST_SCHEMA,
            "lineage": {
                "source": {
                    "source_kind": "content",
                    "content_id": content_id,
                    "content_checksum": content_checksum,
                    "source_clock": CLOCK,
                    "start_timestamp_ns": 0,
                    "end_timestamp_ns": frame_time(length - 1, fps) + 1,
                    "unit_key": unit_key,
                },
                "producer": producer,
            },
            "clock_domains": CLOCK_DOMAINS,
            "frame_timeline": {"clock": CLOCK, "fps": fps, "frame_count": length},
            "streams": streams,
            **{
                name: sorted(
                    rows,
                    key=lambda o: (o["topic"], o["timestamp_ns"], o["occurrence_id"]),
                )
                for name, rows in lists.items()
            },
        }
        expected[unit_id] = ExpectedUnit(unit_key, unit_id, manifest, refusals)
    return expected


def summary(raw: RawLeRobot, expected: dict[str, ExpectedUnit]) -> dict[str, Any]:
    """Counts the M3 plan states for pusht, from the derivation."""
    manifests = [u.manifest for u in expected.values()]

    def values(topic: str) -> list[Any]:
        return [
            o["values"]["value"]
            for m in manifests
            for o in m["transitions"]
            if o["topic"] == topic
        ]

    return {
        "content_id": raw.content_id,
        "file_count": len(raw.files),
        "total_bytes": sum(f["size_bytes"] for f in raw.files),
        "episodes": len(manifests),
        "occurrences": {
            role: sum(len(m[role]) for m in manifests) for role in ROLE_LISTS.values()
        },
        "next.done true": sum(1 for v in values("next.done") if v),
        "next.reward > 0": sum(1 for v in values("next.reward") if v > 0),
        "next.success true": sum(1 for v in values("next.success") if v),
        "refused units": sorted(u.unit_key for u in expected.values() if u.refusals),
    }


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


def collect(
    api: Callable[[str], Any],
    store: Any,
    *,
    snapshot_id: str,
    dataset_key: str | None = None,
) -> dict[str, Any]:
    """Read-only: the snapshot, its content and inventory record, every Episode
    of the content with its revisions and manifest bytes, the stored size of
    every referenced content file, and optionally one DatasetVersion."""
    from .observation import _paged, snake

    obs = empty_observation()
    snapshot = snake(
        (api(f"/source-snapshots/{snapshot_id}") or {}).get("sourceSnapshot")
    )
    obs["snapshot"] = snapshot
    if not snapshot:
        return obs
    content_id = snapshot["content_id"]
    obs["content"] = snake(
        (api(f"/source-contents/{content_id}") or {}).get("sourceContent")
    )
    if obs["content"]:
        obs["inventory"] = snake(
            (api(f"/artifacts/{obs['content']['inventory_artifact_id']}") or {}).get(
                "artifact"
            )
        )
    referenced: set[str] = set()
    for unit in _paged(
        api, f"/episodes?content_id={urllib.parse.quote(content_id)}", "episodes"
    ):
        unit = snake(unit)
        detail = snake(api(f"/episodes/{unit['episode_id']}") or {})
        obs["units"][unit["episode_id"]] = {
            "record": unit,
            "revisions": detail.get("revisions", []),
        }
        for revision in detail.get("revisions", []):
            artifact_id = revision["manifest_artifact_id"]
            artifact = (
                snake((api(f"/artifacts/{artifact_id}") or {}).get("artifact")) or {}
            )
            data = store.get(artifact["uri"]) if artifact.get("uri") else None
            obs["manifests"][artifact_id] = {
                "artifact": artifact,
                "bytes": None if data is None else data.decode("utf-8"),
            }
            if data is not None:
                manifest = json.loads(data)
                referenced |= {
                    path
                    for o in manifest.get("observations", [])
                    if (
                        path := rules.content_file_path_of(
                            o.get("payload"), content_id
                        )
                    )
                    is not None
                }
    inventory_uri = (obs["inventory"] or {}).get("uri")
    if inventory_uri:
        base = inventory_uri.removesuffix("inventory.json") + "files/"
        obs["content_objects"] = {
            path: store.head(base + path) for path in sorted(referenced)
        }
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
                        "domain": "episode",
                        "unit_id": r["episode_id"],
                        "manifest_artifact_id": r["manifest_artifact_id"],
                        "manifest_checksum": r["manifest_checksum"],
                    }
                    for r in map(snake, _paged(api, f"{base}/episodes", "episodes"))
                ],
            }
    return obs


def synthetic_observation(
    raw: RawLeRobot,
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
        "format": "lerobot",
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
        data = rules.canonical_json(unit.manifest)
        checksum = rules.checksum(data)
        artifact_id = rules.manifest_artifact_id(
            unit_id=unit.unit_id, manifest_checksum=checksum
        )
        obs["manifests"][artifact_id] = {
            "artifact": {
                "artifact_id": artifact_id,
                "kind": "episode_manifest",
                "checksum": checksum,
                "size_bytes": len(data),
                "owner_type": "episode",
                "owner_id": unit.unit_id,
            },
            "bytes": data.decode("utf-8"),
        }
        obs["units"][unit.unit_id] = {
            "record": {
                "episode_id": unit.unit_id,
                "source_kind": "content",
                "robot_run_id": None,
                "content_id": content_id,
                "unit_key": unit.unit_key,
            },
            "revisions": [
                {
                    "episode_id": unit.unit_id,
                    "source_kind": "content",
                    "robot_run_id": None,
                    "content_id": content_id,
                    "unit_key": unit.unit_key,
                    "manifest_artifact_id": artifact_id,
                    "manifest_checksum": checksum,
                    **projection(unit.manifest),
                }
            ],
        }
        members.append(
            {
                "domain": "episode",
                "unit_id": unit.unit_id,
                "manifest_artifact_id": artifact_id,
                "manifest_checksum": checksum,
            }
        )
        for o in unit.manifest["observations"]:
            path = rules.content_file_path_of(o.get("payload"), raw.content_id)
            if path is not None:
                obs["content_objects"][path] = o["payload"]["size_bytes"]
    if dataset_key:
        obs["dataset_version"] = {
            "key": dataset_key,
            "version": {
                "composition_checksum": rules.composition_checksum(
                    (m["domain"], m["unit_id"], m["manifest_checksum"]) for m in members
                ),
                "episode": {"episode_count": len(members)},
                "scene": None,
            },
            "members": members,
        }
    return obs


def projection(manifest: dict[str, Any]) -> dict[str, Any]:
    """The revision columns a manifest determines (episode-domain.md)."""
    source = manifest["lineage"]["source"]
    rows: dict[str, Any] = {
        "producer_fingerprint": manifest["lineage"]["producer"]["producer_fingerprint"],
        "window_clock": source["source_clock"],
        "window_start_timestamp_ns": source["start_timestamp_ns"],
        "window_end_timestamp_ns": source["end_timestamp_ns"],
    }
    for role, name in ROLE_LISTS.items():
        rows[f"{role}_topics"] = sorted({o["topic"] for o in manifest[name]})
        rows[f"{role}_count"] = len(manifest[name])
    return rows


# ── checks ───────────────────────────────────────────────────────────────────


def _first_difference(observed: list[Any], expected: list[Any]) -> str:
    for i, (a, b) in enumerate(zip(observed, expected, strict=False)):
        if a != b:
            return f"first difference at {i}: observed {a!r}, expected {b!r}"[:400]
    return f"{len(observed)} observed, {len(expected)} expected"


def _float32_exact(values: Iterable[Any]) -> bool:
    for value in values:
        items = value if isinstance(value, list) else [value]
        for v in items:
            if isinstance(v, float) and float32(v) != v:
                return False
    return True


def check_manifest(f: Findings, manifest: dict[str, Any], unit: ExpectedUnit) -> None:
    exp = unit.manifest
    f.equal(
        manifest.get("schema_version"),
        MANIFEST_SCHEMA,
        "manifest_schema",
        "schema_version",
    )
    source = manifest.get("lineage", {}).get("source", {})
    exp_source = exp["lineage"]["source"]
    f.equal(
        {
            k: source.get(k)
            for k in ("source_kind", "content_id", "content_checksum", "unit_key")
        },
        {
            k: exp_source[k]
            for k in ("source_kind", "content_id", "content_checksum", "unit_key")
        },
        "source_binding",
        "lineage source binding",
    )
    f.equal(
        [
            source.get(k)
            for k in ("source_clock", "start_timestamp_ns", "end_timestamp_ns")
        ],
        [
            exp_source[k]
            for k in ("source_clock", "start_timestamp_ns", "end_timestamp_ns")
        ],
        "window",
        "episode window",
    )
    f.equal(
        manifest.get("lineage", {}).get("producer"),
        exp["lineage"]["producer"],
        "producer_fingerprint",
        "producer",
    )
    f.equal(
        manifest.get("clock_domains"),
        exp["clock_domains"],
        "clock_domains",
        "clock domains",
    )
    f.equal(
        manifest.get("frame_timeline"),
        exp["frame_timeline"],
        "frame_timeline",
        "frame timeline",
    )
    f.equal(manifest.get("streams"), exp["streams"], "episode_streams", "streams")
    for name in ROLE_LISTS.values():
        observed, expected = manifest.get(name) or [], exp[name]
        topics = sorted(
            {o["topic"] for o in expected} | {o.get("topic") for o in observed}
        )
        for topic in topics:
            got = [o for o in observed if o.get("topic") == topic]
            want = [o for o in expected if o["topic"] == topic]
            keyed = [
                (o.get("occurrence_id"), o.get("timestamp_ns"), o.get("native_ref"))
                for o in got
            ]
            exp_keyed = [
                (o["occurrence_id"], o["timestamp_ns"], o["native_ref"]) for o in want
            ]
            if not f.check(
                keyed == exp_keyed,
                "episode_occurrences",
                f"{name} {topic} ids, timestamps and native refs: "
                + _first_difference(keyed, exp_keyed),
            ):
                continue
            code = "transition_values" if name == "transitions" else "episode_values"
            values = [o.get("values") for o in got]
            f.check(
                values == [o["values"] for o in want]
                and _float32_exact(v.get("value") for v in values if v),
                code,
                f"{name} {topic} values: "
                + _first_difference(values, [o["values"] for o in want]),
            )
            payloads = [o.get("payload") for o in got]
            f.check(
                payloads == [o["payload"] for o in want],
                "source_file_ref",
                f"{name} {topic} payloads: "
                + _first_difference(payloads, [o["payload"] for o in want]),
            )
    known = {
        "schema_version",
        "lineage",
        "clock_domains",
        "frame_timeline",
        "streams",
        *ROLE_LISTS.values(),
    }
    f.equal(
        sorted(set(manifest) - known),
        [],
        "manifest_forbidden_field",
        "manifest members",
    )


def verify(
    f: Findings,
    obs: dict[str, Any],
    raw: RawLeRobot,
    expected: dict[str, ExpectedUnit],
    *,
    declaration: dict[str, Any],
    all_units: bool,
) -> None:
    """``expected``: the derivation of the verified units; ``all_units``: the
    content's unit set must be exactly ``expected`` (otherwise a superset)."""
    snapshot = obs.get("snapshot") or {}
    if not f.check(bool(snapshot), "snapshot", "the snapshot is not registered"):
        return
    f.equal(
        {
            k: snapshot.get(k)
            for k in ("format", "upstream", "upstream_revision", "content_id")
        },
        {
            **{k: declaration[k] for k in ("format", "upstream", "upstream_revision")},
            "content_id": raw.content_id,
        },
        "snapshot",
        "snapshot declaration and its content",
    )
    content = obs.get("content") or {}
    f.equal(
        [
            content.get(k)
            for k in ("content_id", "content_checksum", "file_count", "total_bytes")
        ],
        [
            raw.content_id,
            raw.content_checksum,
            len(raw.files),
            sum(x["size_bytes"] for x in raw.files),
        ],
        "content",
        "registered content vs the raw tree",
    )

    units = obs.get("units", {})
    if all_units:
        f.equal(
            sorted(units), sorted(expected), "unit_set", "Episode units of the content"
        )
    else:
        f.check(
            set(expected) <= set(units),
            "unit_set",
            f"missing units {sorted(set(expected) - set(units))[:5]}",
        )
    # JSON tokens: a declaration value or a descriptor member name, quoted, so
    # a value never matches inside another string.
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
            g.check(
                unit_id not in units,
                "refused_unit",
                f"unit {unit.unit_key} was built: {unit.refusals[:3]}",
            )
            continue
        observed = units.get(unit_id)
        if observed is None:
            continue
        record = observed["record"]
        g.equal(
            {
                k: record.get(k)
                for k in (
                    "episode_id",
                    "source_kind",
                    "robot_run_id",
                    "content_id",
                    "unit_key",
                )
            },
            {
                "episode_id": unit_id,
                "source_kind": "content",
                "robot_run_id": None,
                "content_id": raw.content_id,
                "unit_key": unit.unit_key,
            },
            "unit_identity",
            "unit record",
        )
        revisions = observed["revisions"]
        if not g.equal(len(revisions), 1, "revision_count", "revisions per unit"):
            continue
        revision = revisions[0]
        entry = obs["manifests"].get(revision.get("manifest_artifact_id")) or {}
        data = entry.get("bytes")
        if not g.check(
            data is not None, "manifest_object", "manifest bytes not readable"
        ):
            continue
        raw_bytes = data.encode("utf-8")
        checksum = rules.checksum(raw_bytes)
        artifact = entry.get("artifact", {})
        g.equal(
            [
                artifact.get("kind"),
                artifact.get("checksum"),
                artifact.get("size_bytes"),
                revision.get("manifest_checksum"),
            ],
            ["episode_manifest", checksum, len(raw_bytes), checksum],
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
        g.equal(
            raw_bytes,
            rules.canonical_json(manifest),
            "manifest_canonical",
            "canonical bytes",
        )
        named = [token for token in forbidden if token in data]
        g.check(
            not named,
            "manifest_forbidden_field",
            f"the manifest names snapshot, upstream or descriptor data: {named}",
        )
        check_manifest(g, manifest, unit)
        g.equal(
            {k: revision.get(k) for k in projection(unit.manifest)},
            projection(unit.manifest),
            "revision_projection",
            "revision projection",
        )

    # Every referenced preserved file is the raw file and is stored with its size.
    for path, stored in sorted((obs.get("content_objects") or {}).items()):
        entry = raw.file(path)
        f.check(entry is not None, "source_file_ref", f"{path} is not a raw file")
        f.equal(
            stored,
            entry and entry["size_bytes"],
            "source_file_object",
            f"stored size of {path}",
        )

    version = obs.get("dataset_version")
    if version is not None:
        members = version["members"]
        pins = sorted((m["unit_id"], m["manifest_checksum"]) for m in members)
        registered = sorted(
            (unit_id, r["manifest_checksum"])
            for unit_id, u in units.items()
            if unit_id in expected
            for r in u["revisions"]
        )
        f.equal(pins, registered, "composition_members", "DatasetVersion members")
        f.equal(
            version["version"].get("composition_checksum"),
            rules.composition_checksum(
                (m["domain"], m["unit_id"], m["manifest_checksum"]) for m in members
            ),
            "composition_checksum",
            "composition checksum from the pins",
        )
        f.equal(
            (version["version"].get("episode") or {}).get("episode_count"),
            len(expected),
            "composition_summary",
            "episode_count",
        )


# ── negative controls ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Control:
    name: str
    fault: str
    codes: frozenset[str]
    apply: Callable[[dict[str, Any]], None]


def _first_unit(obs: dict[str, Any]) -> str:
    return min(obs["units"], key=lambda u: int(obs["units"][u]["record"]["unit_key"]))


def rewrite(obs: dict[str, Any], mutate: Callable[[dict[str, Any]], None]) -> None:
    """A consistent fault on the first unit's manifest."""
    rewrite_unit(obs, _first_unit(obs), mutate)


def rewrite_unit(
    obs: dict[str, Any], unit_id: str, mutate: Callable[[dict[str, Any]], None]
) -> None:
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
    artifact_id = rules.manifest_artifact_id(
        unit_id=unit_id, manifest_checksum=checksum
    )
    entry["bytes"] = data.decode("utf-8")
    entry["artifact"] = {
        **entry["artifact"],
        "artifact_id": artifact_id,
        "checksum": checksum,
        "size_bytes": len(data),
    }
    obs["manifests"][artifact_id] = entry
    revision.update(
        manifest_artifact_id=artifact_id,
        manifest_checksum=checksum,
        **projection(manifest),
    )
    version = obs.get("dataset_version")
    if version is not None:
        for member in version["members"]:
            if member["unit_id"] == unit_id:
                member.update(
                    manifest_artifact_id=artifact_id, manifest_checksum=checksum
                )
        version["version"]["composition_checksum"] = rules.composition_checksum(
            (m["domain"], m["unit_id"], m["manifest_checksum"])
            for m in version["members"]
        )


def _refingerprint(manifest: dict[str, Any]) -> None:
    source = manifest["lineage"]["source"]
    producer = manifest["lineage"]["producer"]
    producer["producer_fingerprint"] = rules.producer_fingerprint(
        producer_id=producer["producer_id"],
        semantics_version=producer["semantics_version"],
        build_config=producer["build_config"],
        source={
            k: source[k] for k in ("source_kind", "content_id", "content_checksum")
        },
    )


def _shift(manifest: dict[str, Any]) -> None:
    manifest["states"][len(manifest["states"]) // 2]["timestamp_ns"] += 1


def _origin(manifest: dict[str, Any]) -> None:
    manifest["clock_domains"][0]["origin"] = "unix_epoch"


def _absolute(manifest: dict[str, Any]) -> None:
    """A relative timeline given an absolute anchor (the epoch of the
    dataset's upload), consistently: window and every timestamp."""
    anchor = 1_700_000_000_000_000_000
    source = manifest["lineage"]["source"]
    source["start_timestamp_ns"] += anchor
    source["end_timestamp_ns"] += anchor
    for name in ROLE_LISTS.values():
        for o in manifest[name]:
            o["timestamp_ns"] += anchor


def _vector_order(manifest: dict[str, Any]) -> None:
    value = manifest["states"][1]["values"]["value"]
    value.reverse()


def _transition(manifest: dict[str, Any]) -> None:
    for o in manifest["transitions"]:
        if o["topic"] == "next.done":
            o["values"]["value"] = not o["values"]["value"]
            return


def _drop(manifest: dict[str, Any]) -> None:
    manifest["actions"].pop()


def _content_binding(manifest: dict[str, Any]) -> None:
    other_checksum = rules.checksum(b"other content")
    other_id = (
        "content-"
        + rules.sha256_hex(
            rules.canonical_json(
                {
                    "content_id_schema": CONTENT_ID_SCHEMA,
                    "content_checksum": other_checksum,
                }
            )
        )[:32]
    )
    own_id = manifest["lineage"]["source"]["content_id"]
    manifest["lineage"]["source"].update(
        content_id=other_id, content_checksum=other_checksum
    )
    for o in manifest["observations"]:
        path = rules.content_file_path_of(o["payload"], own_id)
        o["payload"]["uri"] = rules.content_file_uri(other_id, path)
    _refingerprint(manifest)


def _file_checksum(manifest: dict[str, Any]) -> None:
    for o in manifest["observations"]:
        o["payload"]["checksum"] = rules.checksum(b"another video")


def _frame_selector(manifest: dict[str, Any]) -> None:
    manifest["observations"][-1]["payload"]["selector"]["timestamp_ns"] += 1


def _missing_file(obs: dict[str, Any]) -> None:
    for path in obs["content_objects"]:
        obs["content_objects"][path] = None


def _snapshot_in_manifest(manifest: dict[str, Any]) -> None:
    manifest["lineage"]["snapshot_id"] = "snapshot-" + "0" * 32


def controls() -> list[Control]:
    def on_manifest(mutate):
        return lambda obs: rewrite(obs, mutate)

    return [
        Control(
            "timestamp_drift",
            "one state 1 ns off its frame",
            frozenset({"episode_occurrences"}),
            on_manifest(_shift),
        ),
        Control(
            "clock_origin",
            "the frame clock declared unix_epoch",
            frozenset({"clock_domains"}),
            on_manifest(_origin),
        ),
        Control(
            "absolute_anchor",
            "unit-relative time given an absolute anchor",
            frozenset({"window", "episode_occurrences"}),
            on_manifest(_absolute),
        ),
        Control(
            "vector_order",
            "one state vector's components reversed",
            frozenset({"episode_values"}),
            on_manifest(_vector_order),
        ),
        Control(
            "transition_value",
            "one next.done value flipped",
            frozenset({"transition_values"}),
            on_manifest(_transition),
        ),
        Control(
            "dropped_occurrence",
            "the last action dropped",
            frozenset({"episode_occurrences"}),
            on_manifest(_drop),
        ),
        Control(
            "content_binding",
            "bound to another content, fingerprint recomputed",
            frozenset({"source_binding", "producer_fingerprint"}),
            on_manifest(_content_binding),
        ),
        Control(
            "source_file_checksum",
            "a video reference pins the sha256 of other bytes",
            frozenset({"source_file_ref"}),
            on_manifest(_file_checksum),
        ),
        Control(
            "frame_selector",
            "a video frame selector 1 ns off",
            frozenset({"source_file_ref"}),
            on_manifest(_frame_selector),
        ),
        Control(
            "missing_native_file",
            "the referenced video absent from the store",
            frozenset({"source_file_object"}),
            _missing_file,
        ),
        Control(
            "snapshot_in_manifest",
            "a snapshot id in the manifest",
            frozenset({"manifest_forbidden_field"}),
            on_manifest(_snapshot_in_manifest),
        ),
    ]


def run_controls(
    obs: dict[str, Any], verify_copy: Callable[[dict[str, Any]], set[str]]
) -> list[dict[str, Any]]:
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
