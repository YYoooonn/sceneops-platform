"""Native nuScenes labels verified from the raw source (ADR-015 §9, §12, R5).

Expectations are derived from the raw nuScenes v1.0 tables with this module's
own json reading and the documented rules (derived-layer.md "Label sets",
ADR-015 §9, M3.0 OQ-6), implemented here a second time and never imported
from SceneOps:

```text
content     content_id from the raw selection (nuscenes_scene.RawNuScenes)  ADR-015 §5
anchor      one per sample of the unit (sample.scene_token = unit key):
            {source_kind content, source_id content_id, channel LIDAR_TOP,
             source_clock nuscenes.timestamp, timestamp_ns sample.timestamp · 1 000,
             group_ref sample.token}; the sample's one LIDAR_TOP key frame is at
            sample.timestamp and its file is preserved          OQ-6
label       one per sample_annotation of the sample: label_id = token,
            instance_id = instance_token, category = category.name (through the
            instance), attributes = attribute names sorted, box {frame_id global,
            center_m translation, size_wlh_m size, rotation_wxyz rotation,
            velocity_mps null}, numbers as floats
document    {schema_version sceneops.label_set/v2, label_set_id, provenance
            {kind external, producer nuscenes, producer_version <table version>,
            model_id null, model_version null}, coverage sorted by (source_kind,
            source_id, channel, source_clock, timestamp_ns, group_ref), labels
            sorted by (anchor, label_id)}; checksum = sha256(canonical JSON)
artifact    label_set_manifest owned by (label_set, label_set_id), id =
            labelset-<sha256(canonical_json({derived_artifact_id_schema,
            prefix labelset, logical_id label_set_id, checksum}))[:32]>
resolution  on the platform's Scene of the unit, without a sample view: the one
            LIDAR_TOP observation at the anchor is the raw key frame
            (native_ref = its sample_data.token), inside the group whose native_ref
            is group_ref
views       a sample view of the Scene that pins the label set presents, for
            every keyframe group, exactly the raw sample's annotation tokens on
            the one sample that holds the group; views anchored on different
            channels agree
```

The platform state is read back read-only (FastAPI + object store); every
negative control applies a consistent fault to a copy of a passing
observation and must be detected.
"""

from __future__ import annotations

import copy
import hashlib
import json
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from . import rules
from .findings import Findings
from .nuscenes_scene import RawNuScenes

LABEL_SET_SCHEMA = "sceneops.label_set/v2"
ANCHOR_CHANNEL = "LIDAR_TOP"
CLOCK = "nuscenes.timestamp"
FRAME = "global"
PRODUCER = "nuscenes"
ANNOTATION_TABLES = ("sample_annotation", "instance", "category", "attribute")
DERIVED_ARTIFACT_ID_SCHEMA = "sceneops.derived_artifact_id/v1"
DOCUMENT_KEYS = {"schema_version", "label_set_id", "provenance", "coverage", "labels"}
OBSERVATION_SCHEMA = "sceneops.nuscenes_label_observation/1"


def derived_artifact_id(*, prefix: str, logical_id: str, checksum: str) -> str:
    document = {
        "derived_artifact_id_schema": DERIVED_ARTIFACT_ID_SCHEMA,
        "prefix": prefix,
        "logical_id": logical_id,
        "checksum": checksum,
    }
    return f"{prefix}-{rules.sha256_hex(rules.canonical_json(document))[:32]}"


def anchor_key(anchor: dict[str, Any]) -> tuple:
    return (
        anchor["source_kind"],
        anchor["source_id"],
        anchor["channel"],
        anchor["source_clock"],
        anchor["timestamp_ns"],
        anchor.get("group_ref") or "",
    )


# ── the documented rules ─────────────────────────────────────────────────────


@dataclass
class ExpectedLabels:
    label_set_id: str
    content_id: str
    unit_keys: list[str]
    document: dict[str, Any]
    checksum: str
    artifact_id: str
    # sample token -> sorted annotation tokens; key frame sample_data token; t_ns
    by_group: dict[str, list[str]]
    key_frame: dict[str, str]
    sample_ns: dict[str, int]
    refusals: list[str] = field(default_factory=list)


def _annotation_tables(raw: RawNuScenes) -> dict[str, list[dict[str, Any]]]:
    return {
        name: json.loads((raw.root / raw.version / f"{name}.json").read_text())
        for name in ANNOTATION_TABLES
    }


