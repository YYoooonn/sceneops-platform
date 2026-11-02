"""Scene analytics schema v2: the persisted contract and what it preserves.

Every comparability statement here goes through the real Parquet round trip and
the platform's own ``restore_clock_instance`` / ``require_comparable``.
"""

from __future__ import annotations

import io
import json

import polars as pl
import pyarrow.parquet as pq
import pytest

from sceneops_analytics import (
    SCENE_TABLE_SCHEMAS,
    AnalyticsTableWriter,
    SceneAnalyticsIntegrityError,
    SceneRevisionContent,
    SceneRevisionPairingError,
    UnsupportedSceneAnalyticsSchemaError,
    build_keyframes_table,
    build_observations_table,
    build_robot_telemetry_table,
    build_scenes_table,
    check_scene_table_files,
    check_scene_table_frames,
    query_parquet,
    query_scene_analytics,
    read_scene_table_bytes,
    scene_table_columns,
    scene_table_schema_stamp,
)
from sceneops_analytics.testing.scene_analytics_migration import (
    v1_scene_table,
    v1_scene_table_bytes,
    write_v1_scene_tables,
)
from sceneops_core.episodes.testing import episode_manifest, observation
from sceneops_core.provenance import ContentUnitSource, manifest_artifact_id_for
from sceneops_core.scenes.analytics import SCENE_ANALYTICS_SCHEMA_VERSION
from sceneops_core.scenes.schemas import (
    SceneManifest,
    project_scene_revision,
    scene_id_for,
)
from sceneops_core.scenes.testing import build_scene_manifest, recording_source
from sceneops_core.sources import content_id_for
from sceneops_core.temporal import (
    ClockInstance,
    ClockOrigin,
    IncomparableClocksError,
    require_comparable,
    restore_clock_instance,
)
from sceneops_storage import LocalArtifactStore

CLOCK = "mcap_log_time"
DATASET = dict(dataset_id="ds", dataset_version="v")
BUILDERS = {
    "scenes": build_scenes_table,
    "observations": build_observations_table,
    "keyframes": build_keyframes_table,
}
CLOCK_COLUMNS = {
    "scenes": ("window_clock", "window_clock_origin", "window_clock_scope"),
    "observations": ("source_clock", "clock_origin", "clock_scope"),
    "keyframes": ("source_clock", "clock_origin", "clock_scope"),
}


def content_of(manifest: SceneManifest) -> SceneRevisionContent:
    checksum = manifest.checksum()
    record = project_scene_revision(
        manifest=manifest,
        manifest_artifact_id=manifest_artifact_id_for(
            unit_id=scene_id_for(manifest.lineage.source), manifest_checksum=checksum
        ),
        manifest_checksum=checksum,
    )
    return SceneRevisionContent(record=record, manifest=manifest)


def recording_scene(run: str = "run-a", key: str = "seg-0", **kwargs) -> SceneManifest:
    return build_scene_manifest(
        source=recording_source(robot_run_id=run, unit_key=key), **kwargs
    )


def content_source(tag: str = "c", key: str = "0", clock: str = "x.clock"):
    checksum = "sha256:" + tag * 64
    return ContentUnitSource(
        content_id=content_id_for(checksum),
        content_checksum=checksum,
        source_clock=clock,
        start_timestamp_ns=0,
        end_timestamp_ns=10_000_000_000,
        unit_key=key,
    )


def with_origins(manifest: SceneManifest, **origins: str) -> SceneManifest:
    """The same Scene with clocks of other origins: ``clock.id=origin`` spelled
    ``clock__id=origin``. An origin is the builder's decision, so it enters neither
    the unit id nor the producer fingerprint."""
    payload = json.loads(manifest.to_canonical_bytes())
    wanted = {clock.replace("__", "."): o for clock, o in origins.items()}
    for domain in payload["clock_domains"]:
        domain["origin"] = wanted.get(domain["clock"], domain["origin"])
    return SceneManifest.model_validate(payload)


