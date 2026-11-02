"""Learning Data Schema v2: the column contract, its footer stamp through both
production writers, and the Episode clock instance through Parquet and back,
through SceneOpsDataset.

Nothing here is evidence that a scope belongs to the Episode stating it: the
checks are structural. That takes the Episode's canonical manifest.
"""

from __future__ import annotations

import hashlib
import io
import json

import polars as pl
import pyarrow.parquet as pq
import pytest

from sceneops_analytics import (
    LEARNING_TABLE_SCHEMA_METADATA_KEY,
    AnalyticsTableWriter,
    LearningDataIntegrityError,
    SceneOpsDataset,
    ShardIndexMismatchError,
    build_learning_episodes_table,
    build_learning_signals_table,
    build_learning_steps_table,
    check_learning_export_tables,
    learning_table_columns,
    learning_table_schema_stamp,
    write_sharded_learning_tables,
)
from sceneops_analytics.learning_dataset import DISABLED_CACHE_POLICY
from sceneops_analytics.testing.learning_migration import (
    NATIVE_CLOCK,
    temporal_entries,
    write_v1_export,
)
from sceneops_analytics.testing.scale_fixture import ScaleSpec, build_scaled_entries
from sceneops_core.episodes.learning import EpisodeRef, FeatureProjection
from sceneops_core.episodes.learning_export import (
    LEARNING_DATA_LAYOUT_VERSION_SHARDED,
    LEARNING_DATA_SCHEMA_VERSION,
    AlignedArtifactRevision,
    LearningDataExportConfig,
    LearningDataExportManifest,
    UnsupportedLearningDataSchemaError,
    default_shard_policy,
    learning_data_export_id,
)
from sceneops_core.temporal import (
    ClockInstance,
    ClockOrigin,
    IncomparableClocksError,
    require_comparable,
)
from sceneops_storage import LocalArtifactStore

DS = dict(dataset_id="ds", dataset_version="v")

# Bumping the version without adding its columns here, or changing a column without
# bumping the version, fails below. An old version's entry stays as the record of
# what that version was.
COLUMNS_BY_VERSION = {
    "v2": {
        "learning_episodes": [
            ("dataset_id", "string"),
            ("dataset_version", "string"),
            ("export_id", "string"),
            ("episode_id", "string"),
            ("aligned_artifact_checksum", "string"),
            ("source_start_timestamp_us", "int64"),
            ("source_end_timestamp_us", "int64"),
            ("source_clock", "string"),
            ("clock_origin", "string"),
            ("clock_scope", "list<string>"),
            ("alignment_semantics_version", "string"),
            ("alignment_config_hash", "string"),
            ("target_frequency_hz", "double"),
            ("achieved_frequency_hz", "double"),
            ("dt_us", "int64"),
            ("step_count", "int64"),
            ("duplicate_discarded_count", "int64"),
            ("task", "string"),
            ("outcome", "string"),
        ],
        "learning_steps": [
            ("dataset_id", "string"),
            ("dataset_version", "string"),
            ("export_id", "string"),
            ("episode_id", "string"),
            ("aligned_artifact_checksum", "string"),
            ("step_index", "int64"),
            ("timestamp_us", "int64"),
        ],
        "learning_signals": [
            ("dataset_id", "string"),
            ("dataset_version", "string"),
            ("export_id", "string"),
            ("episode_id", "string"),
            ("aligned_artifact_checksum", "string"),
            ("step_index", "int64"),
            ("timestamp_us", "int64"),
            ("namespace", "string"),
            ("channel", "string"),
            ("policy", "string"),
            ("status", "string"),
            ("value_kind", "string"),
            ("value_scalar", "double"),
            ("value_vector", "list<double>"),
            ("reference_modality", "string"),
            ("reference_uri", "string"),
            ("reference_selector", "string"),
            ("source_timestamp_us", "int64"),
            ("time_delta_us", "int64"),
            ("source_before_timestamp_us", "int64"),
            ("source_after_timestamp_us", "int64"),
            ("interpolation_ratio", "double"),
        ],
    }
}

# Computed by the code before v2 from the same aligned artifacts, over every column
# except the ones v2 adds and export_id, which is derived from the schema version.
SEMANTIC_DIGESTS_BEFORE_V2 = {
    (
        "temporal",
        "episodes",
    ): "bc138c57756a99ec4c525d3cabcb0ad3c24920db9ec65523b54a95c787ee0db4",
    (
        "temporal",
        "steps",
    ): "16c361b0530625dcfbc711000b670fe0c6245cb588bacf52f5ab5b6b9a30ef80",
    (
        "temporal",
        "signals",
    ): "58778d7cd815ffda343baf1e8f08e218c4437050c9c104e68d0859b4ac1818b3",
    (
        "scale",
        "episodes",
    ): "fa14a20d8edddeb29ac440be939dd04b385524b24c7701ce519b9ee9087ea35f",
    (
        "scale",
        "steps",
    ): "23820e3ac1084cd66d8075c428986d01a03fbd588ce74884eafd42665af8117b",
    (
        "scale",
        "signals",
    ): "4a1adad0dc34d96ee435a83269c35ed000848822ab2dc731e40059177678346d",
}


