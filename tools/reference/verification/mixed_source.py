"""A DatasetVersion of several sources and both domains, verified against the
raw inputs (ADR-015 §3, §7, §12; M3 S5).

The composition spec (``config/reference/mixed-sources/composition.json``) names
the parts of the version: the units of one golden-contract RobotRun and the
units of preserved snapshots. Every expectation is derived here from the raw
inputs, by the rules the per-source verifiers implement, never from what the
platform holds:

```text
recording part   the fixture's locked recording -> SceneExpectation /
                 EpisodeExpectation (verification.contract); unit ids
                 f(domain, recording, run_id, unit_key), run_id from the
                 contract's run_id_template
content part     the raw tree the snapshot declaration selects -> the expected
                 manifests (verification.nuscenes_scene, verification.lerobot);
                 the pin (domain, unit_id, sha256(canonical_json(manifest))) is
                 derived whole, so the stored bytes must equal the derivation
summary          scene_count, observation_count, keyframe_count,
                 observed_channels, episode_count over every expected member
```

Checks of one composed DatasetVersion (codes):

```text
composition_members   the members are exactly the expected units, one each
member_domain         each member's domain is its unit's
pin_artifact_id       manifest_artifact_id = f(unit_id, manifest_checksum)
member_pin            the pin names a registered revision of its unit
unit_identity         the unit record's binding is the expected source and unit
                      key (a content unit names no RobotRun), and its id
                      recomputes from the binding
manifest_checksum     the stored bytes hash to the pin, the record agrees, and a
                      content member's bytes are exactly the derived manifest
manifest_canonical    canonical JSON
manifest_forbidden_field  no membership, location or snapshot field or token in
                      the bytes, including the DatasetVersion's own names
<per-source codes>    recording members: the recording verifier's lineage and
                      manifest checks; content members: the content verifier's
                      manifest checks
source_registry       each content part's content is registered with the raw
                      content checksum and declared by its snapshot declaration
robot_run             each recording part's RobotRun is registered
composition_checksum  the row's checksum equals f(member pins) and
                      f(expected pins)
composition_summary   the row's summaries equal the derived summary
selector              a recorded selector names exactly the parts' sources, one
                      clause per (part, domain), under the members' fingerprints
```

Payload objects are not read here: the per-source verifiers hold each part's
payloads and preserved files to the raw inputs.
"""

from __future__ import annotations

import copy
import json
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import checks, lerobot, nuscenes_scene, rules, synthetic
from .contract import REPO_ROOT, Sources, derive_fixture, load_sources
from .findings import Findings
from .observation import _paged, snake

SPEC_PATH = REPO_ROOT / "config/reference/mixed-sources/composition.json"
EXPECTATIONS_PATH = REPO_ROOT / "config/reference/mixed-sources/expectations.json"
SNAPSHOTS_PATH = REPO_ROOT / "config/reference/source-snapshots.json"
SPEC_SCHEMA = "sceneops.mixed_source_composition/1"
EXPECTATIONS_SCHEMA = "sceneops.mixed_source_expectations/1"
OBSERVATION_SCHEMA = "sceneops.mixed_source_observation/1"
PLURAL = {"scene": "scenes", "episode": "episodes"}


class SpecError(ValueError):
    """The composition spec does not name a derivable composition."""


# ── derivation ───────────────────────────────────────────────────────────────


@dataclass
class ExpectedMember:
    part: str
    domain: str
    unit_id: str
    source_kind: str
    source_id: str
    unit_key: str
    # Content members: the whole pin is derived. Recording members: the
    # verifier does not reproduce payload artifact ids, so the checksum is the
    # verified manifest's.
    manifest_checksum: str | None
    producer_fingerprint: str | None
    summary: dict[str, Any]
    # (findings, manifest, revision) -> per-source content checks
    check: Callable[[Findings, dict[str, Any], dict[str, Any]], None]
    manifest: dict[str, Any] | None = None


@dataclass
class ExpectedPart:
    part: str
    source_kind: str
    source_id: str
    domains: list[str]
    # recording: the RobotRun; content: the declaration and content checksum
    facts: dict[str, Any]


@dataclass
class MixedExpectation:
    parts: dict[str, ExpectedPart]
    members: dict[str, ExpectedMember] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        scenes = [m for m in self.members.values() if m.domain == "scene"]
        return {
            "scene_count": len(scenes),
            "observation_count": sum(m.summary["observation_count"] for m in scenes),
            "keyframe_count": sum(m.summary["keyframe_count"] for m in scenes),
            "observed_channels": sorted(
                {c for m in scenes for c in m.summary["observed_channels"]}
            ),
            "episode_count": sum(1 for m in self.members.values() if m.domain == "episode"),
        }


