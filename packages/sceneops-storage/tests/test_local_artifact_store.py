"""Unit coverage for LocalArtifactStore.read_range (SceneOps V2 Request
5.3) -- filesystem-only, no real infra required despite living alongside
this package's MinIO integration tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sceneops_storage.backends.local import LocalArtifactStore
from sceneops_storage.exceptions import ArtifactNotFoundError, ArtifactReadError


@pytest.fixture()
def store(tmp_path):
    return LocalArtifactStore(root_uri=str(tmp_path))


async def test_read_range_returns_exact_slice(store, tmp_path):
    uri = str(tmp_path / "blob.bin")
    await store.write_bytes(uri, b"0123456789abcdef")

    result = await store.read_range(uri, 3, 5)

    assert result == b"34567"


async def test_read_range_at_start_and_end(store, tmp_path):
    uri = str(tmp_path / "blob.bin")
    await store.write_bytes(uri, b"0123456789")

    assert await store.read_range(uri, 0, 3) == b"012"
    assert await store.read_range(uri, 7, 3) == b"789"


async def test_read_range_missing_artifact_raises_not_found(store, tmp_path):
    uri = str(tmp_path / "never-written.bin")

    with pytest.raises(ArtifactNotFoundError):
        await store.read_range(uri, 0, 4)


async def test_read_range_negative_offset_raises(store, tmp_path):
    uri = str(tmp_path / "blob.bin")
    await store.write_bytes(uri, b"0123456789")

    with pytest.raises(ArtifactReadError):
        await store.read_range(uri, -1, 4)


async def test_read_range_zero_or_negative_length_raises(store, tmp_path):
    uri = str(tmp_path / "blob.bin")
    await store.write_bytes(uri, b"0123456789")

    with pytest.raises(ArtifactReadError):
        await store.read_range(uri, 0, 0)
    with pytest.raises(ArtifactReadError):
        await store.read_range(uri, 0, -5)


async def test_read_range_past_end_of_file_raises(store, tmp_path):
    uri = str(tmp_path / "blob.bin")
    await store.write_bytes(uri, b"0123456789")

    with pytest.raises(ArtifactReadError):
        await store.read_range(uri, 8, 100)


class TestAtomicWriteBytes:
    """write_bytes is tmp-write -> fsync -> os.replace (ADR-008 A3): an
    interrupted write never leaves truncated bytes at a valid final key."""

    async def test_interrupted_write_leaves_no_object_at_final_key(
        self, store, tmp_path, monkeypatch
    ):
        import os

        from sceneops_storage.exceptions import ArtifactWriteError

        uri = str(tmp_path / "run" / "recording.mcap")

        def crash(src, dst):
            raise OSError("injected crash before rename")

        monkeypatch.setattr(os, "replace", crash)
        with pytest.raises(ArtifactWriteError):
            await store.write_bytes(uri, b"x" * 4096)
        monkeypatch.undo()

        assert not await store.exists(uri)
        # The failed attempt cleans up after itself.
        assert list((tmp_path / "run").iterdir()) == []

    async def test_interrupted_write_keeps_previous_object_intact(
        self, store, tmp_path, monkeypatch
    ):
        import os

        from sceneops_storage.exceptions import ArtifactWriteError

        uri = str(tmp_path / "blob.bin")
        await store.write_bytes(uri, b"old-complete-content")

        monkeypatch.setattr(
            os, "fsync", lambda fd: (_ for _ in ()).throw(OSError("injected"))
        )
        with pytest.raises(ArtifactWriteError):
            await store.write_bytes(uri, b"new")
        monkeypatch.undo()

        assert await store.read_bytes(uri) == b"old-complete-content"

    async def test_killed_write_leaves_only_a_temp_name(self, store, tmp_path):
        """A process killed mid-write cannot clean up; what it leaves is a
        temp file that is not at the final key, so the key reads as absent and
        a retry writes it cleanly."""
        directory = tmp_path / "run"
        directory.mkdir()
        (directory / ".recording.mcap.deadbeef.tmp").write_bytes(b"truncated")
        uri = str(directory / "recording.mcap")

        assert not await store.exists(uri)

        await store.write_bytes(uri, b"complete")
        assert await store.read_bytes(uri) == b"complete"

    async def test_write_is_complete_and_leaves_no_temp_file(self, store, tmp_path):
        uri = str(tmp_path / "run" / "recording.mcap")
        payload = bytes(range(256)) * 1024

        await store.write_bytes(uri, payload)

        assert await store.read_bytes(uri) == payload
        assert [p.name for p in (tmp_path / "run").iterdir()] == ["recording.mcap"]

    async def test_temp_files_are_not_listed_as_json_artifacts(self, store, tmp_path):
        directory = tmp_path / "run"
        await store.write_bytes(str(directory / "manifest.json"), b"{}")
        (directory / ".manifest.json.deadbeef.tmp").write_bytes(b"{")

        assert await store.list_json(str(directory)) == [
            str(directory / "manifest.json")
        ]


class TestAtomicWriteJson:
    async def test_write_json_round_trips(self, store, tmp_path):
        uri = str(tmp_path / "d" / "x.json")
        await store.write_json(uri, {"a": 1, "b": "한글"})
        assert await store.read_json(uri) == {"a": 1, "b": "한글"}
        assert [p.name for p in (tmp_path / "d").iterdir()] == ["x.json"]

    async def test_unserializable_payload_writes_nothing_and_keeps_old(
        self, store, tmp_path
    ):
        uri = str(tmp_path / "x.json")
        await store.write_json(uri, {"ok": True})
        with pytest.raises(TypeError):
            await store.write_json(uri, {"bad": object()})
        assert await store.read_json(uri) == {"ok": True}
        assert [p.name for p in tmp_path.iterdir()] == ["x.json"]

    async def test_interrupted_write_json_keeps_previous_object(
        self, store, tmp_path, monkeypatch
    ):
        import os

        from sceneops_storage.exceptions import ArtifactWriteError

        uri = str(tmp_path / "x.json")
        await store.write_json(uri, {"v": 1})
        monkeypatch.setattr(
            os, "replace", lambda *a: (_ for _ in ()).throw(OSError("injected"))
        )
        with pytest.raises(ArtifactWriteError):
            await store.write_json(uri, {"v": 2})
        monkeypatch.undo()
        assert await store.read_json(uri) == {"v": 1}


# ── list_objects ─────────────────────────────────────────────────────────────


async def test_list_objects_is_recursive_sorted_and_reports_size_and_mtime(
    store, tmp_path
):
    from datetime import UTC, datetime, timedelta

    await store.write_bytes(str(tmp_path / "root" / "b" / "two.bin"), b"22")
    await store.write_bytes(str(tmp_path / "root" / "a" / "deep" / "one.bin"), b"1")
    await store.write_bytes(str(tmp_path / "root" / "top.bin"), b"333")

    objects = await store.list_objects(str(tmp_path / "root"))

    assert [o.uri for o in objects] == [
        str(tmp_path / "root" / "a" / "deep" / "one.bin"),
        str(tmp_path / "root" / "b" / "two.bin"),
        str(tmp_path / "root" / "top.bin"),
    ]
    assert [o.size_bytes for o in objects] == [1, 2, 3]
    now = datetime.now(UTC)
    for item in objects:
        assert item.last_modified.tzinfo is not None
        assert timedelta(0) <= now - item.last_modified < timedelta(minutes=5)


async def test_list_objects_ignores_atomic_write_temp_files(store, tmp_path):
    from sceneops_storage.backends.local import _temp_name

    root = tmp_path / "root" / "run-1"
    await store.write_bytes(str(root / "recording.mcap"), b"data")
    # What a hard kill between the temp write and os.replace leaves behind.
    (root / _temp_name("robot_run_manifest.json")).write_bytes(b"{trunc")
    (root / _temp_name("recording.mcap")).write_bytes(b"partial")

    objects = await store.list_objects(str(tmp_path / "root"))

    assert [o.uri for o in objects] == [str(root / "recording.mcap")]


async def test_list_objects_temp_exclusion_does_not_hide_lookalike_objects(
    store, tmp_path
):
    root = tmp_path / "root"
    root.mkdir()
    # Not the write_bytes temp shape: ordinary objects that must be listed.
    for name in (".hidden.json", "x.tmp", ".x.tmp", ".x.notahex.tmp"):
        (root / name).write_bytes(b"1")

    objects = await store.list_objects(str(root))

    assert sorted(Path(o.uri).name for o in objects) == sorted(
        [".hidden.json", "x.tmp", ".x.tmp", ".x.notahex.tmp"]
    )


async def test_list_objects_is_a_directory_prefix_not_a_string_prefix(store, tmp_path):
    await store.write_bytes(str(tmp_path / "runs" / "a" / "x.bin"), b"1")
    await store.write_bytes(str(tmp_path / "runs" / "ab" / "y.bin"), b"1")

    objects = await store.list_objects(str(tmp_path / "runs" / "a"))

    assert [Path(o.uri).name for o in objects] == ["x.bin"]


async def test_list_objects_missing_prefix_empty_dirs_and_files_list_empty(
    store, tmp_path
):
    (tmp_path / "empty" / "nested").mkdir(parents=True)
    await store.write_bytes(str(tmp_path / "file.bin"), b"1")

    assert await store.list_objects(str(tmp_path / "never")) == []
    assert await store.list_objects(str(tmp_path / "empty")) == []
    assert await store.list_objects(str(tmp_path / "file.bin")) == []


async def test_list_objects_preserves_file_uri_style(store, tmp_path):
    await store.write_bytes(str(tmp_path / "root" / "run" / "x.bin"), b"1")

    objects = await store.list_objects(f"file://{tmp_path}/root/")

    assert [o.uri for o in objects] == [f"file://{tmp_path}/root/run/x.bin"]
    assert await store.read_bytes(objects[0].uri) == b"1"


async def test_list_objects_writes_nothing(store, tmp_path):
    await store.write_bytes(str(tmp_path / "root" / "x.bin"), b"1")
    before = sorted(p.name for p in (tmp_path / "root").iterdir())

    await store.list_objects(str(tmp_path / "root"))

    assert sorted(p.name for p in (tmp_path / "root").iterdir()) == before
