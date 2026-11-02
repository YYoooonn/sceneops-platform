"""Pure Postgres/ArtifactStore record -> columnar table builders, and the persisted
contract of the Scene analytics tables.

No I/O in the builders -- callers load ``SceneRevisionRecord`` rows (Postgres) and
verified ``SceneManifest`` revisions (ArtifactStore) themselves and pass them in as
``SceneRevisionContent``. Keeping these functions pure makes them trivially
unit-testable and reusable outside the job/worker runtime (e.g. from a notebook, a
future Spark/dbt stage, or a CLI).

Scene tables are observation-centric like the canonical Scene: every observation is
a row with its own source timestamp and clock, and keyframes are a separate table
over the source-defined grouping. Clocks are never converted.

Every declared timestamp states its whole clock instance
(``sceneops_core.temporal.ClockInstance``): the clock id with its origin and scope.
Timestamps are comparable only when their instances are equal, so equal clock ids
and equal values prove nothing. The instance is always the one the row's own
manifest declares through ``SceneManifest.clock_instance``; nothing here derives a
scope, reads its elements or inherits another row's. The Scene window's instance
and an observation's can differ, because a window may be on a clock no observation
uses.

``source_kind``, ``robot_run_id`` and ``content_id`` on ``scenes`` are provenance:
copied from the revision record, never a part of comparability and never a branch.

An observation's payload is exported as its reference, whatever the source: Logical
URI, checksum, size, media type and, when it selects part of the object, the
selector as canonical JSON. No payload is read.

Persisted contract (ADR-020). A table states its schema in its Parquet footer, and a
reader refuses one that does not state exactly this table of the current version
with this version's columns. Compatible-looking columns are never evidence of a
version. The checks are structural: they cannot establish that a stated scope
belongs to the Scene stating it; that takes the Scene's canonical manifest.
"""

from __future__ import annotations

import io
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq

from sceneops_core.common.canonical_json import canonical_json_bytes
from sceneops_core.robots.schemas import MissionRecord, RobotStateRecord
from sceneops_core.scenes.analytics import SCENE_ANALYTICS_SCHEMA_VERSION
from sceneops_core.scenes.schemas import (
    SceneManifest,
    SceneRecord,
    SceneRevisionRecord,
    project_scene_revision,
)
from sceneops_core.temporal import (
    ClockDomainError,
    ClockInstance,
    restore_clock_instance,
)

from .parquet_contract import (
    TABLE_SCHEMA_METADATA_KEY,
    arrow_columns,
    declared_columns,
    stated_table_schema,
)

SCENE_TABLE_SCHEMA_FAMILY = "scene_analytics"


class SceneRevisionPairingError(ValueError):
    """A Scene revision record and a manifest are not one revision."""


class UnsupportedSceneAnalyticsSchemaError(Exception):
    """A Parquet file is not this table of the schema version this code reads, or
    states none. It is refused, not read with part of its contract missing; the
    object stays as it is."""


class SceneAnalyticsIntegrityError(Exception):
    """A table of the current schema holds rows that schema does not allow: a
    timestamp with no valid clock instance, a declared clock of one Scene stated
    with two instances, or a Scene with no single valid source."""


SCENES_SCHEMA: dict[str, pl.PolarsDataType] = {
    "dataset_id": pl.Utf8,
    "dataset_version": pl.Utf8,
    "scene_id": pl.Utf8,
    "source_kind": pl.Utf8,
    "robot_run_id": pl.Utf8,
    "content_id": pl.Utf8,
    "unit_key": pl.Utf8,
    "producer_fingerprint": pl.Utf8,
    "manifest_artifact_id": pl.Utf8,
    "manifest_checksum": pl.Utf8,
    "window_clock": pl.Utf8,
    "window_clock_origin": pl.Utf8,
    "window_clock_scope": pl.List(pl.Utf8),
    "window_start_timestamp_ns": pl.Int64,
    "window_end_timestamp_ns": pl.Int64,
    "observation_count": pl.Int64,
    "keyframe_count": pl.Int64,
    "observed_channels": pl.List(pl.Utf8),
}

