"""Negative controls of the temporal lineage verifier (tools/reference/temporal_lineage.py).

A control forges one fault in a read-only overlay of a state that passes, and names
the finding code it must produce. A forgery is self-consistent: bytes, checksums,
records and pins all agree, so a wrong scope reaches the comparison with the manifest
instead of stopping at an outdated checksum. Only the controls whose fault is the
integrity of the bytes break a pin. Nothing is written to the store under verification.

The same controls run over the synthetic state (`selftest`, the unit tests) and over a
real platform state (`verify`).
"""

from __future__ import annotations

import copy
import io
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import polars as pl
import pyarrow.parquet as pq
from temporal_lineage import (
    SCENE_TABLES,
    LineageSource,
    ReadStore,
    TemporalLineageVerifier,
    sha256,
)
from verification.findings import Findings

from sceneops_analytics import scene_table_schema_stamp
from sceneops_analytics.parquet_contract import TABLE_SCHEMA_METADATA_KEY
from sceneops_analytics.learning_tables import learning_table_schema_stamp
from sceneops_core.common.derived_ids import (
    aligned_episode_artifact_id,
    derived_artifact_id,
)

# A scope no unit was issued: structurally valid, and nobody's.
FOREIGN_SCOPE = ["recording", "not-this-unit"]


class ControlNotApplicable(Exception):
    """This state has nothing for the control's fault to change, so a pass would prove
    nothing. Reported as such, never as detected."""


class _OverlayStore:
    def __init__(self, base: ReadStore, objects: Mapping[str, bytes]) -> None:
        self._base = base
        self._objects = objects

    async def read_bytes(self, uri: str) -> bytes:
        if uri in self._objects:
            return self._objects[uri]
        return await self._base.read_bytes(uri)

    async def read_range(self, uri: str, offset: int, length: int) -> bytes:
        if uri in self._objects:
            return self._objects[uri][offset : offset + length]
        return await self._base.read_range(uri, offset, length)


@dataclass
class Overlay:
    """A source with forged objects, records and members in front of the real one."""

    base: LineageSource
    objects: dict[str, bytes] = field(default_factory=dict)
    records: dict[str, dict[str, Any]] = field(default_factory=dict)
    member_changes: dict[
        str, Callable[[list[dict[str, Any]]], list[dict[str, Any]]]
    ] = field(default_factory=dict)

    @property
    def store(self) -> ReadStore:
        return _OverlayStore(self.base.store, self.objects)

    def artifact_record(self, artifact_id: str) -> dict[str, Any] | None:
        if artifact_id in self.records:
            return self.records[artifact_id]
        return self.base.artifact_record(artifact_id)

    def members(
        self, domain: str, dataset_id: str, dataset_version: str
    ) -> list[dict[str, Any]] | None:
        rows = self.base.members(domain, dataset_id, dataset_version)
        change = self.member_changes.get(domain)
        return change(rows) if change and rows is not None else rows


def _dumps(document: Any) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode()


# ── aligned artifacts ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AlignedTarget:
    aligned_artifact_id: str
    # A unit other than the target's: its manifest artifact, checksum, Episode id and
    # the scope its manifest issues, when the state under verification has one.
    other_scope: list[str] | None = None
    other_episode_id: str | None = None
    other_source_artifact_id: str | None = None
    other_source_sha256: str | None = None
    other_source_uri: str | None = None


async def _forge_aligned(
    source: LineageSource, target: AlignedTarget, mutate: Callable[[dict], None]
) -> tuple[Overlay, str, str]:
    record = source.artifact_record(target.aligned_artifact_id)
    assert record is not None
    document = json.loads(await source.store.read_bytes(record["uri"]))
    mutate(document)
    data = _dumps(document)
    checksum = sha256(data)
    episode_id = document["alignedEpisode"]["episodeId"]
    artifact_id = aligned_episode_artifact_id(episode_id=episode_id, checksum=checksum)
    uri = f"{record['uri']}.forged-{checksum[7:19]}"
    revision = document["sourceRevision"]
    overlay = Overlay(
        source,
        objects={uri: data},
        records={
            artifact_id: {
                **record,
                "uri": uri,
                "checksum": checksum,
                "owner_id": episode_id,
                # What a forger would register alongside: consistent, so a control
                # shows what the manifest refutes, not what the record does.
                "metadata": {
                    "episode_id": episode_id,
                    "source_artifact_id": revision["sourceArtifactId"],
                    "source_manifest_sha256": revision["sourceManifestSha256"],
                    "source_clock": document["alignedEpisode"]["sourceClock"],
                },
            }
        },
    )
    return overlay, artifact_id, checksum