@pytest.fixture
def store(tmp_path) -> LocalArtifactStore:
    return LocalArtifactStore(root_uri=str(tmp_path))


@pytest.fixture
def writer(store, tmp_path) -> AnalyticsTableWriter:
    return AnalyticsTableWriter(artifact_store=store, root_uri=str(tmp_path))


async def _export(writer, entries) -> tuple[LearningDataExportManifest, str]:
    """What EXPORT_LEARNING_DATA writes, through the same writers."""
    config = LearningDataExportConfig()
    export_id = learning_data_export_id(
        aligned_checksums=[checksum for checksum, _ in entries], export_config=config
    )
    episodes = build_learning_episodes_table(**DS, export_id=export_id, entries=entries)
    table = await writer.write_learning_table(
        "learning_episodes", episodes, **DS, export_id=export_id
    )
    shard_index = await write_sharded_learning_tables(
        writer,
        **DS,
        export_id=export_id,
        entries=entries,
        policy=default_shard_policy(),
    )
    manifest = LearningDataExportManifest(
        schema_version=LEARNING_DATA_SCHEMA_VERSION,
        export_id=export_id,
        **DS,
        inputs=[
            AlignedArtifactRevision(
                episode_id=artifact.aligned_episode.episode_id,
                aligned_artifact_id=f"art-{artifact.aligned_episode.episode_id}",
                aligned_artifact_checksum=checksum,
            )
            for checksum, artifact in entries
        ],
        export_config=config,
        table_uris={"learning_episodes": table.uri},
        table_checksums={"learning_episodes": table.checksum},
        row_counts={"learning_episodes": episodes.height},
        layout_version=LEARNING_DATA_LAYOUT_VERSION_SHARDED,
        shard_index=shard_index,
        episode_count=len(entries),
    )
    written = await writer.write_learning_export_manifest(
        manifest, **DS, export_id=export_id
    )
    return manifest, written.checksum


async def _open(store, manifest, checksum, **kwargs) -> SceneOpsDataset:
    return await SceneOpsDataset.open(
        learning_manifest=manifest,
        learning_manifest_checksum=checksum,
        artifact_store=store,
        **kwargs,
    )


async def _objects(writer) -> dict[str, bytes]:
    store = writer.artifact_store
    return {
        item.uri: await store.read_bytes(item.uri)
        for item in await store.list_objects(writer.root_uri)
    }


def _ref(entry) -> EpisodeRef:
    checksum, artifact = entry
    return EpisodeRef(
        episode_id=artifact.aligned_episode.episode_id,
        aligned_artifact_checksum=checksum,
    )


def _footer(data: bytes) -> pq.FileMetaData:
    return pq.read_metadata(io.BytesIO(data))


def _stamp_of(data: bytes) -> bytes | None:
    return (_footer(data).metadata or {}).get(
        LEARNING_TABLE_SCHEMA_METADATA_KEY.encode()
    )


# ── the contract ────────────────────────────────────────────────────────


def test_the_declared_version_has_exactly_these_columns() -> None:
    expected = COLUMNS_BY_VERSION[LEARNING_DATA_SCHEMA_VERSION]
    for table_name, columns in expected.items():
        assert learning_table_columns(table_name) == columns


def test_steps_and_signals_state_no_temporal_context() -> None:
    for table_name in ("learning_steps", "learning_signals"):
        names = {name for name, _ in learning_table_columns(table_name)}
        assert names.isdisjoint({"source_clock", "clock_origin", "clock_scope"})
        assert {"episode_id", "aligned_artifact_checksum"} <= names


async def test_both_production_writers_stamp_every_table(writer, store) -> None:
    entries = list(temporal_entries().values())
    manifest, _ = await _export(writer, entries)
    episodes = await store.read_bytes(manifest.table_uris["learning_episodes"])
    # Polars path
    assert "Polars" in (_footer(episodes).created_by or "")
    assert (
        _stamp_of(episodes) == learning_table_schema_stamp("learning_episodes").encode()
    )
    # PyArrow path
    for table_name in ("learning_steps", "learning_signals"):
        for shard in getattr(manifest.shard_index, table_name):
            data = await store.read_bytes(shard.uri)
            assert "parquet-cpp-arrow" in (_footer(data).created_by or "")
            assert _stamp_of(data) == learning_table_schema_stamp(table_name).encode()
            assert _footer(data).num_row_groups == len(shard.episodes)