def load_spec(path: Path = SPEC_PATH) -> dict[str, Any]:
    spec = json.loads(path.read_text())
    if spec.get("schema") != SPEC_SCHEMA:
        raise SpecError(f"{path}: schema is not {SPEC_SCHEMA}")
    names = [p["part"] for p in spec["parts"]]
    if len(set(names)) != len(names):
        raise SpecError(f"{path}: duplicate part names {names}")
    return spec


def _declarations() -> dict[str, Any]:
    return json.loads(SNAPSHOTS_PATH.read_text())["snapshots"]


def _recording_part(
    part: dict[str, Any], sources: Sources, cache_root: Path
) -> tuple[ExpectedPart, list[ExpectedMember]]:
    mode, fixture = part["mode"], part["fixture"]
    run_id = sources.run_id(mode, fixture)
    recording = sources.locked(fixture)["recording"]
    derived = derive_fixture(sources, fixture, cache_root)
    members: list[ExpectedMember] = []

    def unit_id(domain: str, unit_key: str) -> str:
        return rules.unit_id(
            domain=domain, source_kind="recording", source_id=run_id, unit_key=unit_key
        )

    if "scene" in part["domains"]:
        scene = derived.scene

        def check_scene(f: Findings, manifest: dict[str, Any], revision: dict[str, Any]) -> None:
            checks.check_lineage(
                f, manifest, robot_run_id=run_id, recording_checksum=recording["sha256"],
                unit_key=scene.unit_key, window=scene.window, revision=revision,
                build_config_file=sources.scene_config, producer=scene.producer,
            )  # fmt: skip
            checks.check_scene_manifest(f, manifest, scene, revision)

        members.append(
            ExpectedMember(
                part=part["part"], domain="scene", unit_id=unit_id("scene", scene.unit_key),
                source_kind="recording", source_id=run_id, unit_key=scene.unit_key,
                manifest_checksum=None, producer_fingerprint=None,
                summary={
                    "observation_count": sum(len(r) for r in scene.observations.values()),
                    "keyframe_count": 0,
                    "observed_channels": sorted(t for t, r in scene.observations.items() if r),
                },
                check=check_scene,
            )  # fmt: skip
        )
    if "episode" in part["domains"]:
        for unit in derived.episodes.units:

            def check_episode(f, manifest, revision, unit=unit) -> None:  # type: ignore[no-untyped-def]
                checks.check_lineage(
                    f, manifest, robot_run_id=run_id, recording_checksum=recording["sha256"],
                    unit_key=unit.unit_key, window=unit.window, revision=revision,
                    build_config_file=sources.episode_config, producer=None,
                )  # fmt: skip
                checks.check_episode_manifest(f, manifest, unit, revision)

            members.append(
                ExpectedMember(
                    part=part["part"], domain="episode", unit_id=unit_id("episode", unit.unit_key),
                    source_kind="recording", source_id=run_id, unit_key=unit.unit_key,
                    manifest_checksum=None, producer_fingerprint=None, summary={},
                    check=check_episode,
                )  # fmt: skip
            )
    facts = {
        "robot_run_id": run_id,
        "robot_id": sources.contract["ingestion_modes"][mode]["robot_id"],
        "recording": recording,
        "derived": derived,
    }
    return ExpectedPart(part["part"], "recording", run_id, part["domains"], facts), members


def _content_part(
    part: dict[str, Any], declarations: dict[str, Any], source_root: Path | None
) -> tuple[ExpectedPart, list[ExpectedMember]]:
    (domain,) = part["domains"]
    declaration = declarations[part["snapshot"]]
    root = source_root or REPO_ROOT / declaration["local_root"]
    config = json.loads((REPO_ROOT / part["build_config"]).read_text())
    keys = part.get("unit_keys")
    if declaration["format"] == "nuscenes" and domain == "scene":
        module: Any = nuscenes_scene
        raw = nuscenes_scene.read_raw(root, declaration["include"])
        expected = nuscenes_scene.derive(raw, config, keys)
    elif declaration["format"] == "lerobot" and domain == "episode":
        module = lerobot
        raw = lerobot.read_raw(root, declaration["include"])
        expected = lerobot.derive(raw, config, None if keys is None else [int(k) for k in keys])
    else:
        raise SpecError(f"part {part['part']}: no rules for {declaration['format']} {domain}s")
    refused = sorted(u.unit_key for u in expected.values() if u.refusals)
    if refused and keys is not None:
        raise SpecError(f"part {part['part']}: the rules refuse the named units {refused}")
    members = []
    for unit_id, unit in sorted(expected.items()):
        if unit.refusals:
            continue
        projection = module.projection(unit.manifest)

        def check(f, manifest, revision, unit=unit, module=module) -> None:  # type: ignore[no-untyped-def]
            module.check_manifest(f, manifest, unit)
            f.equal(
                {k: revision.get(k) for k in module.projection(unit.manifest)},
                module.projection(unit.manifest),
                "revision_projection",
                "revision projection",
            )

        members.append(
            ExpectedMember(
                part=part["part"], domain=domain, unit_id=unit_id, source_kind="content",
                source_id=raw.content_id, unit_key=unit.unit_key,
                manifest_checksum=rules.checksum(rules.canonical_json(unit.manifest)),
                producer_fingerprint=projection["producer_fingerprint"],
                summary={
                    k: projection[k]
                    for k in ("observation_count", "keyframe_count", "observed_channels")
                    if k in projection
                },
                check=check, manifest=unit.manifest,
            )  # fmt: skip
        )
    facts = {
        "declaration": {k: declaration[k] for k in ("format", "upstream", "upstream_revision")},
        "content_checksum": raw.content_checksum,
        "file_count": len(raw.files),
        "total_bytes": sum(f["size_bytes"] for f in raw.files),
    }
    return ExpectedPart(part["part"], "content", raw.content_id, part["domains"], facts), members