def native_scene(tag: str = "c", key: str = "0", *, origin: str) -> SceneManifest:
    return with_origins(
        build_scene_manifest(source=content_source(tag, key)), x__clock=origin
    )


@pytest.fixture()
def store(tmp_path):
    return LocalArtifactStore(root_uri=str(tmp_path))


@pytest.fixture()
def writer(store, tmp_path):
    return AnalyticsTableWriter(artifact_store=store, root_uri=str(tmp_path / "a"))


async def round_trip(writer, table_name: str, scenes) -> pl.DataFrame:
    df = BUILDERS[table_name](**DATASET, scenes=scenes)
    written = await writer.write_table(table_name, df, **DATASET)
    return await writer.read_table(table_name, written.uri)


def instance_of(table_name: str, row: dict) -> ClockInstance:
    clock, origin, scope = CLOCK_COLUMNS[table_name]
    return restore_clock_instance(row[clock], row[origin], row[scope])


def stamped_bytes(df: pl.DataFrame, table_name: str) -> bytes:
    buffer = io.BytesIO()
    df.write_parquet(
        buffer, metadata={"sceneops.table_schema": scene_table_schema_stamp(table_name)}
    )
    return buffer.getvalue()


def write_local(tmp_path, name: str, data: bytes) -> str:
    path = tmp_path / name
    path.write_bytes(data)
    return str(path)


# --- the frozen contract ----------------------------------------------------------


FROZEN_COLUMNS = {
    "scenes": [
        ("dataset_id", "string"),
        ("dataset_version", "string"),
        ("scene_id", "string"),
        ("source_kind", "string"),
        ("robot_run_id", "string"),
        ("content_id", "string"),
        ("unit_key", "string"),
        ("producer_fingerprint", "string"),
        ("manifest_artifact_id", "string"),
        ("manifest_checksum", "string"),
        ("window_clock", "string"),
        ("window_clock_origin", "string"),
        ("window_clock_scope", "list<string>"),
        ("window_start_timestamp_ns", "int64"),
        ("window_end_timestamp_ns", "int64"),
        ("observation_count", "int64"),
        ("keyframe_count", "int64"),
        ("observed_channels", "list<string>"),
    ],
    "observations": [
        ("dataset_id", "string"),
        ("dataset_version", "string"),
        ("scene_id", "string"),
        ("observation_id", "string"),
        ("channel", "string"),
        ("modality", "string"),
        ("timestamp_ns", "int64"),
        ("source_clock", "string"),
        ("clock_origin", "string"),
        ("clock_scope", "list<string>"),
        ("payload_uri", "string"),
        ("payload_checksum", "string"),
        ("payload_media_type", "string"),
        ("payload_size_bytes", "int64"),
        ("payload_selector", "string"),
        ("calibration_id", "string"),
        ("ego_pose_id", "string"),
    ],
    "keyframes": [
        ("dataset_id", "string"),
        ("dataset_version", "string"),
        ("scene_id", "string"),
        ("group_id", "string"),
        ("timestamp_ns", "int64"),
        ("source_clock", "string"),
        ("clock_origin", "string"),
        ("clock_scope", "list<string>"),
        ("observation_ids", "list<string>"),
    ],
}


@pytest.mark.parametrize("table_name", FROZEN_COLUMNS)
def test_a_contract_change_has_to_move_the_version_or_this_test(table_name):
    assert SCENE_ANALYTICS_SCHEMA_VERSION == "v2"
    assert scene_table_columns(table_name) == FROZEN_COLUMNS[table_name]
    assert scene_table_schema_stamp(table_name) == f"scene_analytics/v2/{table_name}"


def test_the_contract_covers_exactly_the_three_scene_tables():
    assert set(SCENE_TABLE_SCHEMAS) == {"scenes", "observations", "keyframes"}
    with pytest.raises(ValueError, match="not a Scene analytics table"):
        scene_table_schema_stamp("robot_telemetry")


# --- clock comparability ----------------------------------------------------------


