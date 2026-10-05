"""Unit coverage for LocalArtifactStore.read_range (SceneOps V2 Request
5.3) -- filesystem-only, no real infra required despite living alongside
this package's MinIO integration tests.
"""

from __future__ import annotations

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
