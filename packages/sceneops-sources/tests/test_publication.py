"""Database-free snapshot publication on a real LocalArtifactStore (ADR-015 §3, §5).

Every stored object is compared with the source bytes; ids are recomputed with
hashlib (tests/tiny_source.py)."""

from __future__ import annotations

import asyncio
import json

import pytest

from sceneops_core.sources import (
    SnapshotSelection,
    load_canonical_source_content_inventory,
    load_canonical_source_snapshot_descriptor,
)
from sceneops_sources import (
    ContentPublicationConflictError,
    SnapshotPublicationConflictError,
    SourceFileChangedError,
    SourceTreeError,
    publication,
    publish_snapshot,
)
from tests.tiny_source import (
    SELECTED,
    TREE,
    declaration,
    expected_content_id,
    object_bytes,
    sha,
)

LICENSE = {"license": "mit"}


async def _publish(store, sources_root, source_dir, decl=None, annotations=LICENSE):
    return await publish_snapshot(
        store=store,
        sources_root=sources_root,
        source_dir=source_dir,
        declaration=decl or declaration(),
        annotations=annotations,
    )


class RecordingStore:
    """A real store that records the order of writes and can fail one write."""

    def __init__(self, inner, *, fail_on_write: int | None = None):
        self._inner = inner
        self.writes: list[str] = []
        self._fail_on_write = fail_on_write

    def __getattr__(self, name):
        return getattr(self._inner, name)

    async def write_bytes(self, uri, data):
        if self._fail_on_write is not None and len(self.writes) == self._fail_on_write:
            raise ConnectionError(f"injected crash before writing {uri}")
        self.writes.append(uri)
        await self._inner.write_bytes(uri, data)


# --- layout, bytes and identity ---------------------------------------------------


async def test_publication_preserves_the_selected_bytes_under_the_documented_keys(
    make_tree, store, sources_root
):
    published = await _publish(store, sources_root, make_tree())
    content_id = expected_content_id({p: TREE[p] for p in SELECTED})
    assert published.content_id == content_id
    content_root = f"{sources_root}/contents/{content_id}"
    stored = await object_bytes(store, sources_root)
    expected_files = {f"{content_root}/files/{p}": TREE[p] for p in SELECTED}
    assert {u: b for u, b in stored.items() if "/files/" in u} == expected_files
    assert published.inventory_uri == f"{content_root}/inventory.json"
    assert published.descriptor_uri == (
        f"{sources_root}/snapshots/{published.snapshot_id}/descriptor.json"
    )
    assert set(stored) == {
        *expected_files,
        published.inventory_uri,
        published.descriptor_uri,
    }
    assert (published.files_written, published.files_reused) == (len(SELECTED), 0)


async def test_inventory_and_descriptor_objects_are_canonical_and_name_each_other(
    make_tree, store, sources_root
):
    root = make_tree()
    published = await _publish(store, sources_root, root)
    inventory_bytes = await store.read_bytes(published.inventory_uri)
    descriptor_bytes = await store.read_bytes(published.descriptor_uri)
    inventory = load_canonical_source_content_inventory(inventory_bytes)
    descriptor = load_canonical_source_snapshot_descriptor(descriptor_bytes)
    assert [(e.path, e.size_bytes, e.sha256) for e in inventory.files] == [
        (p, len(TREE[p]), sha(TREE[p])) for p in SELECTED
    ]
    assert descriptor.content_id == inventory.content_id
    assert descriptor.annotations == LICENSE
    assert published.descriptor_checksum == sha(descriptor_bytes)
    # The descriptor carries no retrieval facts: no local path, no time.
    assert str(root) not in descriptor_bytes.decode()
    assert set(json.loads(descriptor_bytes)) == {
        "annotations",
        "content_id",
        "declaration",
        "schema_version",
    }


async def test_the_descriptor_is_written_last(make_tree, store, sources_root):
    recording = RecordingStore(store)
    published = await _publish(recording, sources_root, make_tree())
    assert recording.writes[-1] == published.descriptor_uri
    assert recording.writes[-2] == published.inventory_uri