async def test_independent_runs_with_equal_clocks_and_times_stay_distinct(writer):
    scenes = [
        content_of(recording_scene("run-a")),
        content_of(recording_scene("run-b")),
    ]
    observations = await round_trip(writer, "observations", scenes)
    (a, b) = [
        observations.filter(pl.col("scene_id") == s.record.scene_id).to_dicts()
        for s in scenes
    ]
    assert [r["timestamp_ns"] for r in a] == [r["timestamp_ns"] for r in b]
    assert {r["source_clock"] for r in a} == {r["source_clock"] for r in b} == {CLOCK}

    first, second = instance_of("observations", a[0]), instance_of("observations", b[0])
    assert first.scope == ("recording", "run-a")
    assert second.scope == ("recording", "run-b")
    with pytest.raises(IncomparableClocksError):
        require_comparable(first, second)


async def test_two_segments_of_one_run_stay_comparable(writer):
    scenes = [
        content_of(recording_scene("run-a", "seg-0")),
        content_of(recording_scene("run-a", "seg-1")),
    ]
    rows = (await round_trip(writer, "scenes", scenes)).to_dicts()
    assert rows[0]["unit_key"] != rows[1]["unit_key"]
    require_comparable(instance_of("scenes", rows[0]), instance_of("scenes", rows[1]))


async def test_a_scene_and_an_episode_of_one_run_share_a_declared_clock(writer):
    episode = episode_manifest(
        [observation("/cam", 0)], robot_run_id="run-a", clock=CLOCK, window=(0, 10)
    )
    scene = content_of(recording_scene("run-a"))
    row = (await round_trip(writer, "observations", [scene])).row(0, named=True)
    require_comparable(instance_of("observations", row), episode.clock_instance(CLOCK))

    other_run = episode_manifest(
        [observation("/cam", 0)], robot_run_id="run-b", clock=CLOCK, window=(0, 10)
    )
    with pytest.raises(IncomparableClocksError):
        require_comparable(
            instance_of("observations", row), other_run.clock_instance(CLOCK)
        )


async def test_unrelated_unit_origin_clocks_stay_incomparable(writer):
    scenes = [
        content_of(native_scene(key="0", origin="unit")),
        content_of(native_scene(key="1", origin="unit")),
    ]
    rows = (await round_trip(writer, "keyframes", scenes)).to_dicts()
    instances = {tuple(instance_of("keyframes", r).scope) for r in rows}
    assert len(instances) == 2
    same_time = [r for r in rows if r["group_id"] == "keyframe-0000"]
    assert same_time[0]["timestamp_ns"] == same_time[1]["timestamp_ns"]
    with pytest.raises(IncomparableClocksError):
        require_comparable(
            instance_of("keyframes", same_time[0]),
            instance_of("keyframes", same_time[1]),
        )


async def test_unix_epoch_clocks_are_comparable_with_empty_scopes(writer):
    scenes = [
        content_of(native_scene("c", origin="unix_epoch")),
        content_of(native_scene("d", origin="unix_epoch")),
    ]
    rows = (await round_trip(writer, "scenes", scenes)).to_dicts()
    assert [r["window_clock_scope"] for r in rows] == [[], []]
    assert rows[0]["content_id"] != rows[1]["content_id"]
    require_comparable(instance_of("scenes", rows[0]), instance_of("scenes", rows[1]))


async def test_a_sql_join_on_the_whole_instance_drops_the_false_matches(
    tmp_path, writer
):
    """Equal values and clock ids: what a join on them alone would call simultaneous."""
    scenes = [
        content_of(recording_scene("run-a")),
        content_of(recording_scene("run-b")),
    ]
    written = await writer.write_table(
        "observations", build_observations_table(**DATASET, scenes=scenes), **DATASET
    )
    data = await writer.artifact_store.read_bytes(written.uri)
    path = write_local(tmp_path, "observations.parquet", data)
    join = """SELECT count(*) AS n FROM observations a JOIN observations b
              ON a.scene_id < b.scene_id AND a.channel = b.channel
             AND a.timestamp_ns = b.timestamp_ns AND a.source_clock = b.source_clock {}"""
    tables = {"observations": path}
    assert query_scene_analytics(tables, join.format(""))["n"].item() == 5
    whole = "AND a.clock_origin = b.clock_origin AND a.clock_scope = b.clock_scope"
    assert query_scene_analytics(tables, join.format(whole))["n"].item() == 0


