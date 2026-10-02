"""Real-MinIO integration coverage for
sceneops_worker.robots.materialization -- concurrent materialization of
the SAME canonical object (as two independent BuildEpisodes job
executions against the same RobotRun would do) and confirmation that the
canonical ArtifactStore object itself is never mutated by any of it.

Requires a reachable MinIO (`make test-integration` against a running
`make local-up` stack). Skips (not fails) otherwise.
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import pytest

from sceneops_worker.robots.materialization import materialize_recording

_FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "rosbag"
_VALID_MCAP = _FIXTURES_DIR / "can_replay_scene_0061.mcap"


def _sha256(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


@pytest.mark.usefixtures("cleanup_minio_prefix", "_minio_reachable")
async def test_concurrent_materializations_of_same_object_do_not_collide(
    worker_context,
) -> None:
    data = _VALID_MCAP.read_bytes()
    uri = worker_context.artifact_store.join_uri(
        worker_context.settings.artifact_root_uri, "run-materialize-1", "recording.mcap"
    )
    await worker_context.artifact_store.write_bytes(uri, data)
    original_checksum = _sha256(data)
    barrier = asyncio.Barrier(2)

    async def _attempt() -> Path:
        async with materialize_recording(
            artifact_store=worker_context.artifact_store, uri=uri
        ) as local_path:
            await barrier.wait()  # force real overlap between the two attempts
            assert local_path.read_bytes() == data
            return local_path

    path_a, path_b = await asyncio.gather(_attempt(), _attempt())

    assert path_a != path_b
    assert path_a.parent != path_b.parent
    # Cleaned up independently -- neither attempt's teardown removed the
    # other's still-in-use file.
    assert not path_a.exists()
    assert not path_b.exists()

    # The canonical object itself was only ever read, never written or
    # deleted by materialization -- still present with the same checksum.
    still_stored = await worker_context.artifact_store.read_bytes(uri)
    assert _sha256(still_stored) == original_checksum


@pytest.mark.usefixtures("cleanup_minio_prefix", "_minio_reachable")
async def test_materialization_failure_does_not_touch_canonical_object(
    worker_context,
) -> None:
    from sceneops_worker.robots.materialization import MaterializationChecksumError

    data = _VALID_MCAP.read_bytes()
    uri = worker_context.artifact_store.join_uri(
        worker_context.settings.artifact_root_uri, "run-materialize-2", "recording.mcap"
    )
    await worker_context.artifact_store.write_bytes(uri, data)

    with pytest.raises(MaterializationChecksumError):
        async with materialize_recording(
            artifact_store=worker_context.artifact_store,
            uri=uri,
            expected_checksum="sha256:" + "0" * 64,
        ):
            pytest.fail("should never reach the yielded block")

    unchanged = await worker_context.artifact_store.read_bytes(uri)
    assert _sha256(unchanged) == _sha256(data)