def derive(raw: RawNuScenes, *, label_set_id: str, unit_keys: list[str]) -> ExpectedLabels:
    tables = _annotation_tables(raw)
    instances = {r["token"]: r for r in tables["instance"]}
    categories = {r["token"]: r for r in tables["category"]}
    attributes = {r["token"]: r for r in tables["attribute"]}
    annotations: dict[str, list[dict[str, Any]]] = {}
    for row in tables["sample_annotation"]:
        annotations.setdefault(row["sample_token"], []).append(row)
    preserved = {f["path"] for f in raw.files}
    content_id = raw.content_id
    scenes = raw.index("scene")
    refusals: list[str] = []
    coverage, labels = [], []
    by_group: dict[str, list[str]] = {}
    key_frame: dict[str, str] = {}
    sample_ns: dict[str, int] = {}
    for unit_key in sorted(set(unit_keys)):
        if unit_key not in scenes:
            refusals.append(f"unit {unit_key} is not a scene of the content")
            continue
        samples = sorted(
            (s for s in raw.tables["sample"] if s["scene_token"] == unit_key),
            key=lambda s: (s["timestamp"], s["token"]),
        )
        for sample in samples:
            lidar = [
                r
                for r in raw.tables["sample_data"]
                if r["sample_token"] == sample["token"]
                and r["is_key_frame"]
                and raw.channel(r)["channel"] == ANCHOR_CHANNEL
            ]
            if len(lidar) != 1 or lidar[0]["timestamp"] != sample["timestamp"]:
                refusals.append(f"sample {sample['token']}: no LIDAR_TOP key frame at its time")
                continue
            if lidar[0]["filename"] not in preserved:
                refusals.append(f"sample {sample['token']}: key frame file not preserved")
                continue
            anchor = {
                "source_kind": "content",
                "source_id": content_id,
                "channel": ANCHOR_CHANNEL,
                "source_clock": CLOCK,
                "timestamp_ns": sample["timestamp"] * 1000,
                "group_ref": sample["token"],
            }
            coverage.append(anchor)
            key_frame[sample["token"]] = lidar[0]["token"]
            sample_ns[sample["token"]] = anchor["timestamp_ns"]
            tokens = []
            for ann in annotations.get(sample["token"], []):
                instance = instances[ann["instance_token"]]
                labels.append(
                    {
                        "label_id": ann["token"],
                        "anchor": anchor,
                        "category": categories[instance["category_token"]]["name"],
                        "instance_id": ann["instance_token"],
                        "box": {
                            "frame_id": FRAME,
                            "center_m": [float(v) for v in ann["translation"]],
                            "size_wlh_m": [float(v) for v in ann["size"]],
                            "rotation_wxyz": [float(v) for v in ann["rotation"]],
                            "velocity_mps": None,
                        },
                        "attributes": sorted(
                            attributes[a]["name"] for a in ann["attribute_tokens"]
                        ),
                    }
                )
                tokens.append(ann["token"])
            by_group[sample["token"]] = sorted(tokens)
    document = {
        "schema_version": LABEL_SET_SCHEMA,
        "label_set_id": label_set_id,
        "provenance": {
            "kind": "external",
            "producer": PRODUCER,
            "producer_version": raw.version,
            "model_id": None,
            "model_version": None,
        },
        "coverage": sorted(coverage, key=anchor_key),
        "labels": sorted(labels, key=lambda label: (anchor_key(label["anchor"]), label["label_id"])),
    }
    checksum = rules.checksum(rules.canonical_json(document))
    return ExpectedLabels(
        label_set_id=label_set_id,
        content_id=content_id,
        unit_keys=sorted(set(unit_keys)),
        document=document,
        checksum=checksum,
        artifact_id=derived_artifact_id(
            prefix="labelset", logical_id=label_set_id, checksum=checksum
        ),
        by_group=by_group,
        key_frame=key_frame,
        sample_ns=sample_ns,
        refusals=refusals,
    )


def summary(expected: ExpectedLabels) -> dict[str, Any]:
    categories: dict[str, int] = {}
    for label in expected.document["labels"]:
        categories[label["category"]] = categories.get(label["category"], 0) + 1
    return {
        "content_id": expected.content_id,
        "unit_keys": expected.unit_keys,
        "anchor_count": len(expected.document["coverage"]),
        "label_count": len(expected.document["labels"]),
        "instance_count": len({label["instance_id"] for label in expected.document["labels"]}),
        "category_count": len(categories),
        "empty_sample_count": sum(1 for v in expected.by_group.values() if not v),
        "checksum": expected.checksum,
        "artifact_id": expected.artifact_id,
        "refusals": expected.refusals,
    }