# --- several clocks in one Scene -------------------------------------------------


def _multi_clock_scene(tag: str = "c", key: str = "0") -> SceneManifest:
    """A window on a source-origin clock no observation uses, a camera on a
    unit-origin clock and a lidar and its keyframes on an absolute one."""
    return with_origins(
        build_scene_manifest(
            source=content_source(tag, key, clock="x.window"),
            source_clock="x.lidar",
            camera_clock="x.camera",
        ),
        x__window="source",
        x__camera="unit",
        x__lidar="unix_epoch",
    )


async def test_every_row_states_the_clock_its_own_structure_declares(writer):
    manifest = _multi_clock_scene()
    scene = content_of(manifest)
    (window,) = (await round_trip(writer, "scenes", [scene])).to_dicts()
    observations = (await round_trip(writer, "observations", [scene])).to_dicts()
    keyframes = (await round_trip(writer, "keyframes", [scene])).to_dicts()

    assert instance_of("scenes", window) == manifest.clock_instance("x.window")
    assert window["window_clock_origin"] == "source"
    assert {r["source_clock"] for r in observations} == {"x.camera", "x.lidar"}
    assert "x.window" not in {r["source_clock"] for r in observations}
    channel_clock = {c.channel: c.source_clock for c in manifest.channels}
    for row in observations:
        assert row["source_clock"] == channel_clock[row["channel"]]
        assert instance_of("observations", row) == manifest.clock_instance(
            row["source_clock"]
        )
    assert {r["clock_origin"] for r in observations} == {"unit", "unix_epoch"}
    assert {r["source_clock"] for r in keyframes} == {"x.lidar"}
    assert {r["clock_origin"] for r in keyframes} == {"unix_epoch"}
    assert all(r["clock_scope"] == [] for r in keyframes)


async def test_no_row_borrows_another_scenes_scope(writer):
    manifests = [
        recording_scene("run-a"),
        recording_scene("run-b"),
        native_scene("c", "0", origin="unit"),
        native_scene("d", "1", origin="unit"),
    ]
    scenes = [content_of(m) for m in manifests]
    for table_name in BUILDERS:
        rows = (await round_trip(writer, table_name, scenes)).to_dicts()
        by_scene = {s.record.scene_id: s.manifest for s in scenes}
        assert {r["scene_id"] for r in rows} == set(by_scene)
        clock = CLOCK_COLUMNS[table_name][0]
        for row in rows:
            assert instance_of(table_name, row) == by_scene[
                row["scene_id"]
            ].clock_instance(row[clock])


# --- native provenance ------------------------------------------------------------


async def test_a_recording_scene_states_its_run_and_a_content_scene_its_content(writer):
    recording = content_of(recording_scene("run-a"))
    native = content_of(native_scene(origin="source"))
    rows = {
        r["scene_id"]: r
        for r in (await round_trip(writer, "scenes", [recording, native])).to_dicts()
    }
    assert rows[recording.record.scene_id]["source_kind"] == "recording"
    assert rows[recording.record.scene_id]["robot_run_id"] == "run-a"
    assert rows[recording.record.scene_id]["content_id"] is None
    assert rows[native.record.scene_id]["source_kind"] == "content"
    assert rows[native.record.scene_id]["content_id"] == native.record.content_id
    assert rows[native.record.scene_id]["robot_run_id"] is None


async def test_provenance_changes_neither_an_instance_nor_any_other_column(writer):
    """Two sources' tables have the same columns, and provenance is not an input of
    equality: the same instance stated under different provenance is one instance."""
    assert (
        await round_trip(writer, "scenes", [content_of(recording_scene())])
    ).schema == (
        await round_trip(writer, "scenes", [content_of(native_scene(origin="unit"))])
    ).schema
    row = {
        "window_clock": "x.clock",
        "window_clock_origin": "unix_epoch",
        "window_clock_scope": [],
    }
    assert instance_of("scenes", {**row, "source_kind": "recording"}) == instance_of(
        "scenes", {**row, "source_kind": "content"}
    )