OBSERVATIONS_SCHEMA: dict[str, pl.PolarsDataType] = {
    "dataset_id": pl.Utf8,
    "dataset_version": pl.Utf8,
    "scene_id": pl.Utf8,
    "observation_id": pl.Utf8,
    "channel": pl.Utf8,
    "modality": pl.Utf8,
    "timestamp_ns": pl.Int64,
    "source_clock": pl.Utf8,
    "clock_origin": pl.Utf8,
    "clock_scope": pl.List(pl.Utf8),
    "payload_uri": pl.Utf8,
    "payload_checksum": pl.Utf8,
    "payload_media_type": pl.Utf8,
    "payload_size_bytes": pl.Int64,
    "payload_selector": pl.Utf8,
    "calibration_id": pl.Utf8,
    "ego_pose_id": pl.Utf8,
}

KEYFRAMES_SCHEMA: dict[str, pl.PolarsDataType] = {
    "dataset_id": pl.Utf8,
    "dataset_version": pl.Utf8,
    "scene_id": pl.Utf8,
    "group_id": pl.Utf8,
    "timestamp_ns": pl.Int64,
    "source_clock": pl.Utf8,
    "clock_origin": pl.Utf8,
    "clock_scope": pl.List(pl.Utf8),
    "observation_ids": pl.List(pl.Utf8),
}

SCENE_TABLE_SCHEMAS: dict[str, dict[str, pl.PolarsDataType]] = {
    "scenes": SCENES_SCHEMA,
    "observations": OBSERVATIONS_SCHEMA,
    "keyframes": KEYFRAMES_SCHEMA,
}

TABLE_BUILDERS = tuple(SCENE_TABLE_SCHEMAS)

# A row's place in a DatasetVersion. One version pins one revision of a Scene, so
# one declared clock of a Scene has one instance under this key.
_SCENE_KEY = ("dataset_id", "dataset_version", "scene_id")

# (clock, origin, scope) of the timestamp each table declares.
_CLOCK_COLUMNS = {
    "scenes": ("window_clock", "window_clock_origin", "window_clock_scope"),
    "observations": ("source_clock", "clock_origin", "clock_scope"),
    "keyframes": ("source_clock", "clock_origin", "clock_scope"),
}

_REQUIRED_COLUMNS = {
    "scenes": (
        *_SCENE_KEY,
        "source_kind",
        "unit_key",
        "producer_fingerprint",
        "manifest_artifact_id",
        "manifest_checksum",
        *_CLOCK_COLUMNS["scenes"],
        "window_start_timestamp_ns",
        "window_end_timestamp_ns",
    ),
    "observations": (
        *_SCENE_KEY,
        "observation_id",
        "channel",
        "timestamp_ns",
        *_CLOCK_COLUMNS["observations"],
    ),
    "keyframes": (
        *_SCENE_KEY,
        "group_id",
        "timestamp_ns",
        *_CLOCK_COLUMNS["keyframes"],
    ),
}

# What the first schema wrote, with no stamp to say so. An unstamped file of exactly
# these columns is an old Scene table, whatever a caller means to do with it.
_V1_SCENE_TABLE_COLUMNS = {
    "scenes": (
        "dataset_id",
        "dataset_version",
        "scene_id",
        "robot_run_id",
        "unit_key",
        "producer_fingerprint",
        "manifest_artifact_id",
        "manifest_checksum",
        "window_clock",
        "window_start_timestamp_ns",
        "window_end_timestamp_ns",
        "observation_count",
        "keyframe_count",
        "observed_channels",
    ),
    "observations": (
        "dataset_id",
        "dataset_version",
        "scene_id",
        "observation_id",
        "channel",
        "modality",
        "timestamp_ns",
        "source_clock",
        "payload_uri",
        "payload_checksum",
        "payload_media_type",
        "payload_size_bytes",
        "payload_selector",
        "calibration_id",
        "ego_pose_id",
    ),
    "keyframes": (
        "dataset_id",
        "dataset_version",
        "scene_id",
        "group_id",
        "timestamp_ns",
        "source_clock",
        "observation_ids",
    ),
}