async def test_the_single_file_writer_stamps_steps_and_signals_too(
    writer, store
) -> None:
    entries = list(temporal_entries().values())
    for table_name, builder in (
        ("learning_steps", build_learning_steps_table),
        ("learning_signals", build_learning_signals_table),
    ):
        written = await writer.write_learning_table(
            table_name,
            builder(**DS, export_id="e" * 64, entries=entries),
            **DS,
            export_id="e" * 64,
        )
        assert _stamp_of(await store.read_bytes(written.uri)) == (
            learning_table_schema_stamp(table_name).encode()
        )


def test_the_stamp_names_family_version_and_table() -> None:
    assert learning_table_schema_stamp("learning_steps") == (
        f"learning_data/{LEARNING_DATA_SCHEMA_VERSION}/learning_steps"
    )
    assert LEARNING_TABLE_SCHEMA_METADATA_KEY == "sceneops.table_schema"


async def test_the_stamp_survives_polars_and_pyarrow_reads(writer, store) -> None:
    manifest, _ = await _export(writer, list(temporal_entries().values()))
    for uri, table_name in (
        (manifest.table_uris["learning_episodes"], "learning_episodes"),
        (manifest.shard_index.learning_steps[0].uri, "learning_steps"),
    ):
        data = await store.read_bytes(uri)
        expected = learning_table_schema_stamp(table_name)
        assert (
            pl.read_parquet_metadata(io.BytesIO(data))[
                LEARNING_TABLE_SCHEMA_METADATA_KEY
            ]
            == expected
        )
        assert (
            pq.ParquetFile(io.BytesIO(data)).metadata.metadata[
                LEARNING_TABLE_SCHEMA_METADATA_KEY.encode()
            ]
            == expected.encode()
        )
        assert pq.ParquetFile(io.BytesIO(data)).read_row_group(0).num_rows > 0


async def test_a_writer_refuses_a_frame_that_is_not_what_the_stamp_declares(
    writer,
) -> None:
    entries = list(temporal_entries().values())
    wrong = build_learning_episodes_table(**DS, export_id="e", entries=entries).drop(
        "clock_scope"
    )
    with pytest.raises(ValueError, match="learning_episodes"):
        await writer.write_learning_table(
            "learning_episodes", wrong, **DS, export_id="e" * 64
        )
    steps = build_learning_steps_table(**DS, export_id="e", entries=entries)
    with pytest.raises(ValueError, match="learning_signals"):
        await writer.write_learning_table_shard(
            "learning_signals",
            steps,
            [steps.height],
            **DS,
            export_id="e" * 64,
            shard_index=0,
        )


# ── temporal context of Recording-derived Episodes ──────────────────────


async def test_two_runs_on_one_clock_and_timestamp_stay_incomparable(
    writer, store
) -> None:
    all_entries = temporal_entries()
    entries = [all_entries["run-a-0"], all_entries["run-b-0"]]
    dataset = await _open(store, *(await _export(writer, entries)))
    one = dataset.get_episode(_ref(entries[0]))
    two = dataset.get_episode(_ref(entries[1]))

    assert one.source_clock == two.source_clock
    assert one.source_start_timestamp_us == two.source_start_timestamp_us
    assert one.clock_origin == two.clock_origin == ClockOrigin.SOURCE
    assert one.clock_instance != two.clock_instance
    with pytest.raises(IncomparableClocksError):
        require_comparable(one.clock_instance, two.clock_instance)


async def test_two_segments_of_one_run_stay_comparable(writer, store) -> None:
    all_entries = temporal_entries()
    entries = [all_entries["run-a-0"], all_entries["run-a-1"]]
    dataset = await _open(store, *(await _export(writer, entries)))
    one = dataset.get_episode(_ref(entries[0]))
    two = dataset.get_episode(_ref(entries[1]))

    assert one.clock_instance == two.clock_instance
    require_comparable(one.clock_instance, two.clock_instance)


async def test_every_restored_instance_equals_the_aligned_episodes(
    writer, store
) -> None:
    entries = list(temporal_entries().values())
    dataset = await _open(store, *(await _export(writer, entries)))
    for entry in entries:
        aligned = entry[1].aligned_episode
        metadata = dataset.get_episode(_ref(entry))
        assert metadata.source_clock == aligned.source_clock
        assert metadata.clock_origin == aligned.clock_origin
        assert metadata.clock_scope == aligned.clock_scope
        assert metadata.clock_instance == aligned.clock_instance
        assert isinstance(metadata.clock_instance, ClockInstance)