# --- persistence ------------------------------------------------------------------


async def test_scope_element_order_survives_parquet(writer):
    manifest = native_scene(origin="unit")
    (row,) = (await round_trip(writer, "scenes", [content_of(manifest)])).to_dicts()
    assert row["window_clock_scope"] == ["content", content_source().content_id, "0"]

    reordered = restore_clock_instance(
        row["window_clock"],
        row["window_clock_origin"],
        list(reversed(row["window_clock_scope"])),
    )
    with pytest.raises(IncomparableClocksError):
        require_comparable(manifest.clock_instance("x.clock"), reordered)


@pytest.mark.parametrize("table_name", BUILDERS)
async def test_every_written_table_states_its_version_and_table(writer, table_name):
    df = BUILDERS[table_name](**DATASET, scenes=[content_of(recording_scene())])
    written = await writer.write_table(table_name, df, **DATASET)
    footer = pq.read_metadata(
        io.BytesIO(await writer.artifact_store.read_bytes(written.uri))
    )
    assert footer.metadata[b"sceneops.table_schema"] == (
        f"scene_analytics/v2/{table_name}".encode()
    )


@pytest.mark.parametrize("table_name", BUILDERS)
async def test_repeating_an_export_converges_on_identical_bytes(writer, table_name):
    scenes = [content_of(recording_scene()), content_of(native_scene(origin="unit"))]
    frames = [BUILDERS[table_name](**DATASET, scenes=scenes) for _ in range(2)]
    first = await writer.write_table(table_name, frames[0], **DATASET)
    data = await writer.artifact_store.read_bytes(first.uri)
    second = await writer.write_table(table_name, frames[1], **DATASET)
    assert (second.uri, second.checksum) == (first.uri, first.checksum)
    assert await writer.artifact_store.read_bytes(second.uri) == data


async def test_v1_and_v2_coexist_at_their_own_keys(writer):
    scenes = [content_of(recording_scene())]
    old = await write_v1_scene_tables(writer, **DATASET, scenes=scenes)
    before = {
        name: await writer.artifact_store.read_bytes(table.uri)
        for name, table in old.items()
    }
    for table_name, builder in BUILDERS.items():
        new = await writer.write_table(
            table_name, builder(**DATASET, scenes=scenes), **DATASET
        )
        assert new.uri != old[table_name].uri
        assert await writer.read_table(table_name, new.uri) is not None
        assert (
            await writer.artifact_store.read_bytes(old[table_name].uri)
            == (before[table_name])
        )
        with pytest.raises(UnsupportedSceneAnalyticsSchemaError, match="None"):
            await writer.read_table(table_name, old[table_name].uri)


@pytest.mark.parametrize("table_name", BUILDERS)
async def test_v2_values_are_v1_values_plus_the_new_columns(table_name):
    scenes = [content_of(recording_scene()), content_of(native_scene(origin="unit"))]
    v2 = BUILDERS[table_name](**DATASET, scenes=scenes)
    v1 = v1_scene_table(table_name, **DATASET, scenes=scenes)
    assert v2.select(v1.columns).equals(v1)


# --- reader refusals --------------------------------------------------------------


def _valid(table_name: str = "keyframes") -> pl.DataFrame:
    return BUILDERS[table_name](**DATASET, scenes=[content_of(recording_scene())])


@pytest.mark.parametrize(
    "stated",
    [
        None,
        "scene_analytics/v1/keyframes",
        "scene_analytics/v3/keyframes",
        "scene_analytics/v2/observations",
        "learning_data/v2/keyframes",
        "keyframes",
    ],
)
def test_a_footer_that_does_not_state_this_table_of_this_version_is_refused(stated):
    buffer = io.BytesIO()
    _valid().write_parquet(
        buffer, metadata={"sceneops.table_schema": stated} if stated else None
    )
    with pytest.raises(UnsupportedSceneAnalyticsSchemaError, match="requires"):
        read_scene_table_bytes(buffer.getvalue(), "keyframes", uri="u")