def derive(
    spec: dict[str, Any],
    *,
    cache_root: Path,
    source_roots: dict[str, Path] | None = None,
) -> MixedExpectation:
    """The expected composition of ``spec``; ``source_roots`` overrides a
    snapshot's local root (by snapshot name)."""
    sources = load_sources()
    declarations = _declarations()
    expected = MixedExpectation(parts={})
    for part in spec["parts"]:
        if part["source_kind"] == "recording":
            derived_part, members = _recording_part(part, sources, cache_root)
        elif part["source_kind"] == "content":
            derived_part, members = _content_part(
                part, declarations, (source_roots or {}).get(part["snapshot"])
            )
        else:
            raise SpecError(f"part {part['part']}: unknown source_kind {part['source_kind']!r}")
        if not members:
            raise SpecError(f"part {part['part']} derives no unit")
        expected.parts[derived_part.part] = derived_part
        for member in members:
            if member.unit_id in expected.members:
                raise SpecError(f"unit {member.unit_id} is named by two parts")
            expected.members[member.unit_id] = member
    return expected


def expectations_document(expected: MixedExpectation) -> dict[str, Any]:
    """The committed form of the derivation (``derive --write``): every
    derived identity, each content pin and the summary."""
    parts = {}
    for name, part in expected.parts.items():
        members = [m for m in expected.members.values() if m.part == name]
        doc: dict[str, Any] = {
            "source_kind": part.source_kind,
            "source_id": part.source_id,
            "domains": part.domains,
        }
        if part.source_kind == "recording":
            doc["recording_sha256"] = part.facts["recording"]["sha256"]
        else:
            doc.update(
                {k: part.facts[k] for k in ("declaration", "content_checksum", "file_count", "total_bytes")}
            )
            doc["producer_fingerprint"] = sorted({m.producer_fingerprint for m in members})
        doc["members"] = [
            {
                "domain": m.domain,
                "unit_key": m.unit_key,
                "unit_id": m.unit_id,
                **({"manifest_checksum": m.manifest_checksum} if m.manifest_checksum else {}),
            }
            for m in sorted(members, key=lambda m: (m.domain, m.unit_id))
        ]
        parts[name] = doc
    return {
        "schema": EXPECTATIONS_SCHEMA,
        "derived_from": {
            "composition": str(SPEC_PATH.relative_to(REPO_ROOT)),
            "rules": "docs/development/reference-verification.md (Mixed-source DatasetVersions)",
        },
        "parts": parts,
        "summary": expected.summary(),
    }


def check_expectations(f: Findings, committed: dict[str, Any] | None, derived: dict[str, Any]) -> None:
    """The committed expectations must equal a fresh derivation."""
    if not f.check(committed is not None, "expectations_drift", "no committed expectations"):
        return
    for key in sorted(set(committed) | set(derived)):
        if key == "parts":
            for name in sorted(set(committed.get("parts", {})) | set(derived["parts"])):
                f.equal(committed.get("parts", {}).get(name), derived["parts"].get(name),
                        "expectations_drift", f"part {name}")  # fmt: skip
        else:
            f.equal(committed.get(key), derived.get(key), "expectations_drift", key)


# ── the platform state ───────────────────────────────────────────────────────


def empty_observation() -> dict[str, Any]:
    return {
        "schema": OBSERVATION_SCHEMA,
        "dataset_version": None,
        "units": {"scene": {}, "episode": {}},
        "manifests": {},
        "contents": {},
        "robot_runs": {},
    }