# ── the observed platform state ──────────────────────────────────────────────


def empty_observation() -> dict[str, Any]:
    return {
        "schema": OBSERVATION_SCHEMA,
        "snapshot": None,
        "label_set": None,
        "scene": None,
        "views": [],
    }


def _entry(api: Callable[[str], Any], store: Any, artifact_id: str) -> dict[str, Any]:
    from .observation import snake

    artifact = snake((api(f"/artifacts/{artifact_id}") or {}).get("artifact")) or {}
    data = store.get(artifact["uri"]) if artifact.get("uri") else None
    return {"artifact": artifact, "bytes": None if data is None else data.decode("utf-8")}


def collect(
    api: Callable[[str], Any],
    store: Any,
    *,
    snapshot_id: str,
    unit_key: str,
    label_set_artifact_id: str,
    view_artifact_ids: list[str],
) -> dict[str, Any]:
    """Read-only: the snapshot, the label set revision, the Scene of the unit
    (its revisions and their manifest bytes) and the named sample views."""
    from .observation import _paged, snake

    obs = empty_observation()
    obs["snapshot"] = snake((api(f"/source-snapshots/{snapshot_id}") or {}).get("sourceSnapshot"))
    obs["label_set"] = _entry(api, store, label_set_artifact_id)
    content_id = (obs["snapshot"] or {}).get("content_id")
    if content_id:
        for unit in _paged(api, f"/scenes?content_id={urllib.parse.quote(content_id)}", "scenes"):
            unit = snake(unit)
            if unit["unit_key"] != unit_key:
                continue
            detail = snake(api(f"/scenes/{unit['scene_id']}") or {})
            obs["scene"] = {
                "record": unit,
                "revisions": [
                    {**r, "manifest": _entry(api, store, r["manifest_artifact_id"])}
                    for r in detail.get("revisions", [])
                ],
            }
    obs["views"] = [_entry(api, store, artifact_id) for artifact_id in view_artifact_ids]
    return obs


def synthetic_observation(
    expected: ExpectedLabels, scene_manifest: dict[str, Any], *, scene_id: str
) -> dict[str, Any]:
    """The platform state the rules describe (test double): the label set as
    derived, the Scene as the Scene rules derive it, and one view per anchor
    channel presenting each group's labels on its key frame sample. Proves
    the checks, not the platform."""
    obs = empty_observation()
    obs["snapshot"] = {"snapshot_id": "snapshot-" + "6" * 32, "content_id": expected.content_id}
    data = rules.canonical_json(expected.document)
    obs["label_set"] = {
        "artifact": {
            "artifact_id": expected.artifact_id,
            "kind": "label_set_manifest",
            "owner_type": "label_set",
            "owner_id": expected.label_set_id,
            "checksum": expected.checksum,
            "size_bytes": len(data),
        },
        "bytes": data.decode("utf-8"),
    }
    scene_bytes = rules.canonical_json(scene_manifest)
    scene_checksum = rules.checksum(scene_bytes)
    scene_artifact = rules.manifest_artifact_id(unit_id=scene_id, manifest_checksum=scene_checksum)
    obs["scene"] = {
        "record": {"scene_id": scene_id, "unit_key": expected.unit_keys[0], "content_id": expected.content_id},
        "revisions": [
            {
                "scene_id": scene_id,
                "manifest_artifact_id": scene_artifact,
                "manifest_checksum": scene_checksum,
                "manifest": {
                    "artifact": {"artifact_id": scene_artifact, "checksum": scene_checksum},
                    "bytes": scene_bytes.decode("utf-8"),
                },
            }
        ],
    }
    by_channel = {o["observation_id"]: o for o in scene_manifest["observations"]}
    total = len(expected.document["labels"])
    for channel in ("CAM_FRONT", ANCHOR_CHANNEL):
        samples = []
        for rank, group in enumerate(scene_manifest["groups"]):
            (anchor,) = [i for i in group["observation_ids"] if by_channel[i]["channel"] == channel]
            samples.append(
                {
                    "sample_id": f"smp-{rank:06d}",
                    "anchor": {
                        "channel": channel,
                        "observation_id": anchor,
                        "timestamp_ns": by_channel[anchor]["timestamp_ns"],
                        "time_delta_ns": 0,
                    },
                    "members": [],
                    "labels": [
                        {
                            "label_set_id": expected.label_set_id,
                            "covered": True,
                            "label_ids": expected.by_group[group["native_ref"]],
                        }
                    ],
                }
            )
        view = {
            "scene": {
                "scene_id": scene_id,
                "manifest_artifact_id": scene_artifact,
                "manifest_checksum": scene_checksum,
            },
            "label_sets": [
                {
                    "label_set_id": expected.label_set_id,
                    "manifest_artifact_id": expected.artifact_id,
                    "manifest_checksum": expected.checksum,
                }
            ],
            "label_stats": [
                {
                    "label_set_id": expected.label_set_id,
                    "covered_observation_count": len(expected.document["coverage"]),
                    "attached_label_count": total,
                    "unattached_label_count": 0,
                }
            ],
            "samples": samples,
        }
        view_bytes = rules.canonical_json(view)
        obs["views"].append(
            {
                "artifact": {"artifact_id": f"sampleview-{channel.lower()}", "checksum": rules.checksum(view_bytes)},
                "bytes": view_bytes.decode("utf-8"),
            }
        )
    return obs