# ── temporal context of native LeRobot Episodes ─────────────────────────


async def test_two_native_episodes_with_identical_frame_times_keep_their_own_scopes(
    writer, store
) -> None:
    all_entries = temporal_entries()
    entries = [all_entries["native-0"], all_entries["native-1"]]
    dataset = await _open(store, *(await _export(writer, entries)))
    one = dataset.get_episode(_ref(entries[0]))
    two = dataset.get_episode(_ref(entries[1]))

    assert one.clock_origin == two.clock_origin == ClockOrigin.UNIT
    assert one.source_clock == two.source_clock == NATIVE_CLOCK
    assert one.clock_scope != two.clock_scope
    assert [s.timestamp_us for s in entries[0][1].aligned_episode.steps] == [
        s.timestamp_us for s in entries[1][1].aligned_episode.steps
    ]
    with pytest.raises(IncomparableClocksError):
        require_comparable(one.clock_instance, two.clock_instance)


async def test_a_native_episode_without_transitions_persists_its_instance(
    writer, store
) -> None:
    entry = temporal_entries()["native-0"]
    aligned = entry[1].aligned_episode
    assert not any(
        channel.startswith("next.")
        for step in aligned.steps
        for channel in (*step.observations, *step.actions)
    )
    dataset = await _open(store, *(await _export(writer, [entry])))
    assert dataset.get_episode(_ref(entry)).clock_instance == aligned.clock_instance


async def test_frame_references_survive_unchanged_and_are_never_opened(
    writer, store
) -> None:
    """The dataset reads the persisted reference, never the video behind it: its
    URI is a logical one no store here could resolve."""
    entry = temporal_entries()["native-0"]
    aligned = entry[1].aligned_episode
    dataset = await _open(store, *(await _export(writer, [entry])))

    for index, step in enumerate(aligned.steps):
        original = step.observations["observation.image"].value
        restored = (
            (await dataset.get_step(_ref(entry), index))
            .observations["observation.image"]
            .value
        )
        assert restored.reference_uri == original.reference_uri
        selector = restored.reference_metadata["selector"]
        assert selector == original.reference_metadata["selector"]
        assert selector["kind"] == "video_frame"


# ── persistence ─────────────────────────────────────────────────────────


async def test_scope_order_and_shape_round_trip_through_parquet(writer, store) -> None:
    entries = list(temporal_entries().values())
    manifest, _ = await _export(writer, entries)
    data = await store.read_bytes(manifest.table_uris["learning_episodes"])
    rows = {r["episode_id"]: r for r in pl.read_parquet(io.BytesIO(data)).to_dicts()}
    for _, artifact in entries:
        aligned = artifact.aligned_episode
        assert rows[aligned.episode_id]["clock_scope"] == list(aligned.clock_scope)
        assert rows[aligned.episode_id]["clock_origin"] == aligned.clock_origin.value
    assert any(len(r["clock_scope"]) > 1 for r in rows.values())
    assert pq.read_table(io.BytesIO(data)).column("clock_scope").to_pylist() == [
        r["clock_scope"] for r in pl.read_parquet(io.BytesIO(data)).to_dicts()
    ]


async def test_unix_epoch_keeps_an_empty_scope_distinct_from_a_null_one(
    writer, store
) -> None:
    checksum, artifact = temporal_entries()["run-a-0"]
    epoch = artifact.model_copy(
        update={
            "aligned_episode": artifact.aligned_episode.model_copy(
                update={"clock_origin": ClockOrigin.UNIX_EPOCH, "clock_scope": ()}
            )
        }
    )
    entry = (checksum, epoch)
    manifest, written = await _export(writer, [entry])
    data = await store.read_bytes(manifest.table_uris["learning_episodes"])
    assert pl.read_parquet(io.BytesIO(data))["clock_scope"].to_list() == [[]]
    dataset = await _open(store, manifest, written)
    assert dataset.get_episode(_ref(entry)).clock_scope == ()


async def test_each_step_and_signal_resolves_to_its_own_episode_in_one_shard(
    writer, store
) -> None:
    """Identical timestamps, one shard: rows are told apart by their owner, and
    each owner's rows reconstruct exactly that Episode."""
    entries = list(temporal_entries().values())
    manifest, checksum = await _export(writer, entries)
    assert len(manifest.shard_index.learning_steps) == 1
    dataset = await _open(store, manifest, checksum)
    data = await store.read_bytes(manifest.shard_index.learning_steps[0].uri)
    per_owner = (
        pl.read_parquet(io.BytesIO(data))
        .group_by("episode_id", "aligned_artifact_checksum")
        .len()
    )
    assert per_owner.height == len(entries)

    instances = set()
    for entry in entries:
        aligned = entry[1].aligned_episode
        for index, step in enumerate(aligned.steps):
            restored = await dataset.get_step(_ref(entry), index)
            assert restored.timestamp_us == step.timestamp_us
            assert restored.observations.keys() == step.observations.keys()
            assert restored.actions.keys() == step.actions.keys()
        instances.add(dataset.get_episode(_ref(entry)).clock_instance)
    # run-a-0 and run-a-1 are one source instance; everything else is its own.
    assert len(instances) == len(entries) - 1