@pytest.mark.parametrize(
    "transform",
    [
        lambda df: df.drop("clock_scope"),
        lambda df: df.with_columns(extra=pl.lit(1)),
        lambda df: df.select(list(reversed(df.columns))),
        lambda df: df.with_columns(pl.col("timestamp_ns").cast(pl.Float64)),
        lambda df: df.with_columns(pl.col("clock_scope").list.join("/")),
    ],
)
def test_a_correct_stamp_on_other_columns_is_refused(transform):
    data = stamped_bytes(transform(_valid()), "keyframes")
    with pytest.raises(UnsupportedSceneAnalyticsSchemaError, match="columns"):
        read_scene_table_bytes(data, "keyframes", uri="u")


def test_a_real_v1_table_is_refused_though_every_column_it_has_is_right():
    v1 = v1_scene_table("keyframes", **DATASET, scenes=[content_of(recording_scene())])
    with pytest.raises(UnsupportedSceneAnalyticsSchemaError, match="None"):
        read_scene_table_bytes(v1_scene_table_bytes(v1), "keyframes", uri="u")


def test_bytes_that_are_not_parquet_are_refused():
    with pytest.raises(UnsupportedSceneAnalyticsSchemaError, match="not a readable"):
        read_scene_table_bytes(b"not parquet", "keyframes", uri="u")


def test_query_parquet_refuses_every_scene_table_and_nothing_else(tmp_path):
    scene = content_of(recording_scene())
    for table_name, builder in BUILDERS.items():
        current = write_local(
            tmp_path,
            f"{table_name}-v2.parquet",
            stamped_bytes(builder(**DATASET, scenes=[scene]), table_name),
        )
        old = write_local(
            tmp_path,
            f"{table_name}-v1.parquet",
            v1_scene_table_bytes(v1_scene_table(table_name, **DATASET, scenes=[scene])),
        )
        for path in (current, old):
            with pytest.raises(ValueError, match="query_scene_analytics"):
                query_parquet({"t": path}, "SELECT * FROM t")

    robot = tmp_path / "robot_telemetry.parquet"
    build_robot_telemetry_table([]).write_parquet(robot)
    assert (
        query_parquet({"t": str(robot)}, "SELECT count(*) AS n FROM t")["n"].item() == 0
    )


def test_query_scene_analytics_validates_before_returning_any_row(tmp_path):
    scene = content_of(recording_scene())
    tables = {
        name: write_local(
            tmp_path,
            f"{name}.parquet",
            stamped_bytes(builder(**DATASET, scenes=[scene]), name),
        )
        for name, builder in BUILDERS.items()
    }
    joined = query_scene_analytics(
        tables,
        """SELECT o.observation_id FROM observations o JOIN scenes s USING (scene_id)
           WHERE o.source_clock = s.window_clock
             AND o.clock_origin = s.window_clock_origin
             AND o.clock_scope = s.window_clock_scope""",
    )
    assert joined.height == 5

    stale = write_local(
        tmp_path,
        "stale.parquet",
        v1_scene_table_bytes(v1_scene_table("scenes", **DATASET, scenes=[scene])),
    )
    with pytest.raises(UnsupportedSceneAnalyticsSchemaError):
        query_scene_analytics({**tables, "scenes": stale}, "SELECT 1 AS n")
    with pytest.raises(ValueError, match="not a Scene analytics table"):
        query_scene_analytics({"telemetry": tables["scenes"]}, "SELECT 1 AS n")


