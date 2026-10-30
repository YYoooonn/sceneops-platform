"""Negative controls: known faults the verifier must detect (ADR-015 §12 step 2).

Each control corrupts a copy of an observation (or of the source inputs)
that passes, and the verification of the copy must report at least one of
the control's codes. A *consistent* fault rewrites everything derived from
the corrupted value, as a faulty but self-consistent platform would: the
manifest is re-serialized canonically, its checksum, artifact id, revision,
DatasetVersion member pins and composition checksum are recomputed. Such a
fault can only be caught by the independent expectations, never by
comparing the platform's records with each other.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from . import rules


@dataclass(frozen=True)
class Control:
    name: str
    fault: str
    codes: frozenset[str]
    apply: Callable[[dict[str, Any], dict[str, Any]], None]


def _manifest_of(
    obs: dict[str, Any], domain: str, unit_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    revision = obs["units"][domain][unit_id]["revisions"][0]
    return revision, json.loads(
        obs["manifests"][revision["manifest_artifact_id"]]["bytes"]
    )


def rewrite_manifest(
    obs: dict[str, Any],
    domain: str,
    unit_id: str,
    mutate: Callable[[dict[str, Any]], None],
    *,
    serialize: Callable[[Any], bytes] = rules.canonical_json,
) -> None:
    """Apply ``mutate`` to the unit's manifest and re-derive everything the
    platform derives from manifest bytes, so the result is self-consistent."""
    revision, manifest = _manifest_of(obs, domain, unit_id)
    mutate(manifest)
    data = serialize(manifest)
    old_id = revision["manifest_artifact_id"]
    checksum = rules.checksum(data)
    new_id = rules.manifest_artifact_id(unit_id=unit_id, manifest_checksum=checksum)
    entry = obs["manifests"].pop(old_id)
    entry["artifact"] = {
        **entry["artifact"],
        "artifact_id": new_id,
        "checksum": checksum,
        "size_bytes": len(data),
    }
    entry["bytes"] = data.decode("utf-8")
    obs["manifests"][new_id] = entry
    source = manifest.get("lineage", {}).get("source", {})
    revision.update(
        manifest_artifact_id=new_id,
        manifest_checksum=checksum,
        producer_fingerprint=manifest.get("lineage", {})
        .get("producer", {})
        .get("producer_fingerprint"),
        window_clock=source.get("source_clock"),
        window_start_timestamp_ns=source.get("start_timestamp_ns"),
        window_end_timestamp_ns=source.get("end_timestamp_ns"),
    )
    _repin(obs, unit_id, new_id, checksum)


def _repin(obs: dict[str, Any], unit_id: str, artifact_id: str, checksum: str) -> None:
    for version in obs["dataset_versions"].values():
        for member in version["members"]:
            if member["unit_id"] == unit_id:
                member.update(
                    manifest_artifact_id=artifact_id, manifest_checksum=checksum
                )
        _recompose(version)


def _recompose(version: dict[str, Any]) -> None:
    version["version"]["composition_checksum"] = rules.composition_checksum(
        (m["domain"], m["unit_id"], m["manifest_checksum"]) for m in version["members"]
    )


def _refingerprint(manifest: dict[str, Any]) -> None:
    lineage = manifest["lineage"]
    producer, source = lineage["producer"], lineage["source"]
    producer["producer_fingerprint"] = rules.producer_fingerprint(
        producer_id=producer["producer_id"],
        semantics_version=producer["semantics_version"],
        build_config=producer["build_config"],
        source=rules.recording_source_revision(
            robot_run_id=source["robot_run_id"],
            recording_checksum=source["recording_checksum"],
        ),
    )


def _first(obs: dict[str, Any], domain: str, run_id: str) -> str:
    return sorted(
        u
        for u, x in obs["units"][domain].items()
        if x["record"]["robot_run_id"] == run_id
    )[0]


# ── Platform controls ────────────────────────────────────────────────────────
# ctx: run_id (a run of the first verified mode), other_run_id (another RobotRun),
# foreign_run_id (an observed RobotRun whose units are outside dataset_key, or None),
# dataset_key (the DatasetVersion holding run_id's units).


def _other_member(
    members: list[dict[str, Any]], member: dict[str, Any]
) -> dict[str, Any]:
    """Another member, of the same domain when the version has one: a one-fixture
    composition holds a single Scene."""
    others = [m for m in members if m["unit_id"] != member["unit_id"]]
    return sorted(others, key=lambda m: m["domain"] != member["domain"])[0]


def _drop_observation(obs, ctx):
    rewrite_manifest(
        obs,
        "scene",
        _first(obs, "scene", ctx["run_id"]),
        lambda m: m["observations"].pop(),
    )


def _shift_1ns(obs, ctx):
    def mutate(m):
        m["observations"][len(m["observations"]) // 2]["timestamp_ns"] += 1

    rewrite_manifest(obs, "scene", _first(obs, "scene", ctx["run_id"]), mutate)


def _payload_byte_in_store(obs, ctx):
    revision, manifest = _manifest_of(obs, "scene", _first(obs, "scene", ctx["run_id"]))
    ref = manifest["observations"][0]["payload"]
    obs["payloads"][rules.payload_artifact_id_of(ref)]["object"]["checksum"] = rules.checksum(
        b"changed byte"
    )


def _payload_rebound(obs, ctx):
    """The platform consistently stores and pins other bytes for one observation."""
    unit_id = _first(obs, "scene", ctx["run_id"])
    other = rules.checksum(b"one changed payload byte")

    def mutate(m):
        ref = m["observations"][0]["payload"]
        entry = obs["payloads"][rules.payload_artifact_id_of(ref)]
        entry["artifact"]["checksum"] = entry["object"]["checksum"] = ref[
            "checksum"
        ] = other

    rewrite_manifest(obs, "scene", unit_id, mutate)


def _wrong_pin(obs, ctx):
    """A member pins a revision that is not registered for its unit (the pin of
    another unit's revision, with a self-consistent artifact id)."""
    version = obs["dataset_versions"][ctx["dataset_key"]]
    member = next(m for m in version["members"] if m["domain"] == "scene")
    donor = _other_member(version["members"], member)
    member["manifest_checksum"] = donor["manifest_checksum"]
    member["manifest_artifact_id"] = rules.manifest_artifact_id(
        unit_id=member["unit_id"], manifest_checksum=donor["manifest_checksum"]
    )
    _recompose(version)


def _pin_artifact_mismatch(obs, ctx):
    version = obs["dataset_versions"][ctx["dataset_key"]]
    member = next(m for m in version["members"] if m["domain"] == "scene")
    member["manifest_artifact_id"] = _other_member(version["members"], member)[
        "manifest_artifact_id"
    ]


def _relative_rebase(obs, ctx):
    """Episode timestamps given relative to the unit's start while the clock
    stays the absolute source clock."""

    def mutate(m):
        source = m["lineage"]["source"]
        origin = source["start_timestamp_ns"]
        for role in ("observations", "states", "actions", "events"):
            for row in m[role]:
                row["timestamp_ns"] -= origin
        source["end_timestamp_ns"] -= origin
        source["start_timestamp_ns"] = 0

    rewrite_manifest(obs, "episode", _first(obs, "episode", ctx["run_id"]), mutate)


def _absolute_anchor(obs, ctx):
    """An absolute anchor added to the poses (and the window kept consistent)."""
    anchor = 1_000_000_000_000_000_000

    def mutate(m):
        for pose in m["poses"]:
            pose["timestamp_ns"] += anchor

    rewrite_manifest(obs, "scene", _first(obs, "scene", ctx["run_id"]), mutate)


def _clock_origin_swap(obs, ctx):
    """A recording clock declared absolute (unix_epoch) instead of source-origin."""

    def mutate(m):
        m["clock_domains"][0]["origin"] = "unix_epoch"

    rewrite_manifest(obs, "scene", _first(obs, "scene", ctx["run_id"]), mutate)


def _clock_made_relative(obs, ctx):
    """A recording clock declared unit-relative while its values stay absolute."""

    def mutate(m):
        m["clock_domains"][0]["origin"] = "unit"

    rewrite_manifest(obs, "episode", _first(obs, "episode", ctx["run_id"]), mutate)


def _clock_undeclared(obs, ctx):
    """The window's clock declaration is dropped."""

    def mutate(m):
        window_clock = m["lineage"]["source"]["source_clock"]
        m["clock_domains"] = [
            d for d in m["clock_domains"] if d["clock"] != window_clock
        ]

    rewrite_manifest(obs, "scene", _first(obs, "scene", ctx["run_id"]), mutate)


def _wrong_source_binding(obs, ctx):
    """The manifest binds another recording; the fingerprint is recomputed for it."""

    def mutate(m):
        m["lineage"]["source"]["recording_checksum"] = rules.checksum(
            b"another recording"
        )
        _refingerprint(m)

    rewrite_manifest(obs, "scene", _first(obs, "scene", ctx["run_id"]), mutate)


def _wrong_run_binding(obs, ctx):
    def mutate(m):
        m["lineage"]["source"]["robot_run_id"] = ctx["other_run_id"]
        m["lineage"]["source"]["recording_artifact_id"] = rules.recording_artifact_id(
            ctx["other_run_id"]
        )
        _refingerprint(m)

    rewrite_manifest(obs, "episode", _first(obs, "episode", ctx["run_id"]), mutate)


def _wrong_unit_key(obs, ctx):
    unit = obs["units"]["scene"][_first(obs, "scene", ctx["run_id"])]
    unit["record"]["unit_key"] = "whole_recording-0000000000000000/recording"


def _missing_member(obs, ctx):
    version = obs["dataset_versions"][ctx["dataset_key"]]
    version["members"] = [
        m
        for m in version["members"]
        if m["unit_id"] != _first(obs, "episode", ctx["run_id"])
    ]
    version["version"]["episode"]["episode_count"] -= 1
    _recompose(version)


def _foreign_unit(obs, run_id):
    """A registered Episode of a RobotRun outside the contract: a copy of run_id's
    first Episode bound to another run, its revision pin recomputed for its id."""
    original = obs["units"]["episode"][_first(obs, "episode", run_id)]
    foreign_run = f"{run_id}-foreign"
    unit_id = rules.unit_id(
        domain="episode",
        source_kind="recording",
        source_id=foreign_run,
        unit_key=original["record"]["unit_key"],
    )
    unit = copy.deepcopy(original)
    unit["record"].update(episode_id=unit_id, robot_run_id=foreign_run)
    for revision in unit["revisions"]:
        revision.update(unit_id=unit_id, robot_run_id=foreign_run)
        revision["manifest_artifact_id"] = rules.manifest_artifact_id(
            unit_id=unit_id, manifest_checksum=revision["manifest_checksum"]
        )
    obs["units"]["episode"][unit_id] = unit
    return unit_id


def _foreign_member(obs, ctx):
    """A unit of another RobotRun composed into the version (counts kept consistent)."""
    version = obs["dataset_versions"][ctx["dataset_key"]]
    foreign_run = ctx.get("foreign_run_id")
    unit_id = (
        _first(obs, "episode", foreign_run)
        if foreign_run
        else _foreign_unit(obs, ctx["run_id"])
    )
    revision = obs["units"]["episode"][unit_id]["revisions"][0]
    version["members"].append(
        {
            "domain": "episode",
            "unit_id": unit_id,
            "manifest_artifact_id": revision["manifest_artifact_id"],
            "manifest_checksum": revision["manifest_checksum"],
        }
    )
    version["version"]["episode"]["episode_count"] += 1
    _recompose(version)


def _composition_checksum(obs, ctx):
    obs["dataset_versions"][ctx["dataset_key"]]["version"]["composition_checksum"] = (
        rules.checksum(b"other composition")
    )


def _summary(obs, ctx):
    obs["dataset_versions"][ctx["dataset_key"]]["version"]["scene"][
        "observation_count"
    ] += 1


def _action_value(obs, ctx):
    def mutate(m):
        row = m["actions"][0]
        name = sorted(row["values"])[0]
        value = row["values"][name]
        row["values"][name] = value + 1 if not isinstance(value, bool) else not value

    rewrite_manifest(obs, "episode", _first(obs, "episode", ctx["run_id"]), mutate)


def _dropped_event(obs, ctx):
    rewrite_manifest(
        obs,
        "episode",
        _first(obs, "episode", ctx["run_id"]),
        lambda m: m["events"].pop(),
    )


def _unbound_fingerprint(obs, ctx):
    """The build_config changes but the fingerprint is not recomputed."""

    def mutate(m):
        m["lineage"]["producer"]["build_config"]["frames"]["ego_frame_id"] = (
            "other_frame"
        )

    rewrite_manifest(obs, "scene", _first(obs, "scene", ctx["run_id"]), mutate)


def _non_canonical(obs, ctx):
    rewrite_manifest(
        obs, "scene", _first(obs, "scene", ctx["run_id"]), lambda m: None,
        serialize=lambda v: json.dumps(v, indent=1, sort_keys=True).encode("utf-8"),
    )  # fmt: skip


def _dataset_field(obs, ctx):
    rewrite_manifest(
        obs,
        "episode",
        _first(obs, "episode", ctx["run_id"]),
        lambda m: m["lineage"]["source"].update(dataset_version="baseline"),
    )


def _payload_object_missing(obs, ctx):
    _, manifest = _manifest_of(obs, "scene", _first(obs, "scene", ctx["run_id"]))
    payload = manifest["observations"][-1]["payload"]
    obs["payloads"][rules.payload_artifact_id_of(payload)]["object"] = None


def _manifest_object_missing(obs, ctx):
    revision, _ = _manifest_of(obs, "episode", _first(obs, "episode", ctx["run_id"]))
    obs["manifests"][revision["manifest_artifact_id"]]["bytes"] = None


def _substituted_recording(obs, ctx):
    run = obs["robot_runs"][ctx["run_id"]]
    other = rules.checksum(b"another recording")
    run["recording_artifact"]["checksum"] = run["recording_object"]["checksum"] = other

    def mutate(m):
        m["lineage"]["source"]["recording_checksum"] = other
        _refingerprint(m)

    rewrite_manifest(obs, "scene", _first(obs, "scene", ctx["run_id"]), mutate)


def _second_revision(obs, ctx):
    unit = obs["units"]["scene"][_first(obs, "scene", ctx["run_id"])]
    extra = copy.deepcopy(unit["revisions"][0])
    extra["producer_fingerprint"] = rules.checksum(b"another producer")
    unit["revisions"].append(extra)


def _payload_identity(obs, ctx):
    """The Episode references the same recorded message through another payload artifact."""

    def mutate(m):
        row = next(r for r in m["observations"] if r.get("payload"))
        old = rules.payload_artifact_id_of(row["payload"])
        new = "payload-" + "0" * 32
        obs["payloads"][new] = copy.deepcopy(obs["payloads"][old])
        artifact = obs["payloads"][new]["artifact"]
        artifact["artifact_id"] = new
        artifact["uri"] = artifact["uri"].rpartition("/")[0] + f"/{new}"
        row["payload"]["uri"] = rules.observation_payload_uri(ctx["run_id"], new)

    rewrite_manifest(obs, "episode", _first(obs, "episode", ctx["run_id"]), mutate)


def _payload_of_another_run(obs, ctx):
    """An observation names, by Logical URI, a payload key of another RobotRun
    whose record and object agree with the reference (consistent)."""

    def mutate(m):
        ref = m["observations"][0]["payload"]
        payload_id = rules.payload_artifact_id_of(ref)
        ref["uri"] = rules.observation_payload_uri("run-elsewhere", payload_id)

    rewrite_manifest(obs, "scene", _first(obs, "scene", ctx["run_id"]), mutate)


def _payload_by_location(obs, ctx):
    """An observation names its payload by its storage location instead of a
    Logical URI."""

    def mutate(m):
        ref = m["observations"][0]["payload"]
        ref["uri"] = obs["payloads"][rules.payload_artifact_id_of(ref)]["artifact"][
            "uri"
        ]

    rewrite_manifest(obs, "scene", _first(obs, "scene", ctx["run_id"]), mutate)


PLATFORM_CONTROLS: tuple[Control, ...] = tuple(
    Control(name, fault, frozenset(codes), fn)
    for name, fault, codes, fn in (
        (
            "dropped_observation",
            "a Scene manifest drops one observation (consistent)",
            {"scene_observations"},
            _drop_observation,
        ),
        (
            "timestamp_shift_1ns",
            "one observation timestamp shifted by 1 ns (consistent)",
            {"scene_observations"},
            _shift_1ns,
        ),
        (
            "payload_byte_in_store",
            "one stored payload object's bytes changed",
            {"payload_object_checksum"},
            _payload_byte_in_store,
        ),
        (
            "payload_rebound",
            "one observation pins other payload bytes, record and object agree (consistent)",
            {"scene_observations"},
            _payload_rebound,
        ),
        (
            "wrong_pin",
            "a DatasetVersion member pins a revision its unit does not have (consistent)",
            {"member_pin"},
            _wrong_pin,
        ),
        (
            "pin_artifact_mismatch",
            "a member's manifest_artifact_id is another revision's",
            {"pin_artifact_id"},
            _pin_artifact_mismatch,
        ),
        (
            "relative_rebase",
            "Episode timestamps re-based to the unit start on an absolute clock (consistent)",
            {"window", "episode_occurrences", "revision_projection"},
            _relative_rebase,
        ),
        (
            "absolute_anchor",
            "an absolute anchor added to the Scene poses (consistent)",
            {"scene_poses", "window"},
            _absolute_anchor,
        ),
        (
            "clock_origin_swap",
            "a Scene recording clock declared with origin unix_epoch (consistent)",
            {"clock_domains"},
            _clock_origin_swap,
        ),
        (
            "clock_made_relative",
            "an Episode recording clock declared with origin unit (consistent)",
            {"clock_domains"},
            _clock_made_relative,
        ),
        (
            "clock_undeclared",
            "a Scene drops the declaration of its window clock (consistent)",
            {"clock_domains"},
            _clock_undeclared,
        ),
        (
            "wrong_source_recording",
            "a Scene binds another recording checksum, fingerprint recomputed (consistent)",
            {"source_binding", "producer_fingerprint"},
            _wrong_source_binding,
        ),
        (
            "wrong_source_run",
            "an Episode binds another RobotRun, fingerprint recomputed (consistent)",
            {"source_binding", "producer_fingerprint"},
            _wrong_run_binding,
        ),
        (
            "wrong_unit_key",
            "a unit row carries another unit key",
            {"unit_identity"},
            _wrong_unit_key,
        ),
        (
            "missing_member",
            "a DatasetVersion lacks one expected unit (consistent)",
            {"composition_members"},
            _missing_member,
        ),
        (
            "foreign_member",
            "a DatasetVersion holds a unit of another RobotRun (consistent)",
            {"composition_members"},
            _foreign_member,
        ),
        (
            "composition_checksum",
            "a composition_checksum that its pins do not produce",
            {"composition_checksum"},
            _composition_checksum,
        ),
        (
            "summary",
            "a DatasetVersion observation_count off by one",
            {"composition_summary"},
            _summary,
        ),
        (
            "action_value",
            "one Episode action value changed (consistent)",
            {"episode_occurrences"},
            _action_value,
        ),
        (
            "dropped_event",
            "an Episode drops its end marker event (consistent)",
            {"episode_occurrences"},
            _dropped_event,
        ),
        (
            "unbound_fingerprint",
            "build_config changed without recomputing the fingerprint (consistent bytes)",
            {"producer_fingerprint", "producer_build_config"},
            _unbound_fingerprint,
        ),
        (
            "non_canonical_manifest",
            "manifest bytes pretty-printed (consistent checksums)",
            {"manifest_canonical"},
            _non_canonical,
        ),
        (
            "dataset_field_in_manifest",
            "a manifest carries a dataset_version (consistent)",
            {"manifest_forbidden_field"},
            _dataset_field,
        ),
        (
            "payload_object_missing",
            "a referenced payload object is absent from the store",
            {"payload_object_missing"},
            _payload_object_missing,
        ),
        (
            "manifest_object_missing",
            "a revision's manifest object is absent from the store",
            {"manifest_missing"},
            _manifest_object_missing,
        ),
        (
            "substituted_recording",
            "the RobotRun pins another recording (consistent)",
            {"robot_run_recording", "source_binding"},
            _substituted_recording,
        ),
        (
            "second_revision",
            "a contract unit has a second revision",
            {"revision_count"},
            _second_revision,
        ),
        (
            "payload_identity",
            "one recorded message referenced through two payload artifacts",
            {"payload_identity"},
            _payload_identity,
        ),
        (
            "payload_of_another_run",
            "one observation names another RobotRun's payload key (consistent)",
            {"payload_ref"},
            _payload_of_another_run,
        ),
        (
            "payload_by_location",
            "one observation names its payload by storage location, not Logical URI",
            {"payload_ref", "manifest_forbidden_field"},
            _payload_by_location,
        ),
    )
)


def run_platform_controls(
    obs: dict[str, Any],
    ctx: dict[str, Any],
    verify: Callable[[dict[str, Any]], set[str]],
) -> list[dict[str, Any]]:
    """Each control on a fresh copy; ``verify`` returns the violation codes."""
    results = []
    for control in PLATFORM_CONTROLS:
        corrupted = copy.deepcopy(obs)
        control.apply(corrupted, ctx)
        codes = verify(corrupted)
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


# ── Source controls ──────────────────────────────────────────────────────────
# Applied to the recording facts / lock / label artifact of a fixture.


def source_controls() -> tuple[Control, ...]:
    def drop_tf(state, _):
        tf = [e for e in state["facts"].entries if e.topic == "/tf"]
        state["facts"].entries.remove(tf[len(tf) // 2])

    def shift_camera(state, _):
        entry = next(
            e
            for e in state["facts"].entries
            if e.topic.startswith("/camera/") and e.topic.endswith("/compressed")
        )
        entry.stamp_ns += 1

    def change_image(state, _):
        entry = next(
            e for e in state["facts"].entries if e.topic == "/lidar/top/points"
        )
        entry.image_checksum = rules.checksum(b"one changed byte")

    def relock(state, _):
        state["lock"]["sha256"] = rules.checksum(b"re-locked")

    def shift_label(state, _):
        artifact = json.loads(state["labels"])
        artifact["samples"][0]["anchor_timestamp_ns"] += 1
        state["labels"] = json.dumps(artifact).encode()

    def drop_label(state, _):
        artifact = json.loads(state["labels"])
        sample = next(s for s in artifact["samples"] if s["labels"])
        sample["labels"].pop()
        state["labels"] = json.dumps(artifact).encode()

    return tuple(
        Control(name, fault, frozenset(codes), fn)
        for name, fault, codes, fn in (
            (
                "source_dropped_pose",
                "the recording drops one /tf message",
                {"source_message_count"},
                drop_tf,
            ),
            (
                "source_timestamp_shift_1ns",
                "one camera header stamp shifted by 1 ns",
                {"source_timestamp"},
                shift_camera,
            ),
            (
                "source_payload_byte",
                "one point cloud's bytes differ from its .pcd.bin",
                {"source_payload_bytes"},
                change_image,
            ),
            (
                "lock_substituted",
                "the lock pins other recording bytes",
                {"lock_recording_checksum"},
                relock,
            ),
            (
                "label_anchor_shift_1ns",
                "one label anchor shifted by 1 ns",
                {"source_label_anchor", "lock_labels_checksum"},
                shift_label,
            ),
            (
                "label_dropped",
                "one label dropped",
                {"source_label_content", "lock_labels_checksum", "lock_label_count"},
                drop_label,
            ),
        )
    )


def expectation_controls() -> tuple[Control, ...]:
    def drift(state, _):
        fixture = next(iter(state["committed"]["fixtures"].values()))
        fixture["scene"]["window"]["end_timestamp_ns"] += 1

    def config_drift(state, _):
        state["committed"]["derived_from"]["scene_build_config"] = rules.checksum(
            b"another config"
        )

    return (
        Control(
            "expectations_window_drift",
            "the committed Scene window differs by 1 ns from the derivation",
            frozenset({"expectations_drift"}),
            drift,
        ),
        Control(
            "expectations_config_drift",
            "the committed expectations were derived from another build configuration",
            frozenset({"expectations_drift"}),
            config_drift,
        ),
    )
