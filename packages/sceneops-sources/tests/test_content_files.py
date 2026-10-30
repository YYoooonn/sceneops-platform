"""Verified content file access and the source object layout (ADR-015 §5; D4, D11)."""

from __future__ import annotations

import os

import pytest

from sceneops_core.sources import SnapshotSelection
from sceneops_sources import (
    ContentFileIntegrityError,
    ContentFileMissingError,
    LocalContentFiles,
    SourceLayoutError,
    StoredContentFiles,
    build_inventory,
    content_file_uri,
    content_files_prefix,
    parse_descriptor_uri,
    publish_snapshot,
    select_files,
)
from sceneops_storage import S3ArtifactStore
from tests.tiny_source import INCLUDE, SELECTED, TREE, declaration, sha

CONTENT_ID = "content-" + "0" * 32
SNAPSHOT_ID = "snapshot-" + "1" * 32


async def _published(store, sources_root, root):
    return await publish_snapshot(
        store=store,
        sources_root=sources_root,
        source_dir=root,
        declaration=declaration(),
    )


# --- layout ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "root",
    [
        "/data/artifacts/sources",
        "s3://sceneops/artifacts/sources",
        "file:///tmp/a/sources",
    ],
)
def test_layout_round_trips_for_every_uri_style(root):
    store = S3ArtifactStore.__new__(S3ArtifactStore)  # join_uri needs no client
    from sceneops_sources import descriptor_uri, inventory_uri

    inventory = inventory_uri(store, root, CONTENT_ID)
    assert inventory == f"{root}/contents/{CONTENT_ID}/inventory.json"
    assert content_file_uri(inventory, "meta/info.json") == (
        f"{root}/contents/{CONTENT_ID}/files/meta/info.json"
    )
    assert content_files_prefix(inventory) == f"{root}/contents/{CONTENT_ID}/files"
    descriptor = descriptor_uri(store, root, SNAPSHOT_ID)
    assert parse_descriptor_uri(descriptor) == (root, SNAPSHOT_ID)


@pytest.mark.parametrize(
    "uri",
    [
        f"s3://b/sources/snapshots/{SNAPSHOT_ID}/descriptor.jsonx",
        f"s3://b/sources/snapshot/{SNAPSHOT_ID}/descriptor.json",
        "s3://b/sources/snapshots/snapshot-xyz/descriptor.json",
        f"snapshots/{SNAPSHOT_ID}/descriptor.json",
    ],
)
def test_descriptor_uris_outside_the_layout_are_refused(uri):
    with pytest.raises(SourceLayoutError):
        parse_descriptor_uri(uri)


@pytest.mark.parametrize("path", ["../escape.bin", "a/../../b", "/abs", "a//b"])
def test_content_file_uris_never_leave_the_content_prefix(path):
    with pytest.raises(ValueError):
        content_file_uri(f"s3://b/sources/contents/{CONTENT_ID}/inventory.json", path)


def test_content_file_uris_derive_only_from_an_inventory_uri():
    with pytest.raises(SourceLayoutError):
        content_file_uri(f"s3://b/sources/other/{CONTENT_ID}/inventory.json", "a.bin")
    with pytest.raises(SourceLayoutError):
        content_file_uri("s3://b/sources/contents/content-zz/inventory.json", "a.bin")


# --- LocalContentFiles ---------------------------------------------------------------


def _local(root):
    inventory = build_inventory(
        root, select_files(root, SnapshotSelection(include=INCLUDE))
    )
    return LocalContentFiles(root, inventory)


def test_local_files_hand_out_verified_paths_only(make_tree):
    root = make_tree()
    files = _local(root)
    for path in SELECTED:
        assert files.local_path(path).read_bytes() == TREE[path]
    with pytest.raises(ContentFileMissingError, match="not a file of"):
        files.local_path(".gitattributes")  # present on disk, not in the content


