#!/usr/bin/env python
"""SceneOps V2 Request 5.4 §5: real-MinIO access-strategy benchmark.

Measures, against a real MinIO instance (`make local-up`), the scenarios
the request asks for:

    A. cold selective EpisodeRef (first-ever access to that episode)
    B. warm selective EpisodeRef (same episode, second access)
    C. N episodes in the SAME shard, via N individual selective reads
    D. the SAME N episodes, via one shard-aware bulk read
       (SceneOpsDataset.preload_episodes)
    E. N episodes spread across DIFFERENT shards, via individual reads

Reports wall time, request/range-read count, bytes read, and cache
hit/miss counters for each. Profiling/reporting tool, not a test: no
pass/fail assertions, no flaky wall-clock thresholds -- run manually, not
part of `make test`/`make test-integration`.

Usage:
    uv run python tools/benchmarks/learning_data/benchmark_minio_access_strategy.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import uuid
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "packages" / "sceneops-analytics"))
sys.path.insert(0, str(_REPO_ROOT / "packages" / "sceneops-core"))
sys.path.insert(0, str(_REPO_ROOT / "packages" / "sceneops-storage"))

from sceneops_analytics import SceneOpsDataset  # noqa: E402
from sceneops_analytics.testing import (  # noqa: E402
    CountingArtifactStore,
    ScaleSpec,
    feature_projection_for,
    write_scaled_dataset_artifacts,
)
from sceneops_core.artifacts.schemas.enums import ArtifactBackend  # noqa: E402
from sceneops_core.config import StorageSettings  # noqa: E402
from sceneops_core.episodes.learning_export import ShardPolicy  # noqa: E402
from sceneops_storage.backends.s3 import S3ArtifactStore  # noqa: E402

BUCKET = os.environ.get("MINIO_BUCKET", "sceneops")
SPEC = ScaleSpec(name="minio-access-strategy", num_episodes=60, steps_per_episode=15)
TIGHT_POLICY = ShardPolicy(max_episodes_per_shard=10, max_rows_per_shard=10_000)


class _PrefixedRoot:
    def __init__(self, root_uri: str) -> None:
        self._root_uri = root_uri.rstrip("/")

    def __truediv__(self, part: str) -> "_PrefixedRoot":
        return _PrefixedRoot(f"{self._root_uri}/{part}")

    def __str__(self) -> str:
        return self._root_uri


def _snapshot(store: CountingArtifactStore) -> dict:
    return {
        "bytes": store.stats.total_bytes_read,
        "range_calls": store.stats.read_range_calls,
    }


def _delta(before: dict, after: dict) -> dict:
    return {k: after[k] - before[k] for k in before}


async def main() -> None:
    base_store = S3ArtifactStore(
        settings=StorageSettings(
            backend=ArtifactBackend.MINIO,
            root_uri=f"s3://{BUCKET}",
            endpoint_url=os.environ.get("MINIO_ENDPOINT_URL", "http://localhost:9000"),
            region=None,
            access_key_id=os.environ.get("MINIO_ROOT_USER", "minioadmin"),
            secret_access_key=os.environ.get("MINIO_ROOT_PASSWORD", "minioadmin"),
        )
    )
    prefix = f"_bench/access-strategy/{uuid.uuid4().hex[:12]}"
    root_uri = f"s3://{BUCKET}/{prefix}"
    try:
        await base_store.exists(f"{root_uri}/_connectivity_check")
    except Exception as exc:  # noqa: BLE001
        print(f"MinIO not reachable: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        write_store = CountingArtifactStore(base_store)
        artifacts = await write_scaled_dataset_artifacts(
            _PrefixedRoot(root_uri),
            SPEC,
            artifact_store=write_store,
            storage_root_uri=f"{root_uri}/storage",
            shard_policy=TIGHT_POLICY,
        )
        shard_index = artifacts.learning_manifest.shard_index
        num_shards = len(shard_index.learning_steps)
        print(
            f"built {SPEC.num_episodes} episodes across {num_shards} shards",
            file=sys.stderr,
        )

        projection = feature_projection_for(SPEC)
        refs_by_shard: list[list] = [
            [m.episode_ref for m in shard.episodes]
            for shard in shard_index.learning_steps
        ]

        results: dict = {}

        # ---- A/B: cold then warm single EpisodeRef ----
        store = CountingArtifactStore(base_store)
        dataset = await SceneOpsDataset.open(
            learning_manifest=artifacts.learning_manifest,
            learning_manifest_checksum=artifacts.learning_manifest_checksum,
            artifact_store=store,
        )
        ref = refs_by_shard[0][0]
        step_count = dataset.get_episode(ref).step_count

        before = _snapshot(store)
        t0 = time.perf_counter()
        await dataset.get_window(ref, 0, step_count, projection)
        results["A_cold_selective"] = {
            "wall_seconds": round(time.perf_counter() - t0, 4),
            **_delta(before, _snapshot(store)),
        }

        before = _snapshot(store)
        t0 = time.perf_counter()
        await dataset.get_window(ref, 0, step_count, projection)
        results["B_warm_selective"] = {
            "wall_seconds": round(time.perf_counter() - t0, 4),
            **_delta(before, _snapshot(store)),
        }

        # ---- C: N episodes in the SAME shard, individual reads ----
        store_c = CountingArtifactStore(base_store)
        dataset_c = await SceneOpsDataset.open(
            learning_manifest=artifacts.learning_manifest,
            learning_manifest_checksum=artifacts.learning_manifest_checksum,
            artifact_store=store_c,
        )
        same_shard_refs = refs_by_shard[1]
        before = _snapshot(store_c)
        t0 = time.perf_counter()
        for r in same_shard_refs:
            await dataset_c.get_window(
                r, 0, dataset_c.get_episode(r).step_count, projection
            )
        results["C_same_shard_individual_reads"] = {
            "episode_count": len(same_shard_refs),
            "wall_seconds": round(time.perf_counter() - t0, 4),
            **_delta(before, _snapshot(store_c)),
        }

        # ---- D: same N episodes, via shard-aware bulk preload ----
        store_d = CountingArtifactStore(base_store)
        dataset_d = await SceneOpsDataset.open(
            learning_manifest=artifacts.learning_manifest,
            learning_manifest_checksum=artifacts.learning_manifest_checksum,
            artifact_store=store_d,
        )
        before = _snapshot(store_d)
        t0 = time.perf_counter()
        await dataset_d.preload_episodes(same_shard_refs)
        for r in same_shard_refs:
            await dataset_d.get_window(
                r, 0, dataset_d.get_episode(r).step_count, projection
            )
        results["D_same_shard_bulk_access"] = {
            "episode_count": len(same_shard_refs),
            "wall_seconds": round(time.perf_counter() - t0, 4),
            **_delta(before, _snapshot(store_d)),
            "cache_hits": dataset_d._episode_steps_cache.stats.hits,
            "cache_misses": dataset_d._episode_steps_cache.stats.misses,
        }

        # ---- E: N episodes spread across DIFFERENT shards, individual reads ----
        store_e = CountingArtifactStore(base_store)
        dataset_e = await SceneOpsDataset.open(
            learning_manifest=artifacts.learning_manifest,
            learning_manifest_checksum=artifacts.learning_manifest_checksum,
            artifact_store=store_e,
        )
        spread_refs = [shard_refs[0] for shard_refs in refs_by_shard]
        before = _snapshot(store_e)
        t0 = time.perf_counter()
        for r in spread_refs:
            await dataset_e.get_window(
                r, 0, dataset_e.get_episode(r).step_count, projection
            )
        results["E_cross_shard_individual_reads"] = {
            "episode_count": len(spread_refs),
            "shards_touched": num_shards,
            "wall_seconds": round(time.perf_counter() - t0, 4),
            **_delta(before, _snapshot(store_e)),
        }

        print("\n=== MinIO access-strategy benchmark ===")
        for key, value in results.items():
            print(f"{key}: {value}")

    finally:
        await base_store.delete_prefix(root_uri)


if __name__ == "__main__":
    asyncio.run(main())
