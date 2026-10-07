"""AnalyticsTableWriter objects are write-once: a retry that reproduces the same
bytes is a no-op, different bytes under an existing key are a conflict, and
snapshot tables are named by their own content so a rebuild from changed data
adds a revision beside the old one. Filesystem-only."""

from __future__ import annotations

import polars as pl
import pytest

from sceneops_analytics import AnalyticsTableWriter
from sceneops_core.common.checksums import sha256_checksum
from sceneops_storage import LocalArtifactStore, WriteOnceConflictError


@pytest.fixture()
def store(tmp_path):
    return LocalArtifactStore(root_uri=str(tmp_path))


@pytest.fixture()
def writer(store, tmp_path):
    return AnalyticsTableWriter(artifact_store=store, root_uri=str(tmp_path / "a"))


async def test_a_snapshot_table_result_pins_the_bytes_at_its_content_named_uri(
    writer, store
):
    df = pl.DataFrame({"scene_id": ["s1", "s2"], "n": [1, 2]})

    result = await writer.write_table("scenes", df, dataset_id="d", dataset_version="v")

    data = await store.read_bytes(result.uri)
    assert result.checksum == sha256_checksum(data)
    assert result.size_bytes == len(data)
    assert result.checksum.removeprefix("sha256:") in result.uri


async def test_rebuilding_a_snapshot_from_the_same_data_converges(writer):
    df = pl.DataFrame({"scene_id": ["s1", "s2"], "n": [1, 2]})

    one = await writer.write_table("scenes", df, dataset_id="d", dataset_version="v")
    two = await writer.write_table("scenes", df, dataset_id="d", dataset_version="v")

    assert (two.uri, two.checksum) == (one.uri, one.checksum)


async def test_rebuilding_a_snapshot_from_changed_data_keeps_the_old_revision(
    writer, store
):
    one = await writer.write_table(
        "scenes",
        pl.DataFrame({"scene_id": ["s1"]}),
        dataset_id="d",
        dataset_version="v",
    )
    old_bytes = await store.read_bytes(one.uri)

    two = await writer.write_table(
        "scenes",
        pl.DataFrame({"scene_id": ["s1", "s2"]}),
        dataset_id="d",
        dataset_version="v",
    )

    assert two.uri != one.uri
    assert await store.read_bytes(one.uri) == old_bytes


async def test_a_robot_run_table_is_a_content_named_revision_too(writer):
    one = await writer.write_robot_run_table(
        "missions", pl.DataFrame({"m": [1]}), robot_run_id="run-1"
    )
    two = await writer.write_robot_run_table(
        "missions", pl.DataFrame({"m": [1, 2]}), robot_run_id="run-1"
    )
    assert one.uri != two.uri


async def test_an_export_id_never_names_two_different_tables(writer, store):
    kwargs = dict(dataset_id="d", dataset_version="v", export_id="e" * 64)
    first = await writer.write_learning_table(
        "learning_episodes", pl.DataFrame({"x": [1]}), **kwargs
    )
    again = await writer.write_learning_table(
        "learning_episodes", pl.DataFrame({"x": [1]}), **kwargs
    )
    assert again.checksum == first.checksum

    with pytest.raises(WriteOnceConflictError):
        await writer.write_learning_table(
            "learning_episodes", pl.DataFrame({"x": [1, 2]}), **kwargs
        )
    assert sha256_checksum(await store.read_bytes(first.uri)) == first.checksum


async def test_a_manifest_key_is_write_once(writer):
    class _Manifest:
        def __init__(self, payload):
            self._payload = payload

        def to_artifact_dict(self):
            return self._payload

    kwargs = dict(dataset_id="d", dataset_version="v", export_id="e" * 64)
    await writer.write_learning_export_manifest(_Manifest({"a": 1}), **kwargs)
    await writer.write_learning_export_manifest(_Manifest({"a": 1}), **kwargs)
    with pytest.raises(WriteOnceConflictError):
        await writer.write_learning_export_manifest(_Manifest({"a": 2}), **kwargs)

    curation = dict(dataset_id="d", dataset_version="v", curation_id="c" * 64)
    await writer.write_curation_manifest(_Manifest({"a": 1}), **curation)
    with pytest.raises(WriteOnceConflictError):
        await writer.write_curation_manifest(_Manifest({"a": 2}), **curation)