def collect(
    api: Callable[[str], Any],
    store: Any,
    *,
    dataset_key: str,
    content_ids: list[str],
    robot_run_ids: list[str],
) -> dict[str, Any]:
    """Read-only: the DatasetVersion row and its members, each member's unit
    with every revision and the member's manifest bytes, the content records
    and snapshots of ``content_ids``, the RobotRuns of ``robot_run_ids``."""
    obs = empty_observation()
    dataset_id, version = dataset_key.split("/", 1)
    base = f"/datasets/{urllib.parse.quote(dataset_id)}/versions/{urllib.parse.quote(version)}"
    row = api(base)
    if row:
        members = []
        for domain, plural in PLURAL.items():
            for revision in map(snake, _paged(api, f"{base}/{plural}", plural)):
                members.append(
                    {
                        "domain": domain,
                        "unit_id": revision[f"{domain}_id"],
                        "manifest_artifact_id": revision["manifest_artifact_id"],
                        "manifest_checksum": revision["manifest_checksum"],
                    }
                )
        obs["dataset_version"] = {"key": dataset_key, "version": snake(row["version"]), "members": members}
        for member in members:
            domain, unit_id = member["domain"], member["unit_id"]
            detail = snake(api(f"/{PLURAL[domain]}/{unit_id}") or {})
            revisions = []
            for r in detail.get("revisions", []):
                revisions.append({**r, "unit_id": r.pop(f"{domain}_id")})
            obs["units"][domain][unit_id] = {"record": detail.get(domain), "revisions": revisions}
            artifact_id = member["manifest_artifact_id"]
            if artifact_id in obs["manifests"]:
                continue
            artifact = snake((api(f"/artifacts/{artifact_id}") or {}).get("artifact")) or {}
            data = store.get(artifact["uri"]) if artifact.get("uri") else None
            obs["manifests"][artifact_id] = {
                "artifact": artifact,
                "bytes": None if data is None else data.decode("utf-8"),
            }
    for content_id in content_ids:
        content = snake((api(f"/source-contents/{content_id}") or {}).get("sourceContent"))
        snapshots = _paged(api, f"/source-snapshots?content_id={urllib.parse.quote(content_id)}", "sourceSnapshots")
        obs["contents"][content_id] = {"content": content, "snapshots": [snake(s) for s in snapshots]}
    for run_id in robot_run_ids:
        found = api(f"/robot-runs/{urllib.parse.quote(run_id)}")
        obs["robot_runs"][run_id] = snake(found["robotRun"]) if found else None
    return obs


def _registered_revision(obs: dict[str, Any], member: dict[str, Any]) -> dict[str, Any] | None:
    unit = obs["units"].get(member.get("domain"), {}).get(member.get("unit_id")) or {}
    return next(
        (
            r
            for r in unit.get("revisions", [])
            if (r.get("manifest_artifact_id"), r.get("manifest_checksum"))
            == (member.get("manifest_artifact_id"), member.get("manifest_checksum"))
        ),
        None,
    )