@dataclass(frozen=True)
class AlignedControl:
    name: str
    fault: str
    codes: frozenset[str]
    mutate: Callable[[dict, AlignedTarget], None]
    needs_other: bool = False


def _set_scope(document: dict, target: AlignedTarget) -> None:
    aligned = document["alignedEpisode"]
    aligned["clockScope"] = target.other_scope or FOREIGN_SCOPE
    if aligned["clockOrigin"] == "unix_epoch":
        aligned["clockOrigin"] = "source"


def _flip_origin(document: dict, target: AlignedTarget) -> None:
    aligned = document["alignedEpisode"]
    if aligned["clockOrigin"] == "unix_epoch":
        aligned["clockOrigin"], aligned["clockScope"] = "source", FOREIGN_SCOPE
    else:
        aligned["clockOrigin"] = {"source": "unit", "unit": "source"}[
            aligned["clockOrigin"]
        ]


def _set(path: tuple[str, ...], value: Any) -> Callable[[dict, AlignedTarget], None]:
    def mutate(document: dict, target: AlignedTarget) -> None:
        node = document
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value

    return mutate


def _other_episode(document: dict, target: AlignedTarget) -> None:
    document["alignedEpisode"]["episodeId"] = target.other_episode_id
    document["sourceRevision"]["episodeId"] = target.other_episode_id


def _other_revision(document: dict, target: AlignedTarget) -> None:
    document["sourceRevision"]["sourceArtifactId"] = target.other_source_artifact_id
    document["sourceRevision"]["sourceManifestSha256"] = target.other_source_sha256
    document["sourceRevision"]["episodeManifestUri"] = target.other_source_uri


def _shift_steps(document: dict, target: AlignedTarget) -> None:
    for step in document["alignedEpisode"]["steps"]:
        step["timestampUs"] += 1


ALIGNED_CONTROLS = (
    AlignedControl(
        "aligned_scope_of_another_unit",
        "a structurally valid scope another unit's manifest issues",
        frozenset({"aligned_clock_instance"}),
        _set_scope,
    ),
    AlignedControl(
        "aligned_other_origin",
        "the right scope on another origin",
        frozenset({"aligned_clock_instance"}),
        _flip_origin,
    ),
    AlignedControl(
        "aligned_undeclared_clock",
        "a clock id the manifest does not declare",
        frozenset({"aligned_clock_declared"}),
        _set(("alignedEpisode", "sourceClock"), "not.declared"),
    ),
    AlignedControl(
        "aligned_source_checksum",
        "a source revision whose bytes hash otherwise",
        frozenset({"source_revision_checksum"}),
        _set(("sourceRevision", "sourceManifestSha256"), "0" * 64),
    ),
    AlignedControl(
        "aligned_other_revision",
        "a source revision of another unit",
        frozenset({"episode_identity"}),
        _other_revision,
        needs_other=True,
    ),
    AlignedControl(
        "aligned_other_episode",
        "an artifact stating another Episode",
        frozenset({"episode_identity"}),
        _other_episode,
        needs_other=True,
    ),
    AlignedControl(
        "aligned_schema_v1",
        "a schema version this reader does not read",
        frozenset({"aligned_schema"}),
        _set(("schemaVersion",), "v1"),
    ),
    AlignedControl(
        "aligned_steps_shifted",
        "steps that are not what the recipe derives from the manifest",
        frozenset({"aligned_content"}),
        _shift_steps,
    ),
)


