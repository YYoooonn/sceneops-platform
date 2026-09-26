"""Real MinIO/S3-compatible coverage for SceneOps V2 Request 5.3's
selective-read path -- confirms the Parquet-range-read mechanism
(``ArtifactStore.read_range`` -> lazy PyArrow file-like -> row-group
fetch) works against a real S3-compatible backend, not only
``LocalArtifactStore``. Complements ``packages/sceneops-storage/tests/
test_s3_artifact_store.py``'s raw ``read_range`` byte-slice coverage with
the actual selective-Parquet-read use case this primitive exists for.

Requires a reachable MinIO instance (`make local-up`); skips (not fails)
if unreachable, matching sceneops-storage's own integration-test
convention. Lives here (not packages/sceneops-analytics/tests/, which
`make test`'s "no infra needed" tier scans by default) for the same
reason scripts/e2e/tests/test_e2e_fixture_bootstrap_integration.py does --
real-infra tests stay out of the fast tier by directory placement alone.
"""

from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio

from sceneops_analytics import SceneOpsDataset
from sceneops_analytics.testing import (
    CountingArtifactStore,
    ScaleSpec,
    feature_projection_for,
    write_scaled_dataset_artifacts,
)
from sceneops_core.artifacts.schemas.enums import ArtifactBackend
from sceneops_core.config import StorageSettings
from sceneops_core.episodes.learning_export import ShardPolicy
from sceneops_storage.backends.s3 import S3ArtifactStore

BUCKET = os.environ.get("MINIO_BUCKET", "sceneops")
_TEST_PREFIX = "_test-integration/selective-reads"

# A tight shard policy so a single-episode fetch genuinely leaves other
# shards untouched over MinIO -- the default policy (200 episodes/shard)
# would put all of these tiny fixture's episodes in one shard, where no
# cross-shard selectivity is even possible to demonstrate.
MULTI_SHARD_SPEC = ScaleSpec(
    name="minio-selective-test", num_episodes=6, steps_per_episode=8
)
TIGHT_POLICY = ShardPolicy(max_episodes_per_shard=2, max_rows_per_shard=10_000)


@pytest_asyncio.fixture()
async def minio_store():
    settings = StorageSettings(
        backend=ArtifactBackend.MINIO,
        root_uri=f"s3://{BUCKET}",
        endpoint_url=os.environ.get("MINIO_ENDPOINT_URL", "http://localhost:9000"),
        region=None,
        access_key_id=os.environ.get("MINIO_ROOT_USER", "minioadmin"),
        secret_access_key=os.environ.get("MINIO_ROOT_PASSWORD", "minioadmin"),
    )
    artifact_store = S3ArtifactStore(settings=settings)
    prefix = f"{_TEST_PREFIX}/{uuid.uuid4().hex[:12]}"
    try:
        await artifact_store.exists(f"s3://{BUCKET}/{prefix}/_connectivity_check")
    except Exception as exc:  # noqa: BLE001 - report as a skip, not a failure
        pytest.skip(f"MinIO not reachable: {exc}")

    yield artifact_store, f"s3://{BUCKET}/{prefix}"

    await artifact_store.delete_prefix(f"s3://{BUCKET}/{prefix}")


class _PrefixedRoot:
    """Adapter so ``write_scaled_dataset_artifacts`` (which composes its
    ``AnalyticsTableWriter`` root via ``tmp_path / "analytics"``) can
    target an ``s3://`` root instead of a filesystem path."""

    def __init__(self, root_uri: str) -> None:
        self._root_uri = root_uri.rstrip("/")

    def __truediv__(self, part: str) -> "_PrefixedRoot":
        return _PrefixedRoot(f"{self._root_uri}/{part}")

    def __str__(self) -> str:
        return self._root_uri


@pytest.mark.asyncio
async def test_v2_selective_window_access_works_against_real_minio(minio_store):
    artifact_store, root_uri = minio_store
    storage_root_uri = f"{root_uri}/storage"

    counted_store = CountingArtifactStore(artifact_store)
    artifacts = await write_scaled_dataset_artifacts(
        _PrefixedRoot(root_uri),
        MULTI_SHARD_SPEC,
        artifact_store=counted_store,
        storage_root_uri=storage_root_uri,
        shard_policy=TIGHT_POLICY,
    )
    assert artifacts.storage_root_uri.startswith("s3://")
    shard_index = artifacts.learning_manifest.shard_index
    assert len(shard_index.learning_steps) >= 3  # 6 episodes / 2 per shard

    # Fresh CountingArtifactStore for the read side -- the write above
    # already touched read_range's write path (write_bytes), and we only
    # want to measure the *read* selectivity below.
    counted_store = CountingArtifactStore(artifact_store)
    dataset = await SceneOpsDataset.open(
        learning_manifest=artifacts.learning_manifest,
        learning_manifest_checksum=artifacts.learning_manifest_checksum,
        artifact_store=counted_store,
    )
    assert dataset._is_sharded is True

    ref = dataset.episodes()[2]
    projection = feature_projection_for(MULTI_SHARD_SPEC)
    step_count = dataset.get_episode(ref).step_count

    window = await dataset.get_window(ref, 0, step_count, projection)
    assert len(window.observation) == step_count

    touched_uris = set(counted_store.stats.per_uri_range_calls)
    all_shard_uris = {
        shard.uri
        for table_name in ("learning_steps", "learning_signals")
        for shard in getattr(shard_index, table_name)
    }
    other_shard_uris = all_shard_uris - {
        shard.uri
        for table_name in ("learning_steps", "learning_signals")
        for shard in getattr(shard_index, table_name)
        for member in shard.episodes
        if member.episode_ref == ref
    }
    assert counted_store.stats.read_range_calls > 0
    # Real proof of selectivity over MinIO: shard files belonging to OTHER
    # episodes were never touched at all.
    assert touched_uris.isdisjoint(other_shard_uris)
    assert len(other_shard_uris) > 0  # sanity: there really were other shards to avoid
