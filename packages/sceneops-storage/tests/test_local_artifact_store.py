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