@dataclass(frozen=True)
class SceneRevisionContent:
    """One Scene revision as the tables read it: a registered revision record and
    the manifest it pins, proven at construction to be one revision. So no row of
    any table can state one Scene's id beside another Scene's clocks, and a builder
    does not have to prove it again.

    The manifest's checksum is recomputed rather than taken from the caller: the
    record names a revision by exactly that value."""

    record: SceneRevisionRecord
    manifest: SceneManifest

    def __post_init__(self) -> None:
        checksum = self.manifest.checksum()
        if checksum != self.record.manifest_checksum:
            raise SceneRevisionPairingError(
                f"Scene {self.record.scene_id!r} pins revision "
                f"{self.record.manifest_checksum}, the manifest is {checksum}"
            )
        try:
            projected = project_scene_revision(
                manifest=self.manifest,
                manifest_artifact_id=self.record.manifest_artifact_id,
                manifest_checksum=checksum,
            )
        except ValueError as exc:
            raise SceneRevisionPairingError(
                f"the manifest of revision {checksum} is not a revision of Scene "
                f"{self.record.scene_id!r}: {exc}"
            ) from exc
        if projected.projection() != self.record.projection():
            raise SceneRevisionPairingError(
                f"Scene {self.record.scene_id!r} revision {checksum}: the record "
                f"{self.record.projection()!r} is not the manifest's projection "
                f"{projected.projection()!r}"
            )

    def clock_instances(self) -> dict[str, ClockInstance]:
        """Each clock this Scene declares, as the manifest declares it."""
        return {
            domain.clock: self.manifest.clock_instance(domain.clock)
            for domain in self.manifest.clock_domains
        }


def build_scenes_table(
    *,
    dataset_id: str,
    dataset_version: str,
    scenes: Sequence[SceneRevisionContent],
) -> pl.DataFrame:
    """One row per Scene revision the DatasetVersion pins."""
    rows = []
    for scene in scenes:
        record = scene.record
        window = scene.manifest.declared_window()
        instance = scene.manifest.clock_instance(window.source_clock)
        rows.append(
            {
                "dataset_id": dataset_id,
                "dataset_version": dataset_version,
                "scene_id": record.scene_id,
                "source_kind": record.source_kind,
                "robot_run_id": record.robot_run_id,
                "content_id": record.content_id,
                "unit_key": record.unit_key,
                "producer_fingerprint": record.producer_fingerprint,
                "manifest_artifact_id": record.manifest_artifact_id,
                "manifest_checksum": record.manifest_checksum,
                "window_clock": instance.clock,
                "window_clock_origin": instance.origin.value,
                "window_clock_scope": list(instance.scope),
                "window_start_timestamp_ns": window.start_timestamp_ns,
                "window_end_timestamp_ns": window.end_timestamp_ns,
                "observation_count": record.observation_count,
                "keyframe_count": record.keyframe_count,
                "observed_channels": record.observed_channels,
            }
        )
    return pl.DataFrame(rows, schema=SCENES_SCHEMA)


def build_observations_table(
    *,
    dataset_id: str,
    dataset_version: str,
    scenes: Sequence[SceneRevisionContent],
) -> pl.DataFrame:
    rows = []
    for scene in scenes:
        manifest = scene.manifest
        modality = {c.channel: c.modality.value for c in manifest.channels}
        clock = {c.channel: c.source_clock for c in manifest.channels}
        instances = scene.clock_instances()
        for o in manifest.observations:
            instance = instances[clock[o.channel]]
            rows.append(
                {
                    "dataset_id": dataset_id,
                    "dataset_version": dataset_version,
                    "scene_id": scene.record.scene_id,
                    "observation_id": o.observation_id,
                    "channel": o.channel,
                    "modality": modality[o.channel],
                    "timestamp_ns": o.timestamp_ns,
                    "source_clock": instance.clock,
                    "clock_origin": instance.origin.value,
                    "clock_scope": list(instance.scope),
                    "payload_uri": o.payload.uri,
                    "payload_checksum": o.payload.checksum,
                    "payload_media_type": o.payload.media_type,
                    "payload_size_bytes": o.payload.size_bytes,
                    "payload_selector": (
                        canonical_json_bytes(
                            o.payload.selector.model_dump(mode="json")
                        ).decode()
                        if o.payload.selector is not None
                        else None
                    ),
                    "calibration_id": o.calibration_id,
                    "ego_pose_id": o.ego_pose_id,
                }
            )
    return pl.DataFrame(rows, schema=OBSERVATIONS_SCHEMA)