async def run_aligned_controls(
    source: LineageSource, target: AlignedTarget
) -> list[dict[str, Any]]:
    results = []
    for control in ALIGNED_CONTROLS:
        if control.needs_other and target.other_episode_id is None:
            continue

        def mutate(document: dict, control: AlignedControl = control) -> None:
            control.mutate(document, target)

        overlay, artifact_id, checksum = await _forge_aligned(source, target, mutate)
        findings = Findings()
        await TemporalLineageVerifier(overlay).verify_aligned(
            findings, aligned_artifact_id=artifact_id, checksum=checksum
        )
        results.append(_result(control.name, control.fault, control.codes, findings))
    results.append(await _aligned_bytes_control(source, target))
    return results


async def _aligned_bytes_control(
    source: LineageSource, target: AlignedTarget
) -> dict[str, Any]:
    record = source.artifact_record(target.aligned_artifact_id)
    assert record is not None
    data = await source.store.read_bytes(record["uri"])
    overlay = Overlay(source, objects={record["uri"]: data + b" "})
    findings = Findings()
    await TemporalLineageVerifier(overlay).verify_aligned(
        findings,
        aligned_artifact_id=target.aligned_artifact_id,
        checksum=record["checksum"],
    )
    return _result(
        "aligned_bytes_changed",
        "stored bytes that are not the pinned ones",
        frozenset({"aligned_checksum"}),
        findings,
    )


def _result(
    name: str, fault: str, codes: frozenset[str], findings: Findings
) -> dict[str, Any]:
    observed = findings.codes()
    return {
        "control": name,
        "fault": fault,
        "expected_codes": sorted(codes),
        "observed_codes": sorted(observed),
        "detected": bool(observed & codes),
    }


# ── learning exports ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LearningTarget:
    manifest_artifact_id: str


@dataclass
class _Export:
    """One forged export manifest, written last so every pin in it holds."""

    source: LineageSource
    record: dict[str, Any]
    document: dict[str, Any]
    objects: dict[str, bytes] = field(default_factory=dict)

    async def episodes_rows(self) -> list[dict[str, Any]]:
        uri = self.document["tableUris"]["learning_episodes"]
        data = await self.source.store.read_bytes(uri)
        return pl.read_parquet(io.BytesIO(data)).to_dicts()

    async def write_episodes(
        self, rows: list[dict[str, Any]], *, stamp: bool = True
    ) -> None:
        uri = self.document["tableUris"]["learning_episodes"]
        data = await self.source.store.read_bytes(uri)
        frame = pl.DataFrame(rows, schema=pl.read_parquet(io.BytesIO(data)).schema)
        buffer = io.BytesIO()
        frame.write_parquet(
            buffer,
            metadata=(
                {
                    TABLE_SCHEMA_METADATA_KEY: learning_table_schema_stamp(
                        "learning_episodes"
                    )
                }
                if stamp
                else None
            ),
        )
        forged = buffer.getvalue()
        checksum = sha256(forged)
        new_uri = f"{uri}.forged-{checksum[7:19]}"
        self.objects[new_uri] = forged
        self.document["tableUris"]["learning_episodes"] = new_uri
        self.document["tableChecksums"]["learning_episodes"] = checksum

    def overlay(self, artifact_id: str) -> tuple[Overlay, str]:
        data = _dumps(self.document)
        checksum = sha256(data)
        uri = f"{self.record['uri']}.forged-{checksum[7:19]}"
        return (
            Overlay(
                self.source,
                objects={**self.objects, uri: data},
                records={
                    artifact_id: {**self.record, "uri": uri, "checksum": checksum}
                },
            ),
            checksum,
        )