async def test_a_sequence_sample_leads_to_its_episodes_instance(writer, store) -> None:
    entries = [temporal_entries()["run-a-0"], temporal_entries()["run-b-0"]]
    dataset = await _open(store, *(await _export(writer, entries)))
    projection = FeatureProjection(
        observation_channels=["/odom#x"], action_channels=["/control#steering"]
    )
    for entry in entries:
        sample = await dataset.get_window(_ref(entry), 0, 1, projection)
        assert dataset.get_episode(sample.episode_ref).clock_instance == (
            entry[1].aligned_episode.clock_instance
        )


async def test_repeat_exports_are_byte_identical(writer, store, tmp_path) -> None:
    entries = list(temporal_entries().values())
    manifest, checksum = await _export(writer, entries)
    other_store = LocalArtifactStore(root_uri=str(tmp_path))
    other_writer = AnalyticsTableWriter(
        artifact_store=other_store, root_uri=str(tmp_path)
    )

    before = await _objects(writer)
    # Write-once: the same bytes at the same keys are a retry and no conflict, and
    # any other bytes there would have raised.
    again, checksum_again = await _export(other_writer, entries)
    assert checksum_again == checksum
    assert again == manifest
    assert await _objects(writer) == before


async def test_old_and_new_exports_of_the_same_inputs_coexist(writer, store) -> None:
    entries = list(temporal_entries().values())
    v1_uri, v1_bytes = await write_v1_export(writer, **DS, entries=entries)
    before = await _objects(writer)

    manifest, _ = await _export(writer, entries)

    v1_export_id = json.loads(v1_bytes)["exportId"]
    assert manifest.export_id != v1_export_id
    assert manifest.export_id[:16] != v1_export_id[:16]
    for uri, data in before.items():
        assert await store.read_bytes(uri) == data


# ── semantics are unchanged ─────────────────────────────────────────────


@pytest.mark.parametrize("fixture", ["temporal", "scale"])
def test_rows_are_what_they_were_before_v2(fixture) -> None:
    entries = (
        list(temporal_entries().values())
        if fixture == "temporal"
        else build_scaled_entries(
            ScaleSpec(name="d", num_episodes=5, steps_per_episode=4)
        )
    )
    excluded = {"export_id", "clock_origin", "clock_scope"}
    for table, builder in (
        ("episodes", build_learning_episodes_table),
        ("steps", build_learning_steps_table),
        ("signals", build_learning_signals_table),
    ):
        df = builder(
            dataset_id="d", dataset_version="v", export_id="e", entries=entries
        )
        rows = [
            {k: v for k, v in row.items() if k not in excluded}
            for row in df.iter_rows(named=True)
        ]
        digest = hashlib.sha256(
            json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        assert digest == SEMANTIC_DIGESTS_BEFORE_V2[(fixture, table)]


# ── corrupt temporal context ────────────────────────────────────────────


async def _rewrite_episodes(store, manifest, mutate, *, stamp=True) -> None:
    uri = manifest.table_uris["learning_episodes"]
    df = mutate(pl.read_parquet(io.BytesIO(await store.read_bytes(uri))))
    buffer = io.BytesIO()
    df.write_parquet(
        buffer,
        metadata=(
            {
                LEARNING_TABLE_SCHEMA_METADATA_KEY: learning_table_schema_stamp(
                    "learning_episodes"
                )
            }
            if stamp
            else None
        ),
    )
    await store.write_bytes(uri, buffer.getvalue())


def _set_first(column: str, value, dtype=None):
    def mutate(df: pl.DataFrame) -> pl.DataFrame:
        values = df[column].to_list()
        values[0] = value
        return df.with_columns(
            pl.Series(column, values, dtype=dtype or df[column].dtype)
        )

    return mutate


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (_set_first("clock_origin", "lunar"), "lunar"),
        (_set_first("clock_origin", None), "valid clock instance"),
        (_set_first("clock_scope", None), "valid clock instance"),
        (_set_first("clock_scope", []), "valid clock instance"),
        (_set_first("clock_scope", [None]), "valid clock instance"),
        (_set_first("clock_scope", [""]), "valid clock instance"),
        (_set_first("source_clock", "Not A Clock!"), "valid clock instance"),
        (_set_first("source_clock", None), "valid clock instance"),
        (
            lambda df: _set_first("clock_scope", ["a"])(
                _set_first("clock_origin", "unix_epoch")(df)
            ),
            "valid clock instance",
        ),
    ],
)
async def test_invalid_clock_context_is_refused_at_open(
    writer, store, mutate, message
) -> None:
    manifest, checksum = await _export(writer, [temporal_entries()["run-a-0"]])
    await _rewrite_episodes(store, manifest, mutate)
    with pytest.raises(LearningDataIntegrityError, match=message):
        await _open(store, manifest, checksum)


