"""``write_once``: the one primitive behind every immutable artifact key.
Filesystem-only; the backends' own atomic-write behaviour is covered by the
store tests."""

from __future__ import annotations

import asyncio

import pytest

from sceneops_core.common.checksums import sha256_checksum
from sceneops_storage import LocalArtifactStore, WriteOnceConflictError, write_once


@pytest.fixture()
def store(tmp_path):
    return LocalArtifactStore(root_uri=str(tmp_path))


async def test_first_write_creates_and_reports_the_pinned_checksum(store, tmp_path):
    uri = str(tmp_path / "a.json")

    written = await write_once(store, uri, b"payload")

    assert written.created is True
    assert written.checksum == sha256_checksum(b"payload")
    assert written.size_bytes == len(b"payload")
    assert await store.read_bytes(uri) == b"payload"


async def test_same_bytes_again_is_a_no_op(store, tmp_path):
    uri = str(tmp_path / "a.json")
    await write_once(store, uri, b"payload")

    again = await write_once(store, uri, b"payload")

    assert again.created is False
    assert again.checksum == sha256_checksum(b"payload")


async def test_different_bytes_conflict_and_never_replace(store, tmp_path):
    uri = str(tmp_path / "a.json")
    await write_once(store, uri, b"first")

    with pytest.raises(WriteOnceConflictError, match="write-once"):
        await write_once(store, uri, b"second")

    assert await store.read_bytes(uri) == b"first"


async def test_the_conflict_type_is_the_callers(store, tmp_path):
    class MyConflict(WriteOnceConflictError):
        pass

    uri = str(tmp_path / "a.json")
    await write_once(store, uri, b"first")

    with pytest.raises(MyConflict):
        await write_once(store, uri, b"second", conflict=MyConflict)


async def test_concurrent_writers_of_one_content_converge(store, tmp_path):
    uri = str(tmp_path / "a.json")

    results = await asyncio.gather(
        *(write_once(store, uri, b"payload") for _ in range(8))
    )

    assert {r.checksum for r in results} == {sha256_checksum(b"payload")}
    assert await store.read_bytes(uri) == b"payload"