# ── checks ───────────────────────────────────────────────────────────────────


def _diff(observed: dict[str, Any], expected: dict[str, Any], keys: tuple[str, ...]) -> list[str]:
    return [k for k in keys if observed.get(k) != expected.get(k)]


def check_label_set(f: Findings, obs: dict[str, Any], expected: ExpectedLabels) -> dict[str, Any] | None:
    entry = obs.get("label_set") or {}
    artifact, text = entry.get("artifact") or {}, entry.get("bytes")
    if not f.check(text is not None, "label_set_artifact", "the label set bytes are not readable"):
        return None
    data = text.encode("utf-8")
    checksum = rules.checksum(data)
    f.equal(
        (artifact.get("kind"), artifact.get("owner_type"), artifact.get("owner_id")),
        ("label_set_manifest", "label_set", expected.label_set_id),
        "label_set_artifact",
        "label set ArtifactRecord kind / owner",
    )
    f.equal(artifact.get("checksum"), checksum, "label_set_artifact", "record checksum vs stored bytes")
    f.equal(
        artifact.get("artifact_id"),
        derived_artifact_id(prefix="labelset", logical_id=expected.label_set_id, checksum=checksum),
        "label_set_artifact",
        "artifact id of the revision",
    )
    document = json.loads(data)
    f.equal(sorted(document), sorted(DOCUMENT_KEYS), "label_set_forbidden_field", "label set fields")
    f.equal(
        sorted(document.get("provenance", {})),
        sorted(expected.document["provenance"]),
        "label_set_forbidden_field",
        "provenance fields",
    )
    f.equal(document.get("schema_version"), LABEL_SET_SCHEMA, "label_set_schema", "schema_version")
    f.equal(document.get("label_set_id"), expected.label_set_id, "label_set_schema", "label_set_id")
    f.equal(document.get("provenance"), expected.document["provenance"], "label_provenance", "provenance")
    f.equal(rules.canonical_json(document), data, "label_set_bytes", "stored bytes are canonical JSON")

    coverage = document.get("coverage", [])
    f.equal(len(coverage), len(expected.document["coverage"]), "label_coverage", "covered anchor count")
    _check_anchors(f, coverage, expected.document["coverage"], content_id=expected.content_id)

    observed_labels = {label["label_id"]: label for label in document.get("labels", [])}
    expected_labels = {label["label_id"]: label for label in expected.document["labels"]}
    missing = sorted(set(expected_labels) - set(observed_labels))
    extra = sorted(set(observed_labels) - set(expected_labels))
    f.check(
        not missing and not extra,
        "label_tokens",
        f"labels by sample_annotation token: {len(missing)} missing (e.g. {missing[:3]}), "
        f"{len(extra)} not in the source (e.g. {extra[:3]})",
    )
    f.equal(len(observed_labels), len(expected_labels), "label_tokens", "label count")
    anchor_faults, semantic, geometry, frames = [], [], [], []
    for token in sorted(set(observed_labels) & set(expected_labels)):
        o, e = observed_labels[token], expected_labels[token]
        if o.get("anchor") != e["anchor"]:
            anchor_faults.append((token, o.get("anchor") or {}, e["anchor"]))
        if _diff(o, e, ("category", "instance_id", "attributes")):
            semantic.append(token)
        box, ebox = o.get("box") or {}, e["box"]
        if _diff(box, ebox, ("center_m", "size_wlh_m", "rotation_wxyz", "velocity_mps")):
            geometry.append(token)
        if box.get("frame_id") != ebox["frame_id"]:
            frames.append(token)
    _check_anchor_faults(f, anchor_faults, what="labels", content_id=expected.content_id)
    f.check(not semantic, "label_semantics", f"category / instance / attributes differ for {len(semantic)} labels, e.g. {semantic[:3]}")
    f.check(not geometry, "label_geometry", f"box geometry differs for {len(geometry)} labels, e.g. {geometry[:3]}")
    f.check(not frames, "label_frame", f"box frame is not {FRAME!r} for {len(frames)} labels, e.g. {frames[:3]}")
    f.equal(checksum, expected.checksum, "label_set_checksum", "revision checksum vs the raw-derived document")
    return document