@pytest.mark.parametrize("column", ["clock_origin", "clock_scope"])
async def test_a_missing_column_is_refused_even_under_a_valid_stamp(
    writer, store, column
) -> None:
    manifest, checksum = await _export(writer, [temporal_entries()["run-a-0"]])
    await _rewrite_episodes(store, manifest, lambda df: df.drop(column))
    with pytest.raises(UnsupportedLearningDataSchemaError, match="columns"):
        await _open(store, manifest, checksum)


async def test_scope_elements_of_another_type_are_refused(writer, store) -> None:
    manifest, checksum = await _export(writer, [temporal_entries()["run-a-0"]])
    await _rewrite_episodes(
        store,
        manifest,
        lambda df: df.with_columns(pl.Series("clock_scope", [[1, 2]])),
    )
    with pytest.raises(UnsupportedLearningDataSchemaError, match="columns"):
        await _open(store, manifest, checksum)


async def test_two_rows_for_one_episode_ref_are_refused(writer, store) -> None:
    manifest, checksum = await _export(writer, [temporal_entries()["run-a-0"]])
    await _rewrite_episodes(store, manifest, lambda df: pl.concat([df, df]))
    with pytest.raises(LearningDataIntegrityError, match="more than one row"):
        await _open(store, manifest, checksum)


async def test_two_instances_of_one_clock_for_one_episode_are_refused(
    writer, store
) -> None:
    """Two revisions of an Episode may coexist, and a unit's source binding fixes
    its scope, so they cannot disagree about one clock."""
    entries = temporal_entries()
    run_a_checksum, run_a = entries["run-a-0"]
    _, run_b = entries["run-b-0"]
    conflicting = run_b.model_copy(
        update={
            "aligned_episode": run_b.aligned_episode.model_copy(
                update={"episode_id": run_a.aligned_episode.episode_id}
            )
        }
    )
    manifest, checksum = await _export(
        writer, [(run_a_checksum, run_a), ("sha256:" + "d" * 64, conflicting)]
    )
    with pytest.raises(LearningDataIntegrityError, match="two instances"):
        await _open(store, manifest, checksum)


async def test_two_revisions_of_an_episode_with_one_instance_are_accepted(
    writer, store
) -> None:
    checksum, artifact = temporal_entries()["run-a-0"]
    entries = [(checksum, artifact), ("sha256:" + "d" * 64, artifact)]
    dataset = await _open(store, *(await _export(writer, entries)))
    assert len(dataset) == 2
    assert {dataset.get_episode(_ref(entry)).clock_instance for entry in entries} == {
        artifact.aligned_episode.clock_instance
    }


async def test_invalid_context_of_a_row_a_curation_hides_is_still_refused(
    writer, store
) -> None:
    """The whole table is one contract; see _episode_metadata_by_ref."""
    entries = [temporal_entries()["run-a-0"], temporal_entries()["run-b-0"]]
    manifest, checksum = await _export(writer, entries)

    def corrupt_second(df: pl.DataFrame) -> pl.DataFrame:
        values = df["clock_origin"].to_list()
        values[1] = "lunar"
        return df.with_columns(pl.Series("clock_origin", values))

    await _rewrite_episodes(store, manifest, corrupt_second)
    with pytest.raises(LearningDataIntegrityError, match="lunar"):
        await _open(store, manifest, checksum)


# ── ownership: a row has one Episode, or it is refused ──────────────────


async def test_a_shard_index_member_with_no_episode_row_is_refused(
    writer, store
) -> None:
    entries = temporal_entries()
    manifest, checksum = await _export(writer, [entries["run-a-0"], entries["run-b-0"]])
    await _rewrite_episodes(store, manifest, lambda df: df.head(1))
    manifest = manifest.model_copy(update={"inputs": manifest.inputs[:1]})
    with pytest.raises(ShardIndexMismatchError, match="no row of\\s+learning_episodes"):
        await _open(store, manifest, checksum)