def test_local_files_refuse_changed_missing_or_linked_files(make_tree, tmp_path):
    root = make_tree()
    files = _local(root)
    (root / "README.md").write_bytes(b"---\nlicense: MIT\n---\n# tiny\n")  # same size
    with pytest.raises(ContentFileIntegrityError, match="sha256"):
        files.local_path("README.md")
    (root / "meta/info.json").write_bytes(b"{}")
    with pytest.raises(ContentFileIntegrityError, match="bytes"):
        files.local_path("meta/info.json")
    (root / "meta/tasks.parquet").unlink()
    with pytest.raises(ContentFileMissingError):
        files.local_path("meta/tasks.parquet")
    target = tmp_path / "copy.mp4"
    video = "videos/observation.image/chunk-000/file-000.mp4"
    target.write_bytes(TREE[video])
    (root / video).unlink()
    os.symlink(target, root / video)
    with pytest.raises(ContentFileIntegrityError, match="regular file"):
        files.local_path(video)


# --- StoredContentFiles --------------------------------------------------------------


async def test_stored_files_materialize_byte_identical_verified_files(
    make_tree, store, sources_root, tmp_path
):
    published = await _published(store, sources_root, make_tree())
    files = await StoredContentFiles.materialize(
        store=store,
        inventory_uri=published.inventory_uri,
        content_checksum=published.inventory.content_checksum,
        workdir=tmp_path / "work",
    )
    assert files.inventory == published.inventory
    for path in SELECTED:
        assert sha(files.local_path(path).read_bytes()) == sha(TREE[path])
    leftovers = [
        p for p in (tmp_path / "work").rglob("*") if p.name.endswith(".partial")
    ]
    assert leftovers == []


async def test_stored_files_materialize_only_the_requested_subset(
    make_tree, store, sources_root, tmp_path
):
    published = await _published(store, sources_root, make_tree())
    files = await StoredContentFiles.materialize(
        store=store,
        inventory_uri=published.inventory_uri,
        content_checksum=published.inventory.content_checksum,
        workdir=tmp_path / "work",
        paths=["meta/info.json"],
    )
    assert files.local_path("meta/info.json").read_bytes() == TREE["meta/info.json"]
    with pytest.raises(ContentFileMissingError, match="not materialized"):
        files.local_path("README.md")
    assert not (tmp_path / "work" / "README.md").exists()
    with pytest.raises(ContentFileMissingError, match="not a file of"):
        await StoredContentFiles.materialize(
            store=store,
            inventory_uri=published.inventory_uri,
            content_checksum=published.inventory.content_checksum,
            workdir=tmp_path / "work",
            paths=["notes/empty.txt"],
        )


async def test_the_inventory_must_be_the_pinned_one(
    make_tree, store, sources_root, tmp_path
):
    first = await _published(store, sources_root, make_tree(name="a"))
    other_tree = make_tree({**TREE, "README.md": b"# other\n"}, name="b")
    other = await publish_snapshot(
        store=store,
        sources_root=sources_root,
        source_dir=other_tree,
        declaration=declaration(upstream_revision="e" * 40),
    )
    with pytest.raises(ContentFileIntegrityError, match="content_checksum"):
        await StoredContentFiles.materialize(
            store=store,
            inventory_uri=first.inventory_uri,
            content_checksum=other.inventory.content_checksum,
            workdir=tmp_path / "w1",
        )
    # The other content's inventory copied under the first content's key.
    moved = first.inventory_uri.replace(first.content_id, "content-" + "f" * 32)
    await store.write_bytes(moved, await store.read_bytes(first.inventory_uri))
    with pytest.raises(ContentFileIntegrityError, match="holds the inventory of"):
        await StoredContentFiles.materialize(
            store=store,
            inventory_uri=moved,
            content_checksum=first.inventory.content_checksum,
            workdir=tmp_path / "w2",
        )
    pretty = first.inventory_uri.replace(first.content_id, "content-" + "e" * 32)
    await store.write_json(pretty, first.inventory.model_dump(mode="json"))
    with pytest.raises(ContentFileIntegrityError, match="canonical"):
        await StoredContentFiles.materialize(
            store=store,
            inventory_uri=pretty,
            content_checksum=first.inventory.content_checksum,
            workdir=tmp_path / "w3",
        )