def _check_anchors(
    f: Findings, observed: list[dict[str, Any]], expected: list[dict[str, Any]], *, content_id: str
) -> None:
    by_time = {a["timestamp_ns"]: a for a in expected}
    faults = []
    for anchor in observed:
        match = by_time.get(anchor.get("timestamp_ns"))
        if match is None or anchor != match:
            faults.append((anchor.get("group_ref"), anchor, match))
    _check_anchor_faults(f, faults, what="coverage", content_id=content_id)


def _check_anchor_faults(
    f: Findings, faults: list[tuple[Any, dict, dict | None]], *, what: str, content_id: str
) -> None:
    """Classify anchors that differ from the raw-derived anchor (None: no raw
    sample's key frame is at the anchor's time)."""
    binding = [k for k, o, _ in faults if (o.get("source_kind"), o.get("source_id")) != ("content", content_id)]
    timing = [
        k for k, o, e in faults
        if e is None or (o.get("channel"), o.get("source_clock"), o.get("timestamp_ns")) != (e["channel"], e["source_clock"], e["timestamp_ns"])
    ]
    groups = [k for k, o, e in faults if e is not None and o.get("group_ref") != e["group_ref"]]
    other = [k for k, o, e in faults if e is not None and set(o) != set(e)]
    f.check(not binding, "label_anchor_binding", f"{what}: {len(binding)} anchors name another source, e.g. {binding[:3]}")
    f.check(not timing, "label_anchor_time", f"{what}: {len(timing)} anchors are not on a raw sample's LIDAR_TOP key frame, e.g. {timing[:3]}")
    f.check(not groups, "label_group_ref", f"{what}: {len(groups)} anchors carry another sample's token, e.g. {groups[:3]}")
    f.check(not other, "label_set_forbidden_field", f"{what}: {len(other)} anchors carry other fields, e.g. {other[:3]}")