@pytest.mark.parametrize("table_name", ["learning_steps", "learning_signals"])
async def test_a_row_group_holding_another_episodes_rows_is_refused(
    writer, store, table_name
) -> None:
    """Identical timestamps, so nothing else would show: without the ownership
    check these rows would be read as the other Episode's, on its clock."""
    entries = temporal_entries()
    pair = [entries["run-a-0"], entries["run-b-0"]]
    manifest, checksum = await _export(writer, pair)
    shard = getattr(manifest.shard_index, table_name)[0]

    data = await store.read_bytes(shard.uri)
    table = pq.read_table(io.BytesIO(data))
    sizes = [member.row_count for member in shard.episodes]
    sink = io.BytesIO()
    with pq.ParquetWriter(sink, table.schema) as out:
        # Swapped: each member's row_group_index now names the other's rows.
        out.write_table(table.slice(sizes[0], sizes[1]), row_group_size=sizes[1])
        out.write_table(table.slice(0, sizes[0]), row_group_size=sizes[0])
    await store.write_bytes(shard.uri, sink.getvalue())
    updated = shard.model_copy(update={"size_bytes": len(sink.getvalue())})
    index = manifest.shard_index.model_copy(update={table_name: [updated]})
    manifest = manifest.model_copy(update={"shard_index": index})

    dataset = await _open(store, manifest, checksum)
    with pytest.raises(LearningDataIntegrityError, match="holds rows of"):
        await dataset.get_step(_ref(pair[0]), 0)


@pytest.mark.parametrize("builder_name", ["learning_steps", "learning_signals"])
async def test_a_single_file_row_of_no_episode_is_refused(
    writer, store, builder_name
) -> None:
    entry = temporal_entries()["run-a-0"]
    builders = {
        "learning_steps": build_learning_steps_table,
        "learning_signals": build_learning_signals_table,
    }
    uris = {}
    for name, builder in builders.items():
        df = builder(**DS, export_id="e" * 64, entries=[entry])
        if name == builder_name:
            df = df.with_columns(pl.lit("ep-nobody").alias("episode_id"))
        uris[name] = (
            await writer.write_learning_table(name, df, **DS, export_id="e" * 64)
        ).uri
    manifest, checksum = await _export(writer, [entry])
    manifest = manifest.model_copy(
        update={
            "shard_index": None,
            "table_uris": {**manifest.table_uris, **uris},
        }
    )
    dataset = await _open(store, manifest, checksum)
    with pytest.raises(LearningDataIntegrityError, match="ep-nobody"):
        await dataset.get_step(_ref(entry), 0)


# ── version rejection ───────────────────────────────────────────────────


@pytest.mark.parametrize("version", ["v1", "v999"])
async def test_the_dataset_refuses_a_manifest_object_of_another_schema(
    writer, store, version
) -> None:
    manifest, checksum = await _export(writer, [temporal_entries()["run-a-0"]])
    stale = manifest.model_copy(update={"schema_version": version})
    with pytest.raises(UnsupportedLearningDataSchemaError, match=version):
        await _open(store, stale, checksum)
    with pytest.raises(UnsupportedLearningDataSchemaError, match=version):
        await check_learning_export_tables(store, stale)


async def test_a_real_v1_export_is_refused_and_left_as_it_is(writer, store) -> None:
    entries = list(temporal_entries().values())
    uri, data = await write_v1_export(writer, **DS, entries=entries)
    objects = await _objects(writer)

    with pytest.raises(UnsupportedLearningDataSchemaError, match="'v1'"):
        LearningDataExportManifest.model_validate_json(data)

    # Claiming to be v2 does not make its tables v2: every file is judged by what
    # its own footer states.
    document = json.loads(data)
    document["schemaVersion"] = LEARNING_DATA_SCHEMA_VERSION
    claimed = LearningDataExportManifest.model_validate(document)
    with pytest.raises(UnsupportedLearningDataSchemaError, match="None"):
        await _open(store, claimed, "sha256:x")
    with pytest.raises(UnsupportedLearningDataSchemaError, match="None"):
        await check_learning_export_tables(store, claimed)

    for object_uri, object_data in objects.items():
        assert await store.read_bytes(object_uri) == object_data
    assert await store.read_bytes(uri) == data