async def test_tampered_or_missing_stored_files_are_refused_and_not_materialized(
    make_tree, store, sources_root, tmp_path
):
    published = await _published(store, sources_root, make_tree())
    pin = {
        "store": store,
        "inventory_uri": published.inventory_uri,
        "content_checksum": published.inventory.content_checksum,
    }
    data_uri = content_file_uri(
        published.inventory_uri, "data/chunk-000/file-000.parquet"
    )
    original = await store.read_bytes(data_uri)
    await store.write_bytes(
        data_uri, original[:-1] + b"X"
    )  # same size, one byte changed
    with pytest.raises(ContentFileIntegrityError, match="sha256"):
        await StoredContentFiles.materialize(**pin, workdir=tmp_path / "w1")
    assert not (tmp_path / "w1" / "data/chunk-000/file-000.parquet").exists()

    await store.write_bytes(data_uri, original)
    await store.delete_prefix(
        content_file_uri(published.inventory_uri, "meta/tasks.parquet")
    )
    with pytest.raises(ContentFileMissingError, match="meta/tasks.parquet"):
        await StoredContentFiles.materialize(**pin, workdir=tmp_path / "w2")
    with pytest.raises(ContentFileMissingError, match="inventory is missing"):
        await StoredContentFiles.materialize(
            **{
                **pin,
                "inventory_uri": published.inventory_uri.replace(
                    published.content_id, "content-" + "a" * 32
                ),
            },
            workdir=tmp_path / "w3",
        )


async def test_interrupted_materialization_resumes_and_never_trusts_a_bad_file(
    make_tree, store, sources_root, tmp_path
):
    published = await _published(store, sources_root, make_tree())
    pin = {
        "store": store,
        "inventory_uri": published.inventory_uri,
        "content_checksum": published.inventory.content_checksum,
    }
    workdir = tmp_path / "work"

    class FailingStore:
        def __init__(self, inner, fail_after):
            self._inner, self._reads = inner, 0
            self._fail_after = fail_after

        def __getattr__(self, name):
            return getattr(self._inner, name)

        async def read_bytes(self, uri):
            if "/files/" in uri:
                if self._reads == self._fail_after:
                    raise ConnectionError("injected crash")
                self._reads += 1
            return await self._inner.read_bytes(uri)

    with pytest.raises(ConnectionError):
        await StoredContentFiles.materialize(
            **{**pin, "store": FailingStore(store, 2)}, workdir=workdir
        )
    # A partial temporary file left by a killed process never counts as a file.
    (workdir / "videos/observation.image/chunk-000").mkdir(parents=True, exist_ok=True)
    (
        workdir / "videos/observation.image/chunk-000/.file-000.mp4.dead.partial"
    ).write_bytes(b"x")
    files = await StoredContentFiles.materialize(**pin, workdir=workdir)
    for path in SELECTED:
        assert files.local_path(path).read_bytes() == TREE[path]

    (workdir / "README.md").write_bytes(b"edited in the workdir")
    with pytest.raises(ContentFileIntegrityError, match="README.md"):
        await StoredContentFiles.materialize(**pin, workdir=workdir)
    assert (
        workdir / "README.md"
    ).read_bytes() == b"edited in the workdir"  # never overwritten


async def test_materialization_refuses_a_workdir_link_out_of_the_workdir(
    make_tree, store, sources_root, tmp_path
):
    published = await _published(store, sources_root, make_tree())
    workdir = tmp_path / "work"
    workdir.mkdir()
    (tmp_path / "elsewhere").mkdir()
    os.symlink(tmp_path / "elsewhere", workdir / "meta")
    with pytest.raises(ContentFileIntegrityError, match="outside"):
        await StoredContentFiles.materialize(
            store=store,
            inventory_uri=published.inventory_uri,
            content_checksum=published.inventory.content_checksum,
            workdir=workdir,
        )
    assert list((tmp_path / "elsewhere").iterdir()) == []
