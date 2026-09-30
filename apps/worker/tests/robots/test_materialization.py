"""Unit tests for sceneops_worker.robots.materialization -- no real
ArtifactStore. Uses a fake in-memory ArtifactStore implementing just
read_bytes, the only method materialize_recording ever calls.

Real-MinIO concurrency + canonical-object-unchanged coverage lives in
apps/worker/tests/robots/test_materialization_integration.py.
"""

from __future__ import annotations

import asyncio
import hashlib

import pytest

from sceneops_worker.robots.materialization import (
    MaterializationChecksumError,
    is_local_uri,
    materialize_recording,
)


class _FakeArtifactStore:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects
        self.read_calls: list[str] = []

    async def read_bytes(self, uri: str) -> bytes:
        self.read_calls.append(uri)
        return self.objects[uri]


def _sha256(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


# ---------------------------------------------------------------------
# is_local_uri
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "uri,expected",
    [
        ("/data/raw/rosbag/scene-0061/scene-0061_0.mcap", True),
        ("file:///data/raw/rosbag/scene-0061/scene-0061_0.mcap", True),
        ("s3://sceneops/artifacts/robot_runs/run-1/run-1.mcap", False),
        ("s3://sceneops-minio/robot_runs/run-1/run-1.mcap", False),
    ],
)
def test_is_local_uri(uri: str, expected: bool) -> None:
    assert is_local_uri(uri) is expected


# ---------------------------------------------------------------------
# materialize_recording
# ---------------------------------------------------------------------


async def test_materialize_yields_readable_local_copy_with_matching_bytes() -> None:
    data = b"\x00\x01mcap-bytes" * 100
    store = _FakeArtifactStore({"s3://bucket/run-1/run-1.mcap": data})

    async with materialize_recording(
        artifact_store=store, uri="s3://bucket/run-1/run-1.mcap"
    ) as local_path:
        assert local_path.exists()
        assert local_path.read_bytes() == data
        materialized_path = local_path

    assert not materialized_path.exists()
    assert not materialized_path.parent.exists()


async def test_materialize_local_filename_derived_from_uri() -> None:
    store = _FakeArtifactStore({"s3://bucket/robot_runs/run-1/run-1.mcap": b"x"})

    async with materialize_recording(
        artifact_store=store, uri="s3://bucket/robot_runs/run-1/run-1.mcap"
    ) as local_path:
        assert local_path.name == "run-1.mcap"


async def test_materialize_verifies_matching_checksum() -> None:
    data = b"real-bytes"
    store = _FakeArtifactStore({"s3://bucket/run-1.mcap": data})

    async with materialize_recording(
        artifact_store=store,
        uri="s3://bucket/run-1.mcap",
        expected_checksum=_sha256(data),
    ) as local_path:
        assert local_path.read_bytes() == data


async def test_materialize_raises_and_cleans_up_on_checksum_mismatch() -> None:
    store = _FakeArtifactStore({"s3://bucket/run-1.mcap": b"actual-bytes"})

    captured_path = None
    with pytest.raises(MaterializationChecksumError):
        async with materialize_recording(
            artifact_store=store,
            uri="s3://bucket/run-1.mcap",
            expected_checksum=_sha256(b"different-bytes"),
        ) as local_path:
            captured_path = local_path

    assert captured_path is None  # never reached the yield
    # Nothing left behind under the OS temp root for this call.


async def test_materialize_skips_verification_when_no_expected_checksum() -> None:
    store = _FakeArtifactStore({"s3://bucket/run-1.mcap": b"whatever bytes"})

    async with materialize_recording(
        artifact_store=store, uri="s3://bucket/run-1.mcap", expected_checksum=None
    ) as local_path:
        assert local_path.read_bytes() == b"whatever bytes"


# ---------------------------------------------------------------------
# Cleanup on success vs. exception raised by the CALLER's own block
# ---------------------------------------------------------------------


async def test_cleanup_runs_on_successful_caller_block() -> None:
    store = _FakeArtifactStore({"s3://bucket/run-1.mcap": b"data"})

    async with materialize_recording(
        artifact_store=store, uri="s3://bucket/run-1.mcap"
    ) as local_path:
        parent = local_path.parent
        assert parent.exists()

    assert not parent.exists()


async def test_cleanup_runs_when_caller_block_raises() -> None:
    store = _FakeArtifactStore({"s3://bucket/run-1.mcap": b"data"})

    parent_holder: list = []

    class _SimulatedParseError(Exception):
        pass

    with pytest.raises(_SimulatedParseError):
        async with materialize_recording(
            artifact_store=store, uri="s3://bucket/run-1.mcap"
        ) as local_path:
            parent_holder.append(local_path.parent)
            assert local_path.parent.exists()
            raise _SimulatedParseError("RosbagAdapter blew up mid-parse")

    assert not parent_holder[0].exists()


# ---------------------------------------------------------------------
# Concurrency (fakes -- real-MinIO concurrency lives in the integration test)
# ---------------------------------------------------------------------


async def test_concurrent_materializations_of_same_uri_get_independent_paths() -> None:
    data = b"shared canonical bytes"
    store = _FakeArtifactStore({"s3://bucket/run-1.mcap": data})

    paths: list = []

    async def _one() -> None:
        async with materialize_recording(
            artifact_store=store, uri="s3://bucket/run-1.mcap"
        ) as local_path:
            paths.append(local_path)
            await asyncio.sleep(0.01)  # force interleaving
            assert local_path.read_bytes() == data

    await asyncio.gather(_one(), _one())

    assert len(paths) == 2
    assert paths[0] != paths[1]
    assert paths[0].parent != paths[1].parent
    # Both cleaned up independently, neither cross-deleted the other early.
    assert not paths[0].exists()
    assert not paths[1].exists()