async def _shift_first_shard(export: _Export, table: str = "learningSteps") -> None:
    """Every step of the first shard one microsecond off, re-pinned. In an incremental
    export the first shard is one reused from the base."""
    shard = export.document["shardIndex"][table][0]
    data = await export.source.store.read_bytes(shard["uri"])
    arrow = pq.read_table(io.BytesIO(data))
    index = arrow.schema.get_field_index("timestamp_us")
    shifted = arrow.set_column(
        index,
        arrow.schema.field(index),
        [[v + 1 for v in arrow.column(index).to_pylist()]],
    )
    buffer = io.BytesIO()
    with pq.ParquetWriter(buffer, shifted.schema) as writer:
        offset = 0
        for member in shard["episodes"]:
            writer.write_table(shifted.slice(offset, member["rowCount"]))
            offset += member["rowCount"]
    forged = buffer.getvalue()
    checksum = sha256(forged)
    uri = f"{shard['uri']}.forged-{checksum[7:19]}"
    export.objects[uri] = forged
    shard.update(uri=uri, checksum=checksum, sizeBytes=len(forged))


def _other_row_scope(
    rows: list[dict[str, Any]], row: dict[str, Any]
) -> tuple[str, list[str]]:
    """The origin and scope another Episode of this export states, or nobody's."""
    for other in rows:
        if other["clock_scope"] and other["clock_scope"] != row["clock_scope"]:
            return other["clock_origin"], other["clock_scope"]
    return "source", FOREIGN_SCOPE


async def _learning_scope_of_another_unit(export: _Export) -> None:
    rows = await export.episodes_rows()
    rows[0]["clock_origin"], rows[0]["clock_scope"] = _other_row_scope(rows, rows[0])
    await export.write_episodes(rows)


async def _learning_other_clock(export: _Export) -> None:
    rows = await export.episodes_rows()
    rows[0]["source_clock"] = "not.declared"
    await export.write_episodes(rows)


async def _learning_other_origin(export: _Export) -> None:
    rows = await export.episodes_rows()
    row = next((r for r in rows if r["clock_scope"]), None)
    if row is None:
        rows[0]["clock_origin"], rows[0]["clock_scope"] = "source", FOREIGN_SCOPE
    else:
        row["clock_origin"] = {"source": "unit", "unit": "source"}[row["clock_origin"]]
    await export.write_episodes(rows)


async def _learning_metadata(export: _Export) -> None:
    rows = await export.episodes_rows()
    rows[0]["dt_us"] += 1
    await export.write_episodes(rows)


async def _learning_other_dataset_version(export: _Export) -> None:
    rows = await export.episodes_rows()
    rows[0]["dataset_version"] = "not-this-version"
    await export.write_episodes(rows)


async def _learning_unstamped(export: _Export) -> None:
    await export.write_episodes(await export.episodes_rows(), stamp=False)


async def _swap_row_groups(export: _Export) -> None:
    """The shard index maps each of two Episodes to the other's row group."""
    members = [
        member
        for shard in export.document["shardIndex"]["learningSteps"]
        for member in shard["episodes"]
    ]
    first, second = members[0], members[1]
    first["episodeRef"], second["episodeRef"] = (
        second["episodeRef"],
        first["episodeRef"],
    )


async def _shard_bytes(export: _Export) -> None:
    shard = export.document["shardIndex"]["learningSteps"][0]
    data = await export.source.store.read_bytes(shard["uri"])
    export.objects[shard["uri"]] = data + b" "


async def _schema_v1(export: _Export) -> None:
    export.document["schemaVersion"] = "v1"


async def _not_a_member(export: _Export) -> None:
    export.document["datasetVersion"] = "not-this-version"


@dataclass(frozen=True)
class LearningControl:
    name: str
    fault: str
    codes: frozenset[str]
    forge: Callable[[_Export], Awaitable[None]]
    needs_two: bool = False