def build_keyframes_table(
    *,
    dataset_id: str,
    dataset_version: str,
    scenes: Sequence[SceneRevisionContent],
) -> pl.DataFrame:
    rows = []
    for scene in scenes:
        instances = scene.clock_instances()
        for g in scene.manifest.keyframes():
            instance = instances[g.source_clock]
            rows.append(
                {
                    "dataset_id": dataset_id,
                    "dataset_version": dataset_version,
                    "scene_id": scene.record.scene_id,
                    "group_id": g.group_id,
                    "timestamp_ns": g.timestamp_ns,
                    "source_clock": instance.clock,
                    "clock_origin": instance.origin.value,
                    "clock_scope": list(instance.scope),
                    "observation_ids": list(g.observation_ids),
                }
            )
    return pl.DataFrame(rows, schema=KEYFRAMES_SCHEMA)


# ── Persisted contract ──


def _schema_of(table_name: str) -> dict[str, pl.PolarsDataType]:
    try:
        return SCENE_TABLE_SCHEMAS[table_name]
    except KeyError:
        raise ValueError(
            f"{table_name!r} is not a Scene analytics table; "
            f"they are {sorted(SCENE_TABLE_SCHEMAS)}"
        ) from None


def scene_table_schema_stamp(table_name: str) -> str:
    """What a table's footer states under ``TABLE_SCHEMA_METADATA_KEY``: its family,
    the schema version and the table, so one table of a snapshot cannot stand in
    for another either."""
    _schema_of(table_name)
    return f"{SCENE_TABLE_SCHEMA_FAMILY}/{SCENE_ANALYTICS_SCHEMA_VERSION}/{table_name}"


def scene_table_columns(table_name: str) -> list[tuple[str, str]]:
    """The exact ``(column, Arrow type)`` contract of ``table_name`` under
    ``SCENE_ANALYTICS_SCHEMA_VERSION``, in column order."""
    return declared_columns(_schema_of(table_name))


def _check_required_values(table_name: str, rows: pl.LazyFrame, where: str) -> None:
    counts = (
        rows.select(pl.col(c).null_count() for c in _REQUIRED_COLUMNS[table_name])
        .collect()
        .row(0, named=True)
    )
    missing = sorted(column for column, count in counts.items() if count)
    if missing:
        raise SceneAnalyticsIntegrityError(
            f"{where}: {table_name} rows have no value for required {missing}"
        )


def _check_scene_rows(rows: pl.LazyFrame, where: str) -> None:
    """One row per Scene of a version, whose source is the Scene's own. The record
    holds what a valid source is: this compares no source kind."""
    seen: set[tuple[str, str, str]] = set()
    frame = rows.select(
        *_SCENE_KEY, "source_kind", "robot_run_id", "content_id", "unit_key"
    ).collect()
    for row in frame.iter_rows(named=True):
        key = (row["dataset_id"], row["dataset_version"], row["scene_id"])
        if key in seen:
            raise SceneAnalyticsIntegrityError(
                f"{where}: more than one scenes row for {key!r}"
            )
        seen.add(key)
        try:
            SceneRecord(
                scene_id=row["scene_id"],
                source_kind=row["source_kind"],
                robot_run_id=row["robot_run_id"],
                content_id=row["content_id"],
                unit_key=row["unit_key"],
            )
        except ValueError as exc:
            raise SceneAnalyticsIntegrityError(
                f"{where}: scenes row for {key!r} states no valid source: {exc}"
            ) from exc


DeclaredInstances = dict[tuple[str, str, str, str], ClockInstance]