async def test_keys_keep_plus_space_and_non_ascii_paths(make_tree, store, sources_root):
    names = {
        "samples/CAM_FRONT/n015-2018-07-24-11-22-45+0800__CAM_FRONT__1532402927612460.jpg": b"\xff\xd8jpeg",
        "maps/read me.txt": b"map",
        "maps/지도.bin": b"\x00",
    }
    root = make_tree({**TREE, **names})
    decl = declaration(selection=SnapshotSelection(include=("maps/**", "samples/**")))
    published = await _publish(store, sources_root, root, decl)
    for path, data in names.items():
        uri = f"{sources_root}/contents/{published.content_id}/files/{path}"
        assert await store.read_bytes(uri) == data


# --- idempotency and deduplication (ADR-015 §3 case table) -------------------------


async def test_a_retry_writes_nothing(make_tree, store, sources_root):
    root = make_tree()
    first = await _publish(store, sources_root, root)
    before = await object_bytes(store, sources_root)
    recording = RecordingStore(store)
    second = await _publish(recording, sources_root, root)
    assert recording.writes == []
    assert not second.wrote_anything
    assert second.descriptor == first.descriptor
    assert await object_bytes(store, sources_root) == before


async def test_new_upstream_revision_with_identical_bytes_reuses_the_content(
    make_tree, store, sources_root
):
    first = await _publish(store, sources_root, make_tree(name="a"))
    recording = RecordingStore(store)
    second = await _publish(
        recording,
        sources_root,
        make_tree(name="b"),  # another local copy of the same bytes
        declaration(upstream_revision="b" * 40),
    )
    assert second.content_id == first.content_id
    assert second.snapshot_id != first.snapshot_id
    assert (second.files_written, second.files_reused) == (0, len(SELECTED))
    assert not second.inventory_written
    assert recording.writes == [second.descriptor_uri]


async def test_new_upstream_revision_with_different_bytes_is_a_new_content(
    make_tree, store, sources_root
):
    first = await _publish(store, sources_root, make_tree(name="a"))
    changed = make_tree({**TREE, "meta/info.json": b'{"fps":30}'}, name="b")
    second = await _publish(
        store, sources_root, changed, declaration(upstream_revision="c" * 40)
    )
    assert second.content_id != first.content_id
    # Unchanged files are stored again under the new content: files are stored
    # once per content, not deduplicated across contents.
    assert second.files_written == len(SELECTED)


async def test_two_selections_of_identical_files_share_one_content(
    make_tree, store, sources_root
):
    root = make_tree()
    first = await _publish(store, sources_root, root)
    wide = declaration(
        selection=SnapshotSelection(
            include=("README.md", "data/**", "meta/*", "meta/**", "videos/**")
        )
    )
    second = await _publish(store, sources_root, root, wide)
    assert second.content_id == first.content_id
    assert second.snapshot_id != first.snapshot_id
    assert second.files_written == 0


async def test_same_bytes_under_another_format_publish_to_the_same_content(
    make_tree, store, sources_root
):
    """Content keys are format-free; the format conflict is decided by registration."""
    root = make_tree()
    first = await _publish(store, sources_root, root)
    other = await _publish(store, sources_root, root, declaration(format="nuscenes"))
    assert other.content_id == first.content_id
    assert other.snapshot_id != first.snapshot_id


# --- conflicts write nothing -------------------------------------------------------


async def test_same_declaration_with_different_bytes_fails_and_writes_nothing(
    make_tree, store, sources_root
):
    await _publish(store, sources_root, make_tree(name="a"))
    before = await object_bytes(store, sources_root)
    changed = make_tree({**TREE, "README.md": b"# changed upstream\n"}, name="b")
    with pytest.raises(SnapshotPublicationConflictError, match="two byte sets"):
        await _publish(store, sources_root, changed)
    assert await object_bytes(store, sources_root) == before


async def test_same_declaration_with_other_annotations_fails_and_writes_nothing(
    make_tree, store, sources_root
):
    root = make_tree()
    await _publish(store, sources_root, root)
    before = await object_bytes(store, sources_root)
    with pytest.raises(SnapshotPublicationConflictError, match="annotations"):
        await _publish(store, sources_root, root, annotations={"license": "apache-2.0"})
    assert await object_bytes(store, sources_root) == before