LEARNING_CONTROLS = (
    LearningControl(
        "learning_scope_of_another_unit",
        "a learning_episodes row stating another Episode's scope",
        frozenset({"learning_clock_instance"}),
        _learning_scope_of_another_unit,
    ),
    LearningControl(
        "learning_other_clock",
        "a learning_episodes row on another clock id",
        frozenset({"learning_clock_instance"}),
        _learning_other_clock,
    ),
    LearningControl(
        "learning_other_origin",
        "a learning_episodes row with the right scope on another origin",
        frozenset({"learning_clock_instance"}),
        _learning_other_origin,
    ),
    LearningControl(
        "learning_metadata",
        "a learning_episodes row the AlignedEpisode does not state",
        frozenset({"learning_metadata"}),
        _learning_metadata,
    ),
    LearningControl(
        "learning_other_dataset_version",
        "a learning_episodes row of another DatasetVersion",
        frozenset({"learning_row_identity"}),
        _learning_other_dataset_version,
    ),
    LearningControl(
        "learning_table_unstamped",
        "a table that states no schema",
        frozenset({"learning_table_schema"}),
        _learning_unstamped,
    ),
    LearningControl(
        "learning_row_groups_swapped",
        "a row group of another Episode",
        frozenset({"learning_rows_owner"}),
        _swap_row_groups,
        needs_two=True,
    ),
    LearningControl(
        "learning_shard_bytes_changed",
        "a shard's stored bytes are not the pinned ones",
        frozenset({"learning_shard_checksum"}),
        _shard_bytes,
    ),
    LearningControl(
        "learning_step_timestamps_shifted",
        "a re-pinned shard whose steps are not the AlignedEpisode's",
        frozenset({"learning_steps"}),
        _shift_first_shard,
    ),
    LearningControl(
        "learning_export_schema_v1",
        "an export manifest of a schema this reader does not read",
        frozenset({"export_schema"}),
        _schema_v1,
    ),
    LearningControl(
        "learning_outside_the_dataset_version",
        "an export stating another DatasetVersion",
        frozenset({"learning_membership", "learning_row_identity"}),
        _not_a_member,
    ),
)


async def run_learning_controls(
    source: LineageSource, target: LearningTarget
) -> list[dict[str, Any]]:
    record = source.artifact_record(target.manifest_artifact_id)
    assert record is not None
    original = json.loads(await source.store.read_bytes(record["uri"]))
    results = []
    for control in LEARNING_CONTROLS:
        if control.needs_two and len(original["inputs"]) < 2:
            continue
        export = _Export(source, record, copy.deepcopy(original))
        await control.forge(export)
        overlay, checksum = export.overlay(target.manifest_artifact_id)
        findings = Findings()
        await TemporalLineageVerifier(overlay).verify_learning_export(
            findings,
            manifest_artifact_id=target.manifest_artifact_id,
            checksum=checksum,
        )
        results.append(_result(control.name, control.fault, control.codes, findings))
    return results


# ── Scene analytics ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SceneTarget:
    dataset_id: str
    dataset_version: str
    table_uris: Mapping[str, str]


@dataclass
class _Snapshot:
    source: LineageSource
    target: SceneTarget
    objects: dict[str, bytes] = field(default_factory=dict)
    records: dict[str, dict[str, Any]] = field(default_factory=dict)
    uris: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.uris = dict(self.target.table_uris)

    async def rows(self, table: str) -> list[dict[str, Any]]:
        data = await self.source.store.read_bytes(self.target.table_uris[table])
        rows = pl.read_parquet(io.BytesIO(data)).to_dicts()
        if not rows:
            # A Scene with no source-defined groups has no keyframes: a valid table
            # with nothing in it to forge.
            raise ControlNotApplicable(f"{table} has no rows")
        return rows

    async def write(
        self, table: str, rows: list[dict[str, Any]], *, stamp: bool = True
    ) -> None:
        original = self.target.table_uris[table]
        data = await self.source.store.read_bytes(original)
        frame = pl.DataFrame(rows, schema=pl.read_parquet(io.BytesIO(data)).schema)
        buffer = io.BytesIO()
        frame.write_parquet(
            buffer,
            metadata=(
                {TABLE_SCHEMA_METADATA_KEY: scene_table_schema_stamp(table)}
                if stamp
                else None
            ),
        )
        forged = buffer.getvalue()
        checksum = sha256(forged)
        uri = f"{original}.forged-{checksum[7:19]}"
        self.objects[uri] = forged
        self.uris[table] = uri
        artifact_id = derived_artifact_id(
            prefix="analyticstable",
            logical_id=f"{self.target.dataset_id}:{self.target.dataset_version}:{table}",
            checksum=checksum,
        )
        self.records[artifact_id] = {
            "artifact_id": artifact_id,
            "kind": "analytics_table",
            "uri": uri,
            "checksum": checksum,
        }