def _merge_instance(
    instances: DeclaredInstances,
    key: tuple[str, str, str, str],
    instance: ClockInstance,
    where: str,
) -> None:
    known = instances.setdefault(key, instance)
    if known != instance:
        raise SceneAnalyticsIntegrityError(
            f"{where}: Scene {key[2]!r} states clock {key[3]!r} with two instances: "
            f"{known!r} and {instance!r}"
        )


def _declared_instances(
    table_name: str, rows: pl.LazyFrame, where: str
) -> DeclaredInstances:
    """Every clock instance the rows state, each restored by the one restoration a
    consumer uses. A Scene's source binding asserts its scope and its manifest
    declares each clock once, so two instances of one (Scene, clock) leave a
    timestamp with no single context."""
    clock, origin, scope = _CLOCK_COLUMNS[table_name]
    distinct = (
        rows.select(*_SCENE_KEY, clock, origin, scope)
        .unique(maintain_order=True)
        .collect()
    )
    instances: DeclaredInstances = {}
    for row in distinct.iter_rows(named=True):
        key = (
            row["dataset_id"],
            row["dataset_version"],
            row["scene_id"],
            row[clock],
        )
        try:
            instance = restore_clock_instance(row[clock], row[origin], row[scope])
        except ClockDomainError as exc:
            raise SceneAnalyticsIntegrityError(
                f"{where}: {table_name} row of Scene {row['scene_id']!r} states no "
                f"valid clock instance: {exc}"
            ) from exc
        _merge_instance(instances, key, instance, where)
    return instances


def _check_rows(table_name: str, rows: pl.LazyFrame, where: str) -> DeclaredInstances:
    _check_required_values(table_name, rows, where)
    if table_name == "scenes":
        _check_scene_rows(rows, where)
    return _declared_instances(table_name, rows, where)


def _check_one_instance_per_clock(
    declared: Iterable[DeclaredInstances], where: str
) -> None:
    merged: DeclaredInstances = {}
    for instances in declared:
        for key, instance in instances.items():
            _merge_instance(merged, key, instance, where)


def _check_frame(table_name: str, df: pl.DataFrame) -> DeclaredInstances:
    expected = _schema_of(table_name)
    if list(df.schema.items()) != list(expected.items()):
        raise ValueError(
            f"{table_name}: frame columns {dict(df.schema)!r} are not the "
            f"{SCENE_ANALYTICS_SCHEMA_VERSION!r} contract {expected!r}"
        )
    return _check_rows(table_name, df.lazy(), "frame")


def check_scene_table_frame(table_name: str, df: pl.DataFrame) -> None:
    """A writer stamps only what the stamp declares: exactly this version's columns
    in order, every required value, every clock instance valid."""
    _check_frame(table_name, df)


def check_scene_table_frames(frames: Mapping[str, pl.DataFrame]) -> None:
    """``check_scene_table_frame`` of each table of one snapshot, and that the
    tables agree with each other on every clock a Scene declares."""
    _check_one_instance_per_clock(
        [_check_frame(table_name, df) for table_name, df in frames.items()], "frames"
    )


def check_scene_table_footer(
    footer: pq.FileMetaData, table_name: str, *, uri: str
) -> None:
    """Refuse a Parquet file unless its footer states this table of the supported
    schema version and its columns are that version's columns."""
    stated = stated_table_schema(footer)
    expected = scene_table_schema_stamp(table_name)
    if stated != expected.encode():
        raise UnsupportedSceneAnalyticsSchemaError(
            f"{uri}: footer states {TABLE_SCHEMA_METADATA_KEY}={stated!r}, this "
            f"reader requires {expected!r} (ADR-020)"
        )
    columns = arrow_columns(footer.schema.to_arrow_schema())
    if columns != scene_table_columns(table_name):
        raise UnsupportedSceneAnalyticsSchemaError(
            f"{uri}: states {expected!r} but its columns are {columns!r}, not "
            f"{scene_table_columns(table_name)!r} (ADR-020)"
        )