# --- corruption -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("origin", "scope", "message"),
    [
        ("martian", ["recording", "run-a"], "martian"),
        ("source", [], "needs a scope"),
        ("unit", [], "needs a scope"),
        ("unix_epoch", ["recording", "run-a"], "has no scope"),
        ("source", ["recording", " run-a"], "whitespace"),
        ("source", ["recording", ""], "1-"),
    ],
)
def test_an_invalid_clock_instance_is_refused(origin, scope, message):
    df = _valid().with_columns(
        clock_origin=pl.lit(origin),
        clock_scope=pl.lit(scope, dtype=pl.List(pl.Utf8)),
    )
    data = stamped_bytes(df, "keyframes")
    with pytest.raises(SceneAnalyticsIntegrityError, match=message):
        read_scene_table_bytes(data, "keyframes", uri="u")


@pytest.mark.parametrize("column", ["clock_origin", "clock_scope", "source_clock"])
def test_a_missing_temporal_value_is_refused(column):
    df = _valid().with_columns(
        pl.lit(None, dtype=_valid().schema[column]).alias(column)
    )
    with pytest.raises(SceneAnalyticsIntegrityError, match=column):
        read_scene_table_bytes(stamped_bytes(df, "keyframes"), "keyframes", uri="u")


def test_a_scope_with_a_null_element_is_refused():
    df = _valid().with_columns(
        clock_scope=pl.lit(["recording", None], dtype=pl.List(pl.Utf8))
    )
    with pytest.raises(SceneAnalyticsIntegrityError, match="must be a string"):
        read_scene_table_bytes(stamped_bytes(df, "keyframes"), "keyframes", uri="u")


def test_two_instances_of_one_declared_clock_of_one_scene_are_refused():
    df = _valid()
    conflicting = pl.concat(
        [
            df.head(1),
            df.tail(1).with_columns(
                clock_scope=pl.lit(["recording", "run-b"], dtype=pl.List(pl.Utf8))
            ),
        ]
    )
    with pytest.raises(SceneAnalyticsIntegrityError, match="two instances"):
        read_scene_table_bytes(
            stamped_bytes(conflicting, "keyframes"), "keyframes", uri="u"
        )


def test_tables_of_one_snapshot_that_disagree_on_a_clock_are_refused(tmp_path):
    scene = content_of(recording_scene("run-a"))
    scenes = build_scenes_table(**DATASET, scenes=[scene])
    keyframes = build_keyframes_table(**DATASET, scenes=[scene]).with_columns(
        clock_scope=pl.lit(["recording", "run-b"], dtype=pl.List(pl.Utf8))
    )
    with pytest.raises(SceneAnalyticsIntegrityError, match="two instances"):
        check_scene_table_files(
            {
                "scenes": write_local(
                    tmp_path, "scenes.parquet", stamped_bytes(scenes, "scenes")
                ),
                "keyframes": write_local(
                    tmp_path, "keyframes.parquet", stamped_bytes(keyframes, "keyframes")
                ),
            }
        )
    with pytest.raises(SceneAnalyticsIntegrityError, match="two instances"):
        check_scene_table_frames({"scenes": scenes, "keyframes": keyframes})


def test_a_clock_of_one_scene_may_have_different_instances_in_two_versions(tmp_path):
    """One version pins one revision of a Scene, so the check is per version: a
    query over two versions is not a conflict by itself."""
    scene = content_of(recording_scene("run-a"))
    one = build_keyframes_table(dataset_id="ds", dataset_version="one", scenes=[scene])
    two = build_keyframes_table(
        dataset_id="ds", dataset_version="two", scenes=[scene]
    ).with_columns(clock_scope=pl.lit(["recording", "run-b"], dtype=pl.List(pl.Utf8)))
    read_scene_table_bytes(
        stamped_bytes(pl.concat([one, two]), "keyframes"), "keyframes", uri="u"
    )


@pytest.mark.parametrize(
    "update",
    [
        {"source_kind": "martian"},
        {"robot_run_id": None},
        {"content_id": "content-" + "c" * 64},
        {"source_kind": "content"},
        {"unit_key": "another-segment"},
    ],
)
def test_a_scene_row_with_no_valid_source_is_refused(update):
    df = _valid("scenes").with_columns(
        pl.lit(value, dtype=pl.Utf8).alias(column) for column, value in update.items()
    )
    with pytest.raises(SceneAnalyticsIntegrityError, match="no valid source"):
        read_scene_table_bytes(stamped_bytes(df, "scenes"), "scenes", uri="u")