def _first_scene_rows(
    rows: list[dict[str, Any]], *, scope_column: str
) -> tuple[list[dict[str, Any]], list[str], str]:
    """The rows of one (Scene, clock), a scope another Scene's row states and that
    row's origin. A Scene with a scope when there is one: a unix_epoch instance has
    none to replace."""
    clock_column = {
        "window_clock_scope": "window_clock",
        "clock_scope": "source_clock",
    }[scope_column]
    origin_column = {
        "window_clock_scope": "window_clock_origin",
        "clock_scope": "clock_origin",
    }[scope_column]
    chosen = next((r for r in rows if r[scope_column]), rows[0])
    selected = [
        r
        for r in rows
        if r["scene_id"] == chosen["scene_id"]
        and r[clock_column] == chosen[clock_column]
    ]
    for other in rows:
        if other["scene_id"] != chosen["scene_id"] and other[scope_column]:
            if other[scope_column] != chosen[scope_column]:
                return selected, other[scope_column], other[origin_column]
    return selected, FOREIGN_SCOPE, "source"


async def _scene_window_scope(snapshot: _Snapshot) -> None:
    rows = await snapshot.rows("scenes")
    selected, scope, origin = _first_scene_rows(rows, scope_column="window_clock_scope")
    for row in selected:
        row["window_clock_scope"], row["window_clock_origin"] = scope, origin
    await snapshot.write("scenes", rows)


async def _scene_window_clock(snapshot: _Snapshot) -> None:
    rows = await snapshot.rows("scenes")
    rows[0]["window_clock"] = "not.declared"
    await snapshot.write("scenes", rows)


async def _scene_window_origin(snapshot: _Snapshot) -> None:
    rows = await snapshot.rows("scenes")
    selected, _, _ = _first_scene_rows(rows, scope_column="window_clock_scope")
    for row in selected:
        if row["window_clock_scope"]:
            row["window_clock_origin"] = {"source": "unit", "unit": "source"}[
                row["window_clock_origin"]
            ]
        else:
            row["window_clock_origin"], row["window_clock_scope"] = (
                "source",
                FOREIGN_SCOPE,
            )
    await snapshot.write("scenes", rows)


async def _scene_window_time(snapshot: _Snapshot) -> None:
    rows = await snapshot.rows("scenes")
    rows[0]["window_start_timestamp_ns"] += 1
    await snapshot.write("scenes", rows)


async def _scene_other_revision(snapshot: _Snapshot) -> None:
    rows = await snapshot.rows("scenes")
    rows[0]["manifest_checksum"] = "sha256:" + "0" * 64
    await snapshot.write("scenes", rows)


async def _scene_producer(snapshot: _Snapshot) -> None:
    rows = await snapshot.rows("scenes")
    rows[0]["producer_fingerprint"] = "sha256:" + "0" * 64
    await snapshot.write("scenes", rows)


async def _scene_other_source(snapshot: _Snapshot) -> None:
    """A Scene stating another source: refused structurally when its id cannot derive
    from it, and held to the pinned revision either way."""
    rows = await snapshot.rows("scenes")
    row = rows[0]
    row["robot_run_id"], row["content_id"] = row["content_id"], row["robot_run_id"]
    row["source_kind"] = "content" if row["source_kind"] == "recording" else "recording"
    await snapshot.write("scenes", rows)


async def _scene_not_a_member(snapshot: _Snapshot) -> None:
    rows = await snapshot.rows("scenes")
    rows[0]["dataset_version"] = "not-this-version"
    await snapshot.write("scenes", rows)


async def _scene_unstamped(snapshot: _Snapshot) -> None:
    await snapshot.write("scenes", await snapshot.rows("scenes"), stamp=False)