def read_scene_table_bytes(data: bytes, table_name: str, *, uri: str) -> pl.DataFrame:
    """A whole Scene analytics table, once its footer and every row are accepted.
    Nothing is returned before both are."""
    try:
        footer = pq.read_metadata(io.BytesIO(data))
    except pa.ArrowInvalid as exc:
        raise UnsupportedSceneAnalyticsSchemaError(
            f"{uri}: not a readable Parquet file: {exc}"
        ) from exc
    check_scene_table_footer(footer, table_name, uri=uri)
    df = pl.read_parquet(io.BytesIO(data))
    _check_rows(table_name, df.lazy(), uri)
    return df


def check_scene_table_files(tables: Mapping[str, str]) -> None:
    """Prove the local files of ``{table name: path}`` as one snapshot: footers
    first, then their rows, then that they agree on every clock a Scene declares.
    Reads the needed columns of every row of every file."""
    declared = []
    for table_name, path in tables.items():
        try:
            footer = pq.read_metadata(path)
        except pa.ArrowInvalid as exc:
            raise UnsupportedSceneAnalyticsSchemaError(
                f"{path}: not a readable Parquet file: {exc}"
            ) from exc
        check_scene_table_footer(footer, table_name, uri=path)
        declared.append(_check_rows(table_name, pl.scan_parquet(path), path))
    _check_one_instance_per_clock(declared, "snapshot")


def is_scene_table_file(footer: pq.FileMetaData) -> bool:
    """Whether a file is a Scene analytics table of any version, supported or not:
    it says so in its footer, or it is unstamped with exactly the columns of one."""
    stated = stated_table_schema(footer)
    if stated is not None:
        return stated.startswith(f"{SCENE_TABLE_SCHEMA_FAMILY}/".encode())
    names = tuple(footer.schema.to_arrow_schema().names)
    return names in _V1_SCENE_TABLE_COLUMNS.values() or names in {
        tuple(schema) for schema in SCENE_TABLE_SCHEMAS.values()
    }


# ── Robot analytics tables ──

ROBOT_TELEMETRY_SCHEMA: dict[str, pl.PolarsDataType] = {
    "robot_id": pl.Utf8,
    "robot_run_id": pl.Utf8,
    "mission_id": pl.Utf8,
    "scene_id": pl.Utf8,
    "timestamp_us": pl.Int64,
    "position": pl.List(pl.Float64),
    "orientation": pl.List(pl.Float64),
    "velocity": pl.List(pl.Float64),
    "acceleration": pl.List(pl.Float64),
    "steering": pl.Float64,
    "throttle": pl.Float64,
    "brake": pl.Float64,
    "battery": pl.Float64,
    "operation_state": pl.Utf8,
}

MISSIONS_SCHEMA: dict[str, pl.PolarsDataType] = {
    "mission_id": pl.Utf8,
    "robot_id": pl.Utf8,
    "robot_run_id": pl.Utf8,
    "status": pl.Utf8,
    "started_at": pl.Datetime("us", "UTC"),
    "ended_at": pl.Datetime("us", "UTC"),
}


def build_robot_telemetry_table(states: list[RobotStateRecord]) -> pl.DataFrame:
    rows = [
        {
            "robot_id": s.robot_id,
            "robot_run_id": s.robot_run_id,
            "mission_id": s.mission_id,
            "scene_id": s.scene_id,
            "timestamp_us": s.timestamp_us,
            "position": s.position,
            "orientation": s.orientation,
            "velocity": s.velocity,
            "acceleration": s.acceleration,
            "steering": s.steering,
            "throttle": s.throttle,
            "brake": s.brake,
            "battery": s.battery,
            "operation_state": str(s.operation_state) if s.operation_state else None,
        }
        for s in states
    ]
    return pl.DataFrame(rows, schema=ROBOT_TELEMETRY_SCHEMA)


def build_missions_table(missions: list[MissionRecord]) -> pl.DataFrame:
    rows = [
        {
            "mission_id": m.mission_id,
            "robot_id": m.robot_id,
            "robot_run_id": m.robot_run_id,
            "status": str(m.status),
            "started_at": m.started_at,
            "ended_at": m.ended_at,
        }
        for m in missions
    ]
    return pl.DataFrame(rows, schema=MISSIONS_SCHEMA)


ROBOT_TABLE_BUILDERS = ("robot_telemetry", "missions")