def verify(f: Findings, obs: dict[str, Any], expected: MixedExpectation) -> None:
    for name, part in expected.parts.items():
        g = f.scoped(part=name)
        if part.source_kind == "recording":
            record = obs["robot_runs"].get(part.source_id)
            g.check(record is not None, "robot_run", f"RobotRun {part.source_id} is not registered")
            continue
        entry = obs["contents"].get(part.source_id) or {}
        content = entry.get("content") or {}
        g.equal(
            [content.get("content_id"), content.get("content_checksum"), content.get("file_count"), content.get("total_bytes")],
            [part.source_id, part.facts["content_checksum"], part.facts["file_count"], part.facts["total_bytes"]],
            "source_registry", "registered content vs the raw tree",
        )  # fmt: skip
        declared = [
            {k: s.get(k) for k in ("format", "upstream", "upstream_revision")}
            for s in entry.get("snapshots", [])
            if s.get("content_id") == part.source_id
        ]
        g.check(
            part.facts["declaration"] in declared,
            "source_registry",
            f"content {part.source_id} is not declared by {part.facts['declaration']} (declared: {declared[:3]})",
        )

    version = obs.get("dataset_version")
    if not f.check(version is not None, "dataset_version_missing", "the DatasetVersion is not composed"):
        return
    members = version["members"]
    by_unit = {m.get("unit_id"): m for m in members}
    f.equal(len(by_unit), len(members), "composition_members", "at most one member per unit")
    f.equal(sorted(by_unit), sorted(expected.members), "composition_members", "member units")
    # Names that would make a manifest depend on its membership.
    dataset_id, version_name = version["key"].split("/", 1)
    forbidden_tokens = [json.dumps(dataset_id), json.dumps(version_name)]

    for unit_id, member in sorted(by_unit.items()):
        exp = expected.members.get(unit_id)
        if exp is None:
            continue
        g = f.scoped(part=exp.part, unit_key=exp.unit_key)
        g.equal(member.get("domain"), exp.domain, "member_domain", f"member {unit_id} domain")
        g.equal(
            member.get("manifest_artifact_id"),
            rules.manifest_artifact_id(unit_id=unit_id, manifest_checksum=member.get("manifest_checksum", "")),
            "pin_artifact_id", f"member {unit_id} pin",
        )  # fmt: skip
        unit = obs["units"].get(exp.domain, {}).get(unit_id)
        if not g.check(unit is not None and unit.get("record") is not None, "unit_identity", f"unit {unit_id} is not registered"):
            continue
        record = unit["record"]
        binding = {"source_kind": exp.source_kind, "unit_key": exp.unit_key,
                   "robot_run_id": exp.source_id if exp.source_kind == "recording" else None,
                   "content_id": exp.source_id if exp.source_kind == "content" else None}  # fmt: skip
        g.equal({k: record.get(k) for k in binding}, binding, "unit_identity", "unit binding")
        source_id = record.get("robot_run_id") if record.get("source_kind") == "recording" else record.get("content_id")
        g.equal(
            rules.unit_id(domain=exp.domain, source_kind=record.get("source_kind") or "", source_id=source_id or "", unit_key=record.get("unit_key") or ""),
            unit_id, "unit_identity", "unit id vs f(domain, source_kind, source_id, unit_key)",
        )  # fmt: skip
        revision = _registered_revision(obs, member)
        if not g.check(revision is not None, "member_pin", f"member {unit_id} pins no registered revision of its unit"):
            continue
        entry = obs["manifests"].get(member.get("manifest_artifact_id")) or {}
        text = entry.get("bytes")
        if not g.check(text is not None, "manifest_missing", f"manifest of {unit_id} is not in the store"):
            continue
        data = text.encode("utf-8")
        actual = rules.checksum(data)
        artifact = entry.get("artifact") or {}
        g.equal(
            [actual, artifact.get("checksum"), artifact.get("size_bytes"), artifact.get("kind")],
            [member.get("manifest_checksum"), actual, len(data), checks.MANIFEST_KIND[exp.domain]],
            "manifest_checksum", "stored bytes vs the pin and the manifest record",
        )  # fmt: skip
        if exp.manifest_checksum is not None:
            g.equal(actual, exp.manifest_checksum, "manifest_checksum", "stored bytes vs the derived manifest")
        try:
            manifest = json.loads(data)
        except json.JSONDecodeError as exc:
            g.fail("manifest_canonical", f"manifest of {unit_id} is not JSON: {exc}")
            continue
        g.check(rules.canonical_json(manifest) == data, "manifest_canonical", "canonical bytes")
        bad = checks._forbidden_keys(manifest)
        named = [t for t in forbidden_tokens if t in text]
        g.check(not bad and not named, "manifest_forbidden_field", f"the manifest carries {bad[:5]} {named}")
        exp.check(g, manifest, revision)

    row = version["version"]
    pins = [(m.get("domain"), m.get("unit_id"), m.get("manifest_checksum")) for m in members]
    f.equal(row.get("composition_checksum"), rules.composition_checksum(pins), "composition_checksum", "checksum vs f(member pins)")
    expected_pins = [
        (e.domain, e.unit_id, e.manifest_checksum or (by_unit.get(e.unit_id) or {}).get("manifest_checksum"))
        for e in expected.members.values()
    ]
    f.equal(row.get("composition_checksum"), rules.composition_checksum(expected_pins), "composition_checksum", "checksum vs f(expected pins)")  # fmt: skip
    summary = expected.summary()
    f.equal(
        {k: checks._summary_value(row, k) for k in summary}, summary,
        "composition_summary", "summaries vs the derivation",
    )  # fmt: skip
    selector = row.get("selector")
    if selector is not None:
        fingerprints: dict[tuple[str, str, str], set[str]] = {}
        for e in expected.members.values():
            fp = e.producer_fingerprint
            if fp is None:  # a recording member: the verified manifest's own
                revision = _registered_revision(obs, by_unit.get(e.unit_id) or {})
                fp = (revision or {}).get("producer_fingerprint")
            fingerprints.setdefault((e.domain, e.source_kind, e.source_id), set()).add(fp)
        clauses = sorted(
            (c.get("domain"), c.get("source_kind"), c.get("source_id"), c.get("producer_fingerprint"))
            for c in selector.get("sources", [])
        )
        f.equal(
            clauses,
            sorted((d, k, s, fp) for (d, k, s), fps in fingerprints.items() for fp in fps),
            "selector", "selector clauses vs the parts' sources",
        )  # fmt: skip


# ── a synthetic platform state (selftest) ────────────────────────────────────


def _register(
    obs: dict[str, Any], member: ExpectedMember, manifest: dict[str, Any], projection: dict[str, Any]
) -> dict[str, Any]:
    data = rules.canonical_json(manifest)
    checksum = rules.checksum(data)
    artifact_id = rules.manifest_artifact_id(unit_id=member.unit_id, manifest_checksum=checksum)
    obs["manifests"][artifact_id] = {
        "artifact": {"artifact_id": artifact_id, "kind": checks.MANIFEST_KIND[member.domain],
                     "checksum": checksum, "size_bytes": len(data)},  # fmt: skip
        "bytes": data.decode("utf-8"),
    }
    source = manifest["lineage"]["source"]
    binding = {
        "source_kind": member.source_kind,
        "robot_run_id": member.source_id if member.source_kind == "recording" else None,
        "content_id": member.source_id if member.source_kind == "content" else None,
        "unit_key": member.unit_key,
    }
    obs["units"][member.domain][member.unit_id] = {
        "record": {f"{member.domain}_id": member.unit_id, **binding},
        "revisions": [
            {
                "unit_id": member.unit_id, **binding,
                "manifest_artifact_id": artifact_id, "manifest_checksum": checksum,
                "producer_fingerprint": manifest["lineage"]["producer"]["producer_fingerprint"],
                "window_clock": source["source_clock"],
                "window_start_timestamp_ns": source["start_timestamp_ns"],
                "window_end_timestamp_ns": source["end_timestamp_ns"],
                **projection,
            }
        ],
    }  # fmt: skip
    return {"domain": member.domain, "unit_id": member.unit_id,
            "manifest_artifact_id": artifact_id, "manifest_checksum": checksum}  # fmt: skip