async def _observation_scope(snapshot: _Snapshot) -> None:
    rows = await snapshot.rows("observations")
    selected, scope, origin = _first_scene_rows(rows, scope_column="clock_scope")
    for row in selected:
        row["clock_scope"], row["clock_origin"] = scope, origin
    await snapshot.write("observations", rows)


async def _observation_window_substituted(snapshot: _Snapshot) -> None:
    """Every observation of a Scene stated on the Scene's window instance, whatever
    its channel's clock: the shortcut a table with one context per Scene would take."""
    scenes = {r["scene_id"]: r for r in await snapshot.rows("scenes")}
    rows = await snapshot.rows("observations")
    changed = False
    for row in rows:
        window = scenes[row["scene_id"]]
        if row["source_clock"] != window["window_clock"]:
            changed = True
            row["source_clock"] = window["window_clock"]
            row["clock_origin"] = window["window_clock_origin"]
            row["clock_scope"] = window["window_clock_scope"]
    if not changed:
        raise ControlNotApplicable("every observation is on its Scene's window clock")
    await snapshot.write("observations", rows)


async def _observation_time(snapshot: _Snapshot) -> None:
    rows = await snapshot.rows("observations")
    rows[0]["timestamp_ns"] += 1
    await snapshot.write("observations", rows)


async def _observation_channel(snapshot: _Snapshot) -> None:
    rows = await snapshot.rows("observations")
    other = next(
        (r["channel"] for r in rows if r["channel"] != rows[0]["channel"]), None
    )
    if other is None:
        raise ControlNotApplicable("every observation is on one channel")
    rows[0]["channel"] = other
    await snapshot.write("observations", rows)


async def _observation_payload(snapshot: _Snapshot) -> None:
    rows = await snapshot.rows("observations")
    rows[0]["payload_checksum"] = "sha256:" + "0" * 64
    await snapshot.write("observations", rows)


async def _observation_dropped(snapshot: _Snapshot) -> None:
    await snapshot.write("observations", (await snapshot.rows("observations"))[1:])


async def _keyframe_scope(snapshot: _Snapshot) -> None:
    rows = await snapshot.rows("keyframes")
    selected, scope, origin = _first_scene_rows(rows, scope_column="clock_scope")
    for row in selected:
        row["clock_scope"], row["clock_origin"] = scope, origin
    await snapshot.write("keyframes", rows)


async def _keyframe_clock(snapshot: _Snapshot) -> None:
    rows = await snapshot.rows("keyframes")
    selected, _, _ = _first_scene_rows(rows, scope_column="clock_scope")
    for row in selected:
        row["source_clock"] = "not.declared"
    await snapshot.write("keyframes", rows)


async def _keyframe_members(snapshot: _Snapshot) -> None:
    rows = await snapshot.rows("keyframes")
    rows[0]["observation_ids"] = rows[0]["observation_ids"][1:] or [
        "not-an-observation"
    ]
    await snapshot.write("keyframes", rows)


async def _keyframe_dropped(snapshot: _Snapshot) -> None:
    await snapshot.write("keyframes", (await snapshot.rows("keyframes"))[1:])


async def _table_not_registered(snapshot: _Snapshot) -> None:
    table = "keyframes"
    await snapshot.write(table, await snapshot.rows(table))
    snapshot.records.clear()


@dataclass(frozen=True)
class SceneControl:
    name: str
    fault: str
    codes: frozenset[str]
    forge: Callable[[_Snapshot], Awaitable[None]]