def _scene_manifest(obs: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    scene = obs.get("scene") or {}
    revisions = scene.get("revisions") or []
    if len(revisions) != 1 or not (revisions[0].get("manifest") or {}).get("bytes"):
        return None
    return revisions[0], json.loads(revisions[0]["manifest"]["bytes"])


def check_resolution(f: Findings, obs: dict[str, Any], document: dict[str, Any], expected: ExpectedLabels) -> None:
    """Every anchor of the platform's label set, resolved on the platform's Scene
    by the documented rule, lands on the raw key frame inside its group."""
    found = _scene_manifest(obs)
    if not f.check(found is not None, "label_resolution", "the unit's Scene has not exactly one readable revision"):
        return
    _, manifest = found
    source = manifest["lineage"]["source"]
    f.equal(
        (source.get("source_kind"), source.get("content_id"), source.get("unit_key")),
        ("content", expected.content_id, expected.unit_keys[0]),
        "label_resolution",
        "the Scene's source binding",
    )
    clocks = {c["channel"]: c["source_clock"] for c in manifest["channels"]}
    at: dict[tuple[str, str, int], list[dict[str, Any]]] = {}
    for o in manifest["observations"]:
        at.setdefault((o["channel"], clocks[o["channel"]], o["timestamp_ns"]), []).append(o)
    groups = {g["native_ref"]: g for g in manifest["groups"] if g.get("native_ref")}
    wrong = []
    for anchor in document.get("coverage", []):
        if (anchor.get("source_kind"), anchor.get("source_id")) != ("content", source.get("content_id")):
            wrong.append((anchor.get("group_ref"), "names another source"))
            continue
        hits = at.get((anchor.get("channel"), anchor.get("source_clock"), anchor.get("timestamp_ns")), [])
        group = groups.get(anchor.get("group_ref"))
        if len(hits) != 1:
            wrong.append((anchor.get("group_ref"), f"{len(hits)} observations at the anchor"))
        elif group is None:
            wrong.append((anchor.get("group_ref"), "no group with that native_ref"))
        elif hits[0]["observation_id"] not in group["observation_ids"]:
            wrong.append((anchor.get("group_ref"), f"{hits[0]['observation_id']} not in {group['group_id']}"))
        elif hits[0].get("native_ref") != expected.key_frame.get(anchor.get("group_ref")):
            wrong.append((anchor.get("group_ref"), "not the raw LIDAR_TOP key frame"))
    f.check(not wrong, "label_resolution", f"{len(wrong)} anchors do not resolve in their group, e.g. {wrong[:3]}")


def check_views(f: Findings, obs: dict[str, Any], expected: ExpectedLabels) -> None:
    views = obs.get("views") or []
    found = _scene_manifest(obs)
    if not views or found is None:
        return
    revision, manifest = found
    group_of = {o: g["native_ref"] for g in manifest["groups"] for o in g["observation_ids"]}
    label_set = obs["label_set"]["artifact"]
    total = len(expected.document["labels"])
    presented = []
    for index, entry in enumerate(views):
        v = f.scoped(view=index)
        view = json.loads(entry["bytes"])
        v.equal(
            view["scene"]["manifest_checksum"], revision["manifest_checksum"],
            "view_scene_pin", "the view's Scene revision",
        )  # fmt: skip
        pins = [(p["label_set_id"], p["manifest_artifact_id"], p["manifest_checksum"]) for p in view["label_sets"]]
        v.equal(
            pins, [(expected.label_set_id, label_set.get("artifact_id"), label_set.get("checksum"))],
            "view_label_pin", "the view's label set pin",
        )  # fmt: skip
        (stats,) = view["label_stats"] or [{}]
        v.equal(
            (stats.get("attached_label_count", 0) + stats.get("unattached_label_count", 0), stats.get("covered_observation_count")),
            (total, len(expected.document["coverage"])),
            "view_label_stats", "attached + unattached labels, covered anchors",
        )  # fmt: skip
        by_group: dict[str, list[str]] = {}
        problems = []
        for sample in view["samples"]:
            (labels,) = sample["labels"]
            if not labels["covered"]:
                continue
            held = {group_of[o] for o in [sample["anchor"]["observation_id"], *(m["observation_id"] for m in sample["members"])] if o in group_of}
            if len(held) != 1:
                problems.append(f"{sample['sample_id']} covers {len(held)} groups")
                continue
            (group,) = held
            if group in by_group:
                problems.append(f"group {group} presented twice")
            by_group[group] = labels["label_ids"]
        v.check(not problems, "view_group_labels", f"{problems[:3]}")
        mismatched = sorted(g for g in set(by_group) | set(expected.by_group) if by_group.get(g) != expected.by_group.get(g))
        v.check(
            not mismatched, "view_group_labels",
            f"{len(mismatched)} keyframe groups do not present exactly their raw sample's annotations, e.g. {mismatched[:3]}",
        )  # fmt: skip
        presented.append(by_group)
    f.check(
        all(p == presented[0] for p in presented), "view_agreement",
        "views anchored on different channels present different labels per group",
    )  # fmt: skip


def verify(f: Findings, obs: dict[str, Any], expected: ExpectedLabels) -> None:
    f.check(not expected.refusals, "raw_label_rules", f"the raw source violates the label rules: {expected.refusals[:3]}")
    f.equal((obs.get("snapshot") or {}).get("content_id"), expected.content_id, "snapshot_content", "the snapshot's content")
    document = check_label_set(f, obs, expected)
    if document is not None:
        check_resolution(f, obs, document, expected)
    check_views(f, obs, expected)


# ── negative controls ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Control:
    name: str
    fault: str
    codes: frozenset[str]
    apply: Callable[[dict[str, Any]], None]


def rewrite_label_set(obs: dict[str, Any], mutate: Callable[[dict[str, Any]], None]) -> None:
    """Apply ``mutate`` to the label set and recompute what the platform derives
    from the bytes (checksum, artifact id, size) and the views' pins, so the
    result is self-consistent."""
    entry = obs["label_set"]
    document = json.loads(entry["bytes"])
    mutate(document)
    document["coverage"].sort(key=anchor_key)
    document["labels"].sort(key=lambda label: (anchor_key(label["anchor"]), label["label_id"]))
    data = rules.canonical_json(document)
    checksum = rules.checksum(data)
    artifact_id = derived_artifact_id(prefix="labelset", logical_id=document["label_set_id"], checksum=checksum)
    entry["bytes"] = data.decode("utf-8")
    entry["artifact"] = {**entry["artifact"], "artifact_id": artifact_id, "checksum": checksum, "size_bytes": len(data)}
    for view_entry in obs["views"]:
        view = json.loads(view_entry["bytes"])
        for pin in view["label_sets"]:
            pin.update(manifest_artifact_id=artifact_id, manifest_checksum=checksum)
        view_entry["bytes"] = rules.canonical_json(view).decode("utf-8")


def rewrite_view(obs: dict[str, Any], mutate: Callable[[dict[str, Any]], None]) -> None:
    entry = obs["views"][0]
    view = json.loads(entry["bytes"])
    mutate(view)
    entry["bytes"] = rules.canonical_json(view).decode("utf-8")


def rewrite_scene(obs: dict[str, Any], mutate: Callable[[dict[str, Any]], None]) -> None:
    revision = obs["scene"]["revisions"][0]
    manifest = json.loads(revision["manifest"]["bytes"])
    mutate(manifest)
    data = rules.canonical_json(manifest)
    checksum = rules.checksum(data)
    revision["manifest"]["bytes"] = data.decode("utf-8")
    revision["manifest_checksum"] = checksum
    for view_entry in obs["views"]:
        view = json.loads(view_entry["bytes"])
        view["scene"]["manifest_checksum"] = checksum
        view_entry["bytes"] = rules.canonical_json(view).decode("utf-8")


def _relabel(document: dict[str, Any], old: dict[str, Any], new: dict[str, Any]) -> None:
    document["coverage"] = [new if a == old else a for a in document["coverage"]]
    for label in document["labels"]:
        if label["anchor"] == old:
            label["anchor"] = new


def _swap_group_refs(document: dict[str, Any]) -> None:
    a, b = document["coverage"][0], document["coverage"][1]
    new_a, new_b = {**a, "group_ref": b["group_ref"]}, {**b, "group_ref": a["group_ref"]}
    _relabel(document, a, new_a)
    _relabel(document, b, new_b)


def _shift_anchor(document: dict[str, Any]) -> None:
    a = document["coverage"][len(document["coverage"]) // 2]
    _relabel(document, a, {**a, "timestamp_ns": a["timestamp_ns"] + 1})


def _other_content(document: dict[str, Any]) -> None:
    other = "content-" + hashlib.sha256(b"another content").hexdigest()[:32]
    for a in list(document["coverage"]):
        _relabel(document, a, {**a, "source_id": other})


def _recording_kind(document: dict[str, Any]) -> None:
    for a in list(document["coverage"]):
        _relabel(document, a, {**a, "source_kind": "recording"})


def _lost_label(document: dict[str, Any]) -> None:
    document["labels"].pop(len(document["labels"]) // 2)


def _lost_sample(document: dict[str, Any]) -> None:
    last = document["coverage"].pop()
    document["labels"] = [label for label in document["labels"] if label["anchor"] != last]


def _moved_label(document: dict[str, Any]) -> None:
    label = document["labels"][0]
    label["anchor"] = next(a for a in document["coverage"] if a != label["anchor"])


def _center(document: dict[str, Any]) -> None:
    document["labels"][len(document["labels"]) // 3]["box"]["center_m"][0] += 0.01


def _size_swap(document: dict[str, Any]) -> None:
    box = document["labels"][-1]["box"]
    box["size_wlh_m"][0], box["size_wlh_m"][1] = box["size_wlh_m"][1], box["size_wlh_m"][0]


def _rotation(document: dict[str, Any]) -> None:
    box = document["labels"][1]["box"]
    w, x, y, z = box["rotation_wxyz"]
    box["rotation_wxyz"] = [w, x, y, -z] if z else [-w, x, y, z]


def _frame(document: dict[str, Any]) -> None:
    for label in document["labels"]:
        label["box"]["frame_id"] = "ego"


def _category(document: dict[str, Any]) -> None:
    first = document["labels"][0]
    other = next(label for label in document["labels"] if label["category"] != first["category"])
    first["category"], other["category"] = other["category"], first["category"]


def _instance(document: dict[str, Any]) -> None:
    first = document["labels"][0]
    other = next(label for label in document["labels"] if label["instance_id"] != first["instance_id"])
    first["instance_id"], other["instance_id"] = other["instance_id"], first["instance_id"]


def _snapshot_in_label_set(document: dict[str, Any]) -> None:
    document["provenance"]["snapshot_id"] = "snapshot-" + "0" * 32


def _view_moves_labels(view: dict[str, Any]) -> None:
    covered = [s for s in view["samples"] if s["labels"][0]["label_ids"]]
    a, b = covered[0]["labels"][0], covered[1]["labels"][0]
    a["label_ids"], b["label_ids"] = b["label_ids"], a["label_ids"]


def _view_drops_label(view: dict[str, Any]) -> None:
    sample = next(s for s in view["samples"] if s["labels"][0]["label_ids"])
    sample["labels"][0]["label_ids"].pop()
    stats = view["label_stats"][0]
    stats["attached_label_count"] -= 1


def _view_pins_another_revision(view: dict[str, Any]) -> None:
    view["label_sets"][0]["manifest_checksum"] = rules.checksum(b"another revision")


def _scene_group_token_swap(manifest: dict[str, Any]) -> None:
    first, second = manifest["groups"][0], manifest["groups"][1]
    first["native_ref"], second["native_ref"] = second["native_ref"], first["native_ref"]


def controls() -> list[Control]:
    def on_labels(mutate):
        return lambda obs: rewrite_label_set(obs, mutate)

    def on_view(mutate):
        return lambda obs: rewrite_view(obs, mutate)

    return [
        Control("swapped_group_ref", "two samples' anchors exchange their sample tokens",
                frozenset({"label_group_ref", "label_resolution"}), on_labels(_swap_group_refs)),
        Control("anchor_timestamp_shift", "one anchor 1 ns off its key frame",
                frozenset({"label_anchor_time", "label_resolution"}), on_labels(_shift_anchor)),
        Control("other_content_binding", "every anchor names another content",
                frozenset({"label_anchor_binding"}), on_labels(_other_content)),
        Control("recording_binding", "every anchor names a RobotRun of the content's id",
                frozenset({"label_anchor_binding"}), on_labels(_recording_kind)),
        Control("lost_label", "one annotation dropped",
                frozenset({"label_tokens"}), on_labels(_lost_label)),
        Control("lost_sample", "the last sample dropped with its labels",
                frozenset({"label_coverage", "label_tokens"}), on_labels(_lost_sample)),
        Control("label_on_another_sample", "one label anchored on another sample",
                frozenset({"label_anchor_time", "label_group_ref"}), on_labels(_moved_label)),
        Control("box_center", "one box moved by 1 cm",
                frozenset({"label_geometry"}), on_labels(_center)),
        Control("box_size_swap", "one box's width and length exchanged",
                frozenset({"label_geometry"}), on_labels(_size_swap)),
        Control("box_rotation", "one box's yaw mirrored",
                frozenset({"label_geometry"}), on_labels(_rotation)),
        Control("box_frame", "boxes declared in the ego frame",
                frozenset({"label_frame"}), on_labels(_frame)),
        Control("category_swap", "two labels exchange categories",
                frozenset({"label_semantics"}), on_labels(_category)),
        Control("instance_swap", "two labels exchange instances",
                frozenset({"label_semantics"}), on_labels(_instance)),
        Control("snapshot_in_label_set", "a snapshot id in the label set provenance",
                frozenset({"label_set_forbidden_field"}), on_labels(_snapshot_in_label_set)),
        Control("view_labels_on_wrong_group", "a view presents two groups' labels exchanged",
                frozenset({"view_group_labels"}), on_view(_view_moves_labels)),
        Control("view_lost_label", "a view drops one attached label",
                frozenset({"view_group_labels", "view_label_stats"}), on_view(_view_drops_label)),
        Control("view_label_pin", "a view pins another label set revision",
                frozenset({"view_label_pin"}), on_view(_view_pins_another_revision)),
        Control("scene_group_token_swap", "the Scene's groups exchange sample tokens",
                frozenset({"label_resolution"}), lambda obs: rewrite_scene(obs, _scene_group_token_swap)),
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