def synthetic_observation(
    expected: MixedExpectation, *, dataset_key: str, with_selector: bool
) -> dict[str, Any]:
    """The state the rules describe for ``expected``, composed under
    ``dataset_key`` (by pins, or with the selector the parts determine)."""
    obs = empty_observation()
    sources = load_sources()
    pins = []
    for member in sorted(expected.members.values(), key=lambda m: m.unit_id):
        part = expected.parts[member.part]
        if member.source_kind == "recording":
            run_id, sha = part.facts["robot_run_id"], part.facts["recording"]["sha256"]
            derived = part.facts["derived"]
            if member.domain == "scene":
                manifest = synthetic.scene_manifest(derived.scene, run_id, sha, sources.scene_config)
                projection = {"observed_channels": member.summary["observed_channels"],
                              "observation_count": member.summary["observation_count"], "keyframe_count": 0}  # fmt: skip
            else:
                unit = next(u for u in derived.episodes.units if u.unit_key == member.unit_key)
                manifest = synthetic.episode_manifest(unit, run_id, sha, sources.episode_config)
                projection = {}
                for role in ("observations", "states", "actions", "events"):
                    by_topic = unit.occurrences.get(role, {})
                    projection[f"{role[:-1]}_topics"] = sorted(t for t, r in by_topic.items() if r)
                    projection[f"{role[:-1]}_count"] = sum(len(r) for r in by_topic.values())
        else:
            manifest = member.manifest
            module = nuscenes_scene if member.domain == "scene" else lerobot
            projection = {k: v for k, v in module.projection(manifest).items()
                          if not k.startswith("window") and k != "producer_fingerprint"}  # fmt: skip
        pins.append(_register(obs, member, manifest, projection))
    for name, part in expected.parts.items():
        if part.source_kind == "recording":
            obs["robot_runs"][part.source_id] = {"run_id": part.source_id}
        else:
            obs["contents"][part.source_id] = {
                "content": {"content_id": part.source_id, "content_checksum": part.facts["content_checksum"],
                            "file_count": part.facts["file_count"], "total_bytes": part.facts["total_bytes"]},
                "snapshots": [{"snapshot_id": "snapshot-" + rules.sha256_hex(name.encode())[:32],
                               "content_id": part.source_id, **part.facts["declaration"]}],
            }  # fmt: skip
    summary = expected.summary()
    selector = None
    if with_selector:
        clauses = set()
        for pin in pins:
            unit = obs["units"][pin["domain"]][pin["unit_id"]]
            record, revision = unit["record"], unit["revisions"][0]
            source_id = record["robot_run_id"] or record["content_id"]
            clauses.add((pin["domain"], record["source_kind"], source_id, revision["producer_fingerprint"]))
        selector = {"sources": [dict(zip(("domain", "source_kind", "source_id", "producer_fingerprint"), c))
                                for c in sorted(clauses)]}  # fmt: skip
    obs["dataset_version"] = {
        "key": dataset_key,
        "version": {
            "dataset_id": dataset_key.split("/")[0], "version": dataset_key.split("/")[1],
            "composition_checksum": rules.composition_checksum((p["domain"], p["unit_id"], p["manifest_checksum"]) for p in pins),
            "selector": selector,
            "scene": {k: summary[k] for k in ("scene_count", "observation_count", "keyframe_count", "observed_channels")},
            "episode": {"episode_count": summary["episode_count"]},
        },
        "members": pins,
    }  # fmt: skip
    return obs


# ── negative controls ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Control:
    name: str
    fault: str
    codes: frozenset[str]
    apply: Callable[[dict[str, Any]], None]


def _members_of(obs: dict[str, Any], *, domain: str, source_kind: str) -> list[dict[str, Any]]:
    return [
        m
        for m in obs["dataset_version"]["members"]
        if m["domain"] == domain
        and obs["units"][domain][m["unit_id"]]["record"]["source_kind"] == source_kind
    ]