async def test_racing_publishers_of_one_declaration_with_different_bytes(
    make_tree, store, sources_root, monkeypatch
):
    """Both pass the early check; the write-once descriptor key decides the race."""

    async def no_early_check(*_args):
        return None

    monkeypatch.setattr(publication, "_refuse_conflicting_descriptor", no_early_check)
    await _publish(store, sources_root, make_tree(name="a"))
    changed = make_tree({**TREE, "README.md": b"# other\n"}, name="b")
    with pytest.raises(SnapshotPublicationConflictError, match="two byte sets"):
        await _publish(store, sources_root, changed)
    descriptors = [
        u for u in await object_bytes(store, sources_root) if "/snapshots/" in u
    ]
    assert len(descriptors) == 1


async def test_concurrent_identical_publications_converge(
    make_tree, store, sources_root
):
    root = make_tree()
    results = await asyncio.gather(
        *(_publish(store, sources_root, root) for _ in range(4))
    )
    assert len({r.descriptor_checksum for r in results}) == 1
    assert (
        len([u for u in await object_bytes(store, sources_root) if "/snapshots/" in u])
        == 1
    )


async def test_a_corrupted_stored_content_file_fails_republication(
    make_tree, store, sources_root
):
    root = make_tree()
    published = await _publish(store, sources_root, root)
    victim = f"{sources_root}/contents/{published.content_id}/files/meta/info.json"
    await store.write_bytes(victim, b"tampered")
    with pytest.raises(ContentPublicationConflictError, match="meta/info.json"):
        await _publish(
            store, sources_root, root, declaration(upstream_revision="d" * 40)
        )
    assert await store.read_bytes(victim) == b"tampered"  # never overwritten


async def test_invalid_selections_fail_before_any_write(make_tree, store, sources_root):
    root = make_tree()
    with pytest.raises(SourceTreeError, match="match no file"):
        await _publish(
            store,
            sources_root,
            root,
            declaration(selection=SnapshotSelection(include=("nothing/**",))),
        )
    with pytest.raises(ValueError):
        declaration(selection=SnapshotSelection(include=("data/[0-9]*",)))
    assert await store.list_objects(sources_root) == []


# --- interrupted publication -------------------------------------------------------


@pytest.mark.parametrize("crash_at", [0, 2, len(SELECTED)])
async def test_a_crash_before_the_descriptor_publishes_nothing_and_a_retry_converges(
    make_tree, store, sources_root, crash_at
):
    """crash_at = the number of writes that succeed: 0 (no file), 2 (some files),
    len(SELECTED) (every file, no inventory)."""
    root = make_tree()
    with pytest.raises(ConnectionError):
        await _publish(
            RecordingStore(store, fail_on_write=crash_at), sources_root, root
        )
    left = await object_bytes(store, sources_root)
    assert not any(u.endswith(("descriptor.json", "inventory.json")) for u in left)
    assert len(left) == crash_at

    published = await _publish(store, sources_root, root)
    assert (published.files_written, published.files_reused) == (
        len(SELECTED) - crash_at,
        crash_at,
    )
    assert published.descriptor_written


async def test_a_crash_between_inventory_and_descriptor_leaves_no_snapshot(
    make_tree, store, sources_root
):
    root = make_tree()
    with pytest.raises(ConnectionError):
        await _publish(
            RecordingStore(store, fail_on_write=len(SELECTED) + 1), sources_root, root
        )
    left = await object_bytes(store, sources_root)
    assert any(u.endswith("inventory.json") for u in left)
    assert not any("/snapshots/" in u for u in left)
    recording = RecordingStore(store)
    published = await _publish(recording, sources_root, root)
    assert recording.writes == [published.descriptor_uri]


async def test_a_source_file_changing_after_hashing_fails_publication(
    make_tree, store, sources_root
):
    root = make_tree()

    class ChangingStore(RecordingStore):
        async def write_bytes(self, uri, data):
            # Upstream sync rewrites a not-yet-uploaded file mid-publication.
            (root / "videos/observation.image/chunk-000/file-000.mp4").write_bytes(
                b"new"
            )
            await super().write_bytes(uri, data)

    with pytest.raises(SourceFileChangedError, match="file-000.mp4"):
        await _publish(ChangingStore(store), sources_root, root)
    assert not any("/snapshots/" in u for u in await object_bytes(store, sources_root))