SCENE_CONTROLS = (
    SceneControl(
        "scene_window_scope_of_another_unit",
        "a window stating another Scene's scope",
        frozenset({"scene_window_instance"}),
        _scene_window_scope,
    ),
    SceneControl(
        "scene_window_other_clock",
        "a window on another clock id",
        frozenset({"scene_window_instance"}),
        _scene_window_clock,
    ),
    SceneControl(
        "scene_window_other_origin",
        "a window with the right scope on another origin",
        frozenset({"scene_window_instance"}),
        _scene_window_origin,
    ),
    SceneControl(
        "scene_window_time",
        "a window the manifest does not declare",
        frozenset({"scene_window_time"}),
        _scene_window_time,
    ),
    SceneControl(
        "scene_other_revision",
        "a row stating a revision the DatasetVersion does not pin",
        frozenset({"scene_revision", "scene_table_structure"}),
        _scene_other_revision,
    ),
    SceneControl(
        "scene_other_producer",
        "a row stating a producer the revision does not name",
        frozenset({"scene_provenance"}),
        _scene_producer,
    ),
    SceneControl(
        "scene_other_source",
        "a row pairing the Scene with a source of the other kind",
        frozenset({"scene_provenance", "scene_table_structure"}),
        _scene_other_source,
    ),
    SceneControl(
        "scene_not_a_member",
        "a row of another DatasetVersion",
        frozenset({"scene_membership"}),
        _scene_not_a_member,
    ),
    SceneControl(
        "scene_table_unstamped",
        "a table that states no schema",
        frozenset({"scene_table_schema"}),
        _scene_unstamped,
    ),
    SceneControl(
        "observation_scope_of_another_unit",
        "observations stating another Scene's scope",
        frozenset({"observation_instance"}),
        _observation_scope,
    ),
    SceneControl(
        "observation_window_substituted",
        "observations stating the window's instance instead of their channel's",
        frozenset({"observation_instance"}),
        _observation_window_substituted,
    ),
    SceneControl(
        "observation_time",
        "an observation at another time",
        frozenset({"observation_content"}),
        _observation_time,
    ),
    SceneControl(
        "observation_other_channel",
        "an observation attributed to another channel",
        frozenset({"observation_content"}),
        _observation_channel,
    ),
    SceneControl(
        "observation_other_payload",
        "an observation with another immutable payload reference",
        frozenset({"observation_content"}),
        _observation_payload,
    ),
    SceneControl(
        "observation_dropped",
        "a table missing an observation",
        frozenset({"observation_rows"}),
        _observation_dropped,
    ),
    SceneControl(
        "keyframe_scope_of_another_unit",
        "keyframes stating another Scene's scope",
        frozenset({"keyframe_instance"}),
        _keyframe_scope,
    ),
    SceneControl(
        "keyframe_other_clock",
        "keyframes on a clock their group does not declare",
        frozenset({"keyframe_instance"}),
        _keyframe_clock,
    ),
    SceneControl(
        "keyframe_other_members",
        "a keyframe with other observation associations",
        frozenset({"keyframe_content"}),
        _keyframe_members,
    ),
    SceneControl(
        "keyframe_dropped",
        "a table missing a keyframe",
        frozenset({"keyframe_rows"}),
        _keyframe_dropped,
    ),
    SceneControl(
        "table_not_registered",
        "bytes no ArtifactRecord of this DatasetVersion names",
        frozenset({"scene_table_record"}),
        _table_not_registered,
    ),
)


async def run_scene_controls(
    source: LineageSource, target: SceneTarget
) -> list[dict[str, Any]]:
    results = []
    for control in SCENE_CONTROLS:
        snapshot = _Snapshot(source, target)
        try:
            await control.forge(snapshot)
        except ControlNotApplicable as exc:
            results.append(
                {
                    "control": control.name,
                    "fault": control.fault,
                    "not_applicable": str(exc),
                    "detected": False,
                }
            )
            continue
        overlay = Overlay(source, objects=snapshot.objects, records=snapshot.records)
        findings = Findings()
        await TemporalLineageVerifier(overlay).verify_scene_analytics(
            findings,
            dataset_id=target.dataset_id,
            dataset_version=target.dataset_version,
            table_uris=snapshot.uris,
        )
        results.append(_result(control.name, control.fault, control.codes, findings))
    return results


__all__ = [
    "ALIGNED_CONTROLS",
    "AlignedTarget",
    "ControlNotApplicable",
    "FOREIGN_SCOPE",
    "LEARNING_CONTROLS",
    "LearningTarget",
    "Overlay",
    "SCENE_CONTROLS",
    "SCENE_TABLES",
    "SceneTarget",
    "run_aligned_controls",
    "run_learning_controls",
    "run_scene_controls",
]