@pytest.mark.parametrize(
    "stated",
    [
        None,
        b"learning_data/v1/learning_episodes",
        b"learning_data/v999/learning_episodes",
        b"learning_data/v2/learning_steps",
        b"scene_analytics/v2/learning_episodes",
    ],
)
async def test_learning_episodes_with_another_stamp_is_refused(
    writer, store, stated
) -> None:
    manifest, checksum = await _export(writer, [temporal_entries()["run-a-0"]])
    uri = manifest.table_uris["learning_episodes"]
    df = pl.read_parquet(io.BytesIO(await store.read_bytes(uri)))
    buffer = io.BytesIO()
    df.write_parquet(
        buffer,
        metadata=(
            {LEARNING_TABLE_SCHEMA_METADATA_KEY: stated.decode()} if stated else None
        ),
    )
    await store.write_bytes(uri, buffer.getvalue())
    with pytest.raises(UnsupportedLearningDataSchemaError, match="footer states"):
        await _open(store, manifest, checksum)


async def _replace_shard_stamp(store, manifest, table_name, stated):
    """A shard whose columns are exactly right and whose footer states ``stated``:
    steps and signals have the same columns under v1 and v2, so only the stamp can
    tell them apart."""
    shard = getattr(manifest.shard_index, table_name)[0]
    data = await store.read_bytes(shard.uri)
    table = pq.read_table(io.BytesIO(data))
    metadata = {k: v for k, v in (table.schema.metadata or {}).items()}
    metadata.pop(LEARNING_TABLE_SCHEMA_METADATA_KEY.encode(), None)
    if stated is not None:
        metadata[LEARNING_TABLE_SCHEMA_METADATA_KEY.encode()] = stated
    table = table.replace_schema_metadata(metadata or None)
    sink = io.BytesIO()
    pq.write_table(table, sink)
    await store.write_bytes(shard.uri, sink.getvalue())
    updated = shard.model_copy(update={"size_bytes": len(sink.getvalue())})
    index = manifest.shard_index.model_copy(update={table_name: [updated]})
    return manifest.model_copy(update={"shard_index": index})


@pytest.mark.parametrize("table_name", ["learning_steps", "learning_signals"])
@pytest.mark.parametrize(
    "stated",
    [None, b"learning_data/v1/{table}", b"learning_data/v999/{table}", b"nonsense"],
)
@pytest.mark.parametrize("cache", ["default", "disabled"])
async def test_a_shard_with_compatible_columns_and_another_stamp_is_refused(
    writer, store, table_name, stated, cache
) -> None:
    entry = temporal_entries()["run-a-0"]
    manifest, checksum = await _export(writer, [entry])
    manifest = await _replace_shard_stamp(
        store,
        manifest,
        table_name,
        stated.replace(b"{table}", table_name.encode()) if stated else None,
    )
    with pytest.raises(UnsupportedLearningDataSchemaError, match="footer states"):
        await check_learning_export_tables(store, manifest)

    # Opening stays free of per-shard reads, so the refusal comes with the first
    # read of that shard, before any of its rows are handed out.
    kwargs = {"cache_policy": DISABLED_CACHE_POLICY} if cache == "disabled" else {}
    dataset = await _open(store, manifest, checksum, **kwargs)
    with pytest.raises(UnsupportedLearningDataSchemaError, match="footer states"):
        await dataset.get_step(_ref(entry), 0)


@pytest.mark.parametrize("table_name", ["learning_steps", "learning_signals"])
async def test_the_bulk_read_path_refuses_a_shard_with_another_stamp(
    writer, store, table_name
) -> None:
    entry = temporal_entries()["run-a-0"]
    manifest, checksum = await _export(writer, [entry])
    manifest = await _replace_shard_stamp(
        store, manifest, table_name, f"learning_data/v1/{table_name}".encode()
    )
    dataset = await _open(store, manifest, checksum)
    with pytest.raises(UnsupportedLearningDataSchemaError, match="footer states"):
        await dataset.preload_episodes([_ref(entry)])


async def test_a_shard_of_one_table_cannot_stand_in_for_the_other(
    writer, store
) -> None:
    entry = temporal_entries()["run-a-0"]
    manifest, _ = await _export(writer, [entry])
    manifest = await _replace_shard_stamp(
        store, manifest, "learning_signals", b"learning_data/v2/learning_steps"
    )
    with pytest.raises(UnsupportedLearningDataSchemaError, match="learning_signals"):
        await check_learning_export_tables(store, manifest)


async def test_a_file_that_is_no_parquet_is_refused(writer, store) -> None:
    manifest, checksum = await _export(writer, [temporal_entries()["run-a-0"]])
    await store.write_bytes(manifest.table_uris["learning_episodes"], b"not parquet")
    with pytest.raises(UnsupportedLearningDataSchemaError, match="not a readable"):
        await _open(store, manifest, checksum)


async def test_a_current_export_passes_the_footer_check_without_reading_rows(
    writer, store
) -> None:
    manifest, _ = await _export(writer, list(temporal_entries().values()))
    await check_learning_export_tables(store, manifest)