def test_two_rows_of_one_scene_are_refused():
    df = _valid("scenes")
    with pytest.raises(SceneAnalyticsIntegrityError, match="more than one"):
        read_scene_table_bytes(
            stamped_bytes(pl.concat([df, df]), "scenes"), "scenes", uri="u"
        )


# --- builder inputs ---------------------------------------------------------------


def test_a_record_cannot_be_paired_with_another_scenes_manifest():
    a, b = content_of(recording_scene("run-a")), content_of(recording_scene("run-b"))
    with pytest.raises(SceneRevisionPairingError, match="pins revision"):
        SceneRevisionContent(record=a.record, manifest=b.manifest)


def test_a_record_cannot_be_paired_with_another_revision_of_its_scene():
    one = content_of(recording_scene())
    other_revision = recording_scene(keyframe_timestamps_ns=(1_000_000_000,))
    assert one.manifest.checksum() != other_revision.checksum()
    with pytest.raises(SceneRevisionPairingError, match="pins revision"):
        SceneRevisionContent(record=one.record, manifest=other_revision)


def test_a_manifest_of_another_unit_at_the_records_pin_is_refused():
    """The checksum matches, the pin's unit does not: a hand-built record."""
    a, b = content_of(recording_scene("run-a")), content_of(recording_scene("run-b"))
    forged = b.record.model_copy(
        update={
            "manifest_checksum": a.manifest.checksum(),
            "manifest_artifact_id": manifest_artifact_id_for(
                unit_id=b.record.scene_id, manifest_checksum=a.manifest.checksum()
            ),
        }
    )
    with pytest.raises(SceneRevisionPairingError, match="not a revision of Scene"):
        SceneRevisionContent(record=forged, manifest=a.manifest)


def test_a_record_the_manifest_does_not_project_is_refused():
    one = content_of(recording_scene())
    wrong = one.record.model_copy(update={"window_clock": "another.clock"})
    with pytest.raises(SceneRevisionPairingError, match="projection"):
        SceneRevisionContent(record=wrong, manifest=one.manifest)


@pytest.mark.parametrize("table_name", BUILDERS)
def test_a_builder_emits_scenes_in_the_given_order(table_name):
    scenes = [
        content_of(recording_scene("run-b")),
        content_of(recording_scene("run-a")),
    ]
    rows = BUILDERS[table_name](**DATASET, scenes=scenes)["scene_id"].to_list()
    assert list(dict.fromkeys(rows)) == [s.record.scene_id for s in scenes]


# --- writer refusals --------------------------------------------------------------


async def test_a_writer_stamps_only_what_the_stamp_declares(writer):
    with pytest.raises(ValueError, match="contract"):
        await writer.write_table("keyframes", _valid().drop("clock_scope"), **DATASET)
    with pytest.raises(ValueError, match="contract"):
        await writer.write_table("keyframes", _valid("scenes"), **DATASET)
    with pytest.raises(ValueError, match="not a Scene analytics table"):
        await writer.write_table("robot_telemetry", _valid(), **DATASET)


async def test_a_writer_refuses_a_row_with_no_valid_instance(writer, store, tmp_path):
    invalid = _valid().with_columns(clock_origin=pl.lit("martian"))
    with pytest.raises(SceneAnalyticsIntegrityError, match="martian"):
        await writer.write_table("keyframes", invalid, **DATASET)
    assert await store.list_objects(str(tmp_path / "a")) == []


async def test_a_table_with_no_rows_is_a_valid_table(writer):
    empty = _valid().clear()
    written = await writer.write_table("keyframes", empty, **DATASET)
    assert (await writer.read_table("keyframes", written.uri)).height == 0


def test_clock_origin_values_are_the_enum_values():
    df = _valid()
    assert df["clock_origin"].unique().to_list() == [ClockOrigin.SOURCE.value]