def _recompose(obs: dict[str, Any]) -> None:
    """The version row as the platform would have written it for the
    (corrupted) members: the checksum follows the pins."""
    version = obs["dataset_version"]
    version["version"]["composition_checksum"] = rules.composition_checksum(
        (m["domain"], m["unit_id"], m["manifest_checksum"]) for m in version["members"]
    )


def _rewrite_member(obs: dict[str, Any], member: dict[str, Any], mutate: Callable[[dict[str, Any]], None]) -> None:
    """A consistent manifest fault: the bytes are re-serialized and the record,
    revision, member pin and composition checksum all follow them."""
    old_id = member["manifest_artifact_id"]
    entry = obs["manifests"].pop(old_id)
    manifest = json.loads(entry["bytes"])
    mutate(manifest)
    data = rules.canonical_json(manifest)
    checksum = rules.checksum(data)
    new_id = rules.manifest_artifact_id(unit_id=member["unit_id"], manifest_checksum=checksum)
    entry["artifact"].update({"artifact_id": new_id, "checksum": checksum, "size_bytes": len(data)})
    entry["bytes"] = data.decode("utf-8")
    obs["manifests"][new_id] = entry
    for revision in obs["units"][member["domain"]][member["unit_id"]]["revisions"]:
        if revision["manifest_artifact_id"] == old_id:
            revision.update({"manifest_artifact_id": new_id, "manifest_checksum": checksum})
    member.update({"manifest_artifact_id": new_id, "manifest_checksum": checksum})
    _recompose(obs)


def _content_shift(obs: dict[str, Any]) -> None:
    member = _members_of(obs, domain="episode", source_kind="content")[0]

    def shift(manifest: dict[str, Any]) -> None:
        manifest["states"][1]["timestamp_ns"] += 1

    _rewrite_member(obs, member, shift)


def _recording_shift(obs: dict[str, Any]) -> None:
    member = _members_of(obs, domain="scene", source_kind="recording")[0]

    def shift(manifest: dict[str, Any]) -> None:
        manifest["observations"][1]["timestamp_ns"] += 1

    _rewrite_member(obs, member, shift)


def _membership_in_manifest(obs: dict[str, Any]) -> None:
    member = _members_of(obs, domain="scene", source_kind="content")[0]
    name = obs["dataset_version"]["key"].split("/", 1)[1]
    _rewrite_member(obs, member, lambda m: m["channels"][0].update({"sensor_id": name}))


def _wrong_pin(obs: dict[str, Any]) -> None:
    """A member pins a checksum its unit never registered (artifact id and
    composition checksum recomputed)."""
    member = _members_of(obs, domain="episode", source_kind="content")[-1]
    checksum = "sha256:" + "9" * 64
    member.update({"manifest_checksum": checksum,
                   "manifest_artifact_id": rules.manifest_artifact_id(unit_id=member["unit_id"], manifest_checksum=checksum)})  # fmt: skip
    _recompose(obs)


def _drop_member(obs: dict[str, Any]) -> None:
    obs["dataset_version"]["members"].remove(_members_of(obs, domain="episode", source_kind="content")[-1])
    obs["dataset_version"]["version"]["episode"]["episode_count"] -= 1
    _recompose(obs)


def _foreign_member(obs: dict[str, Any]) -> None:
    """A unit of another content, registered and pinned consistently."""
    template = _members_of(obs, domain="episode", source_kind="content")[0]
    unit = copy.deepcopy(obs["units"]["episode"][template["unit_id"]])
    other = "content-" + "f" * 32
    unit_id = rules.unit_id(domain="episode", source_kind="content", source_id=other, unit_key="0")
    unit["record"].update({"episode_id": unit_id, "content_id": other})
    for revision in unit["revisions"]:
        revision.update({"unit_id": unit_id, "content_id": other})
    obs["units"]["episode"][unit_id] = unit
    member = {**template, "unit_id": unit_id,
              "manifest_artifact_id": rules.manifest_artifact_id(unit_id=unit_id, manifest_checksum=template["manifest_checksum"])}  # fmt: skip
    unit["revisions"][0]["manifest_artifact_id"] = member["manifest_artifact_id"]
    obs["manifests"][member["manifest_artifact_id"]] = copy.deepcopy(obs["manifests"][template["manifest_artifact_id"]])
    obs["dataset_version"]["members"].append(member)
    obs["dataset_version"]["version"]["episode"]["episode_count"] += 1
    _recompose(obs)


def _duplicate_unit(obs: dict[str, Any]) -> None:
    """A second revision of one unit pinned beside the first."""
    member = _members_of(obs, domain="scene", source_kind="content")[0]
    unit = obs["units"]["scene"][member["unit_id"]]
    checksum = "sha256:" + "8" * 64
    second = {**member, "manifest_checksum": checksum,
              "manifest_artifact_id": rules.manifest_artifact_id(unit_id=member["unit_id"], manifest_checksum=checksum)}  # fmt: skip
    unit["revisions"].append({**unit["revisions"][0], "manifest_checksum": checksum,
                              "manifest_artifact_id": second["manifest_artifact_id"]})  # fmt: skip
    obs["manifests"][second["manifest_artifact_id"]] = copy.deepcopy(obs["manifests"][member["manifest_artifact_id"]])
    obs["dataset_version"]["members"].append(second)
    _recompose(obs)


def _wrong_domain(obs: dict[str, Any]) -> None:
    member = _members_of(obs, domain="episode", source_kind="recording")[0]
    member["domain"] = "scene"
    obs["units"]["scene"][member["unit_id"]] = obs["units"]["episode"].pop(member["unit_id"])
    _recompose(obs)


def _synthetic_robot_run(obs: dict[str, Any]) -> None:
    """A content Scene registered as if a RobotRun had recorded it."""
    member = _members_of(obs, domain="scene", source_kind="content")[0]
    record = obs["units"]["scene"][member["unit_id"]]["record"]
    record.update({"source_kind": "recording", "robot_run_id": "run-synthetic-" + record["content_id"], "content_id": None})


def _source_swap(obs: dict[str, Any]) -> None:
    """A LeRobot unit recorded under the nuScenes content."""
    scene = _members_of(obs, domain="scene", source_kind="content")[0]
    nuscenes_content = obs["units"]["scene"][scene["unit_id"]]["record"]["content_id"]
    member = _members_of(obs, domain="episode", source_kind="content")[0]
    obs["units"]["episode"][member["unit_id"]]["record"]["content_id"] = nuscenes_content


def _undeclared_content(obs: dict[str, Any]) -> None:
    content_id = next(iter(obs["contents"]))
    obs["contents"][content_id]["snapshots"] = []


def _summary_count(obs: dict[str, Any]) -> None:
    obs["dataset_version"]["version"]["episode"]["episode_count"] += 1


def _summary_channels(obs: dict[str, Any]) -> None:
    obs["dataset_version"]["version"]["scene"]["observed_channels"].pop(0)


def _checksum_of_other_pins(obs: dict[str, Any]) -> None:
    pins = [(m["domain"], m["unit_id"], m["manifest_checksum"]) for m in obs["dataset_version"]["members"]][1:]
    obs["dataset_version"]["version"]["composition_checksum"] = rules.composition_checksum(pins)


def _selector_clause(obs: dict[str, Any]) -> None:
    clause = next(c for c in obs["dataset_version"]["version"]["selector"]["sources"] if c["source_kind"] == "content")
    clause["source_id"] = "content-" + "e" * 32


def controls(*, with_selector: bool) -> list[Control]:
    found = [
        Control("content_timestamp_shift", "a pusht state 1 ns off its frame, rewritten consistently",
                frozenset({"manifest_checksum", "episode_occurrences"}), _content_shift),
        Control("recording_timestamp_shift", "a recording Scene observation 1 ns off, rewritten consistently",
                frozenset({"scene_observations"}), _recording_shift),
        Control("membership_in_manifest", "the version's name written into a content manifest",
                frozenset({"manifest_forbidden_field"}), _membership_in_manifest),
        Control("wrong_pin", "a member pins a checksum its unit never registered",
                frozenset({"member_pin"}), _wrong_pin),
        Control("dropped_member", "a pusht member dropped, summary and checksum recomputed",
                frozenset({"composition_members"}), _drop_member),
        Control("foreign_member", "a unit of another content composed in",
                frozenset({"composition_members"}), _foreign_member),
        Control("duplicate_unit", "two revisions of one unit pinned",
                frozenset({"composition_members"}), _duplicate_unit),
        Control("wrong_domain", "a recording Episode composed as a Scene",
                frozenset({"composition_members", "member_domain"}), _wrong_domain),
        Control("synthetic_robot_run", "a content Scene bound to a RobotRun",
                frozenset({"unit_identity"}), _synthetic_robot_run),
        Control("source_swap", "a LeRobot unit recorded under the nuScenes content",
                frozenset({"unit_identity"}), _source_swap),
        Control("undeclared_content", "a content part without its snapshot declaration",
                frozenset({"source_registry"}), _undeclared_content),
        Control("summary_count", "episode_count off by one",
                frozenset({"composition_summary"}), _summary_count),
        Control("summary_channels", "an observed channel missing from the summary",
                frozenset({"composition_summary"}), _summary_channels),
        Control("checksum_of_other_pins", "the composition checksum of another pin set",
                frozenset({"composition_checksum"}), _checksum_of_other_pins),
    ]  # fmt: skip
    if with_selector:
        found.append(
            Control("selector_clause", "a selector clause names another content",
                    frozenset({"selector"}), _selector_clause)  # fmt: skip
        )
    return found


def run_controls(
    obs: dict[str, Any], verify_copy: Callable[[dict[str, Any]], set[str]]
) -> list[dict[str, Any]]:
    results = []
    with_selector = (obs["dataset_version"]["version"] or {}).get("selector") is not None
    for control in controls(with_selector=with_selector):
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
