#!/usr/bin/env python
"""SceneOps V2 Request 5.2: physical-layout comparison + selective-read
potential + old-vs-new access-pattern re-run.

Three things, all against the extended scale fixture
(``sceneops_analytics.testing.scale_fixture``, SceneOps V2 Request 5.2 §1):

1. Candidate physical layout comparison (§2/§7): "episode-per-file"
   (``max_episodes_per_shard=1``) vs the selected bounded-shard policy
   (``default_shard_policy()``), at every scale in the ladder -- object
   count, per-shard size distribution, episodes-per-shard.
2. Selective-read potential (§6): for one representative EpisodeRef per
   scale, locate its shard + row group (via the manifest's shard_index,
   never by listing the object store), then inspect the *real* Parquet
   row-group metadata (pyarrow) to report bytes/rows at each candidate
   granularity (row group / shard / whole export) -- a theoretical
   amplification-ratio comparison only; no range read is performed.
3. Old-vs-new access-pattern re-run (§7): builds the SAME entries (same
   scale spec, same realistic variation) under both the legacy
   single-file layout and the new sharded layout, then re-runs a subset of
   SceneOps V2 Request 5.1's workloads (open / cold single-episode /
   warm single-episode / full export) against each via
   CountingArtifactStore, to check whether physical layout alone (with the
   *reader* unchanged, per this request's "do not rewrite SceneOpsDataset
   lazy access yet" constraint) changes bytes-read/wall-time -- expected
   to show "no", by design, since Request 5.3 owns actually exploiting the
   new layout.

Profiling/reporting tool, not a test: no pass/fail assertions, no flaky
wall-clock thresholds. Not part of `make test`.

Usage:
    uv run python scripts/dev/benchmark_learning_data_layout.py
    uv run python scripts/dev/benchmark_learning_data_layout.py --scales tiny,small
    uv run python scripts/dev/benchmark_learning_data_layout.py --out /tmp/report.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

import pyarrow.parquet as pq

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "packages" / "sceneops-analytics"))
sys.path.insert(0, str(_REPO_ROOT / "packages" / "sceneops-core"))
sys.path.insert(0, str(_REPO_ROOT / "packages" / "sceneops-storage"))

from sceneops_analytics import (  # noqa: E402
    AnalyticsTableWriter,
    SceneOpsDataset,
    build_learning_episodes_table,
    build_learning_signals_table,
    build_learning_steps_table,
)
from sceneops_analytics.testing import (  # noqa: E402
    DEFAULT_SCALE_LADDER,
    CountingArtifactStore,
    ScaleSpec,
    build_scaled_entries,
    episode_ref,
    feature_projection_for,
    write_scaled_dataset_artifacts,
)
from sceneops_core.episodes.learning_export import (  # noqa: E402
    AlignedArtifactRevision,
    LearningDataExportConfig,
    LearningDataExportManifest,
    ShardPolicy,
    default_shard_policy,
    learning_data_export_id,
)
from sceneops_storage import LocalArtifactStore  # noqa: E402

POLICIES: dict[str, ShardPolicy] = {
    "episode-per-file": ShardPolicy(
        max_episodes_per_shard=1, max_rows_per_shard=10_000_000
    ),
    "bounded-default": default_shard_policy(),
}

# Old-vs-new access-pattern re-run is bounded to these scales (see module
# docstring §3) -- "large" would add ~3 more full writes (legacy + 2
# policies already covered by §1) for a question already answered at
# smaller scales.
ACCESS_PATTERN_RERUN_SCALES = ("tiny", "small", "medium")


def _uri_to_path(uri: str) -> Path:
    parsed = urlparse(uri)
    return Path(parsed.path) if parsed.scheme == "file" else Path(uri)


def _size_stats(sizes: list[int]) -> dict:
    if not sizes:
        return {"count": 0, "min": 0, "max": 0, "mean": 0, "total": 0}
    return {
        "count": len(sizes),
        "min": min(sizes),
        "max": max(sizes),
        "mean": round(statistics.mean(sizes), 1),
        "total": sum(sizes),
    }


async def _layout_stats_for(
    tmp_path: Path, spec: ScaleSpec, policy: ShardPolicy
) -> dict:
    artifacts = await write_scaled_dataset_artifacts(
        tmp_path, spec, shard_policy=policy
    )
    shard_index = artifacts.learning_manifest.shard_index
    stats: dict = {}
    for table_name in ("learning_steps", "learning_signals"):
        shards = getattr(shard_index, table_name)
        episodes_per_shard = [len(shard.episodes) for shard in shards]
        stats[table_name] = {
            "num_objects": len(shards),
            "size_bytes": _size_stats([shard.size_bytes for shard in shards]),
            "row_count": _size_stats([shard.row_count for shard in shards]),
            "episodes_per_shard": _size_stats(episodes_per_shard),
        }
    stats["total_objects"] = 1 + sum(  # +1 for learning_episodes
        stats[t]["num_objects"] for t in ("learning_steps", "learning_signals")
    )
    return stats, artifacts


async def compare_layouts(scale_names: list[str]) -> dict:
    ladder_by_name = {spec.name: spec for spec in DEFAULT_SCALE_LADDER}
    report: dict = {}
    with tempfile.TemporaryDirectory(prefix="sceneops-layout-") as tmp_root_str:
        tmp_root = Path(tmp_root_str)
        for scale_name in scale_names:
            spec = ladder_by_name[scale_name]
            report[scale_name] = {"num_episodes": spec.num_episodes, "policies": {}}
            for policy_name, policy in POLICIES.items():
                tmp_path = tmp_root / scale_name / policy_name
                tmp_path.mkdir(parents=True, exist_ok=True)
                print(
                    f"--- layout scale={scale_name} policy={policy_name} ---",
                    file=sys.stderr,
                )
                stats, artifacts = await _layout_stats_for(tmp_path, spec, policy)
                report[scale_name]["policies"][policy_name] = stats
                if policy_name == "bounded-default":
                    report[scale_name][
                        "selective_read_potential"
                    ] = await selective_read_potential(spec, artifacts)
    return report


async def selective_read_potential(spec: ScaleSpec, artifacts) -> dict:
    """SceneOps V2 Request 5.2 §6: for the middle EpisodeRef (by
    generation order), locate its shard + row group via the manifest
    alone (no object-store listing), then read the *real* Parquet
    row-group metadata to report bytes at three candidate granularities.
    No range read happens here -- this only proves what a range-aware
    reader (Request 5.3) could theoretically narrow access to.
    """
    shard_index = artifacts.learning_manifest.shard_index
    target_ref = episode_ref(spec, spec.num_episodes // 2)

    result: dict = {"episode_ref": target_ref.episode_id}
    total_export_bytes = sum(
        shard.size_bytes
        for table_name in ("learning_steps", "learning_signals")
        for shard in getattr(shard_index, table_name)
    )
    result["total_export_bytes"] = total_export_bytes

    for table_name in ("learning_steps", "learning_signals"):
        shards = getattr(shard_index, table_name)
        found = None
        for shard in shards:
            for member in shard.episodes:
                if member.episode_ref == target_ref:
                    found = (shard, member)
                    break
            if found:
                break
        if found is None:
            result[table_name] = {"error": "episode ref not found in shard_index"}
            continue
        shard, member = found
        path = _uri_to_path(shard.uri)
        parquet_file = pq.ParquetFile(path)
        row_group_meta = parquet_file.metadata.row_group(member.row_group_index)
        row_group_bytes = row_group_meta.total_byte_size
        row_group_rows = row_group_meta.num_rows

        result[table_name] = {
            "shard_uri": str(path.name),
            "shard_bytes": shard.size_bytes,
            "shard_row_count": shard.row_count,
            "row_group_index": member.row_group_index,
            "row_group_bytes": row_group_bytes,
            "row_group_rows": row_group_rows,
            "amplification_vs_whole_export": (
                round(total_export_bytes / row_group_bytes, 1)
                if row_group_bytes
                else None
            ),
            "amplification_vs_shard": (
                round(shard.size_bytes / row_group_bytes, 1)
                if row_group_bytes
                else None
            ),
        }
    return result


# ---------------------------------------------------------------------
# Old (single-file) vs new (sharded) access-pattern re-run
# ---------------------------------------------------------------------


async def _write_legacy_single_file(tmp_path: Path, spec: ScaleSpec):
    dataset_id = f"bench-legacy-{spec.name}"
    dataset_version = "v1"
    storage_root_uri = str(tmp_path / "storage")
    artifact_store = LocalArtifactStore(root_uri=storage_root_uri)
    entries = build_scaled_entries(spec)

    export_config = LearningDataExportConfig()
    export_id = learning_data_export_id(
        aligned_checksums=[checksum for checksum, _ in entries],
        export_config=export_config,
    )
    writer = AnalyticsTableWriter(
        artifact_store=artifact_store, root_uri=str(tmp_path / "analytics")
    )
    builders = {
        "learning_episodes": build_learning_episodes_table,
        "learning_steps": build_learning_steps_table,
        "learning_signals": build_learning_signals_table,
    }
    table_uris, table_checksums, row_counts = {}, {}, {}
    for name, builder in builders.items():
        df = builder(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            export_id=export_id,
            entries=entries,
        )
        result = await writer.write_learning_table(
            name,
            df,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            export_id=export_id,
        )
        table_uris[name] = result.uri
        table_checksums[name] = result.checksum
        row_counts[name] = df.height

    manifest = LearningDataExportManifest(
        export_id=export_id,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        inputs=[
            AlignedArtifactRevision(
                episode_id=artifact.aligned_episode.episode_id,
                aligned_artifact_id=f"art-{checksum}",
                aligned_artifact_checksum=checksum,
            )
            for checksum, artifact in entries
        ],
        export_config=export_config,
        table_uris=table_uris,
        table_checksums=table_checksums,
        row_counts=row_counts,
        episode_count=spec.num_episodes,
    )
    write_result = await writer.write_learning_export_manifest(
        manifest,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        export_id=export_id,
    )
    return manifest, write_result.checksum, storage_root_uri


async def _measure_workloads(
    manifest, checksum, storage_root_uri, spec: ScaleSpec
) -> dict:
    store = CountingArtifactStore(LocalArtifactStore(root_uri=storage_root_uri))
    results: dict = {}

    t0 = time.perf_counter()
    dataset = await SceneOpsDataset.open(
        learning_manifest=manifest,
        learning_manifest_checksum=checksum,
        artifact_store=store,
    )
    results["A_open"] = {
        "wall_seconds": round(time.perf_counter() - t0, 6),
        "read_bytes_total": store.stats.read_bytes_total,
        "distinct_uris_read": store.stats.distinct_uris_read,
    }

    refs = dataset.episodes()
    ref_c = refs[0]
    ref_d = refs[min(1, len(refs) - 1)]
    projection = feature_projection_for(spec)

    io_before = store.stats.read_bytes_total
    t0 = time.perf_counter()
    await dataset.get_window(
        ref_c, 0, dataset.get_episode(ref_c).step_count, projection
    )
    results["C_cold_one_episode"] = {
        "wall_seconds": round(time.perf_counter() - t0, 6),
        "read_bytes_delta": store.stats.read_bytes_total - io_before,
        "distinct_uris_read": store.stats.distinct_uris_read,
    }

    io_before = store.stats.read_bytes_total
    t0 = time.perf_counter()
    await dataset.get_window(
        ref_d, 0, dataset.get_episode(ref_d).step_count, projection
    )
    results["D_warm_second_episode"] = {
        "wall_seconds": round(time.perf_counter() - t0, 6),
        "read_bytes_delta": store.stats.read_bytes_total - io_before,
    }

    export_store = CountingArtifactStore(LocalArtifactStore(root_uri=storage_root_uri))
    t0 = time.perf_counter()
    export_dataset = await SceneOpsDataset.open(
        learning_manifest=manifest,
        learning_manifest_checksum=checksum,
        artifact_store=export_store,
    )
    for ref in export_dataset.episodes():
        metadata = export_dataset.get_episode(ref)
        if metadata.step_count:
            await export_dataset.get_window(ref, 0, metadata.step_count, projection)
    results["H_cold_full_export"] = {
        "wall_seconds": round(time.perf_counter() - t0, 6),
        "read_bytes_total": export_store.stats.read_bytes_total,
        "distinct_uris_read": export_store.stats.distinct_uris_read,
    }
    return results


async def rerun_access_patterns(scale_names: list[str]) -> dict:
    ladder_by_name = {spec.name: spec for spec in DEFAULT_SCALE_LADDER}
    report: dict = {}
    with tempfile.TemporaryDirectory(prefix="sceneops-rerun-") as tmp_root_str:
        tmp_root = Path(tmp_root_str)
        for scale_name in scale_names:
            spec = ladder_by_name[scale_name]
            print(f"--- access-pattern re-run scale={scale_name} ---", file=sys.stderr)

            legacy_path = tmp_root / scale_name / "legacy"
            legacy_path.mkdir(parents=True, exist_ok=True)
            (
                legacy_manifest,
                legacy_checksum,
                legacy_root,
            ) = await _write_legacy_single_file(legacy_path, spec)
            legacy_results = await _measure_workloads(
                legacy_manifest, legacy_checksum, legacy_root, spec
            )

            sharded_path = tmp_root / scale_name / "sharded"
            sharded_path.mkdir(parents=True, exist_ok=True)
            artifacts = await write_scaled_dataset_artifacts(sharded_path, spec)
            sharded_results = await _measure_workloads(
                artifacts.learning_manifest,
                artifacts.learning_manifest_checksum,
                artifacts.storage_root_uri,
                spec,
            )

            report[scale_name] = {
                "legacy_single_file": legacy_results,
                "sharded": sharded_results,
            }
    return report


def _print_summary(layout_report: dict, rerun_report: dict) -> None:
    print("\n================ LAYOUT COMPARISON ================")
    for scale_name, scale_data in layout_report.items():
        print(f"\n--- {scale_name} (num_episodes={scale_data['num_episodes']}) ---")
        for policy_name, stats in scale_data["policies"].items():
            print(f"  policy={policy_name} total_objects={stats['total_objects']}")
            for table_name in ("learning_steps", "learning_signals"):
                t = stats[table_name]
                print(
                    f"    {table_name}: objects={t['num_objects']} "
                    f"size_bytes(min/mean/max)={t['size_bytes']['min']}/"
                    f"{t['size_bytes']['mean']}/{t['size_bytes']['max']} "
                    f"episodes_per_shard(mean/max)={t['episodes_per_shard']['mean']}/"
                    f"{t['episodes_per_shard']['max']}"
                )
        if "selective_read_potential" in scale_data:
            srp = scale_data["selective_read_potential"]
            print(f"  selective-read-potential for {srp['episode_ref']}:")
            print(f"    total_export_bytes={srp['total_export_bytes']}")
            for table_name in ("learning_steps", "learning_signals"):
                t = srp[table_name]
                print(
                    f"    {table_name}: row_group_bytes={t['row_group_bytes']} "
                    f"shard_bytes={t['shard_bytes']} "
                    f"amp_vs_export={t['amplification_vs_whole_export']}x "
                    f"amp_vs_shard={t['amplification_vs_shard']}x"
                )

    print("\n================ OLD vs NEW ACCESS-PATTERN RE-RUN ================")
    for scale_name, data in rerun_report.items():
        print(f"\n--- {scale_name} ---")
        for layout_name in ("legacy_single_file", "sharded"):
            r = data[layout_name]
            print(f"  {layout_name}:")
            for workload in (
                "A_open",
                "C_cold_one_episode",
                "D_warm_second_episode",
                "H_cold_full_export",
            ):
                w = r[workload]
                print(f"    {workload}: {w}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scales",
        default=",".join(spec.name for spec in DEFAULT_SCALE_LADDER),
        help="comma-separated scale names for the layout comparison (default: all)",
    )
    parser.add_argument(
        "--rerun-scales",
        default=",".join(ACCESS_PATTERN_RERUN_SCALES),
        help="comma-separated scale names for the old-vs-new access-pattern re-run",
    )
    parser.add_argument(
        "--out", default=None, help="write full JSON report to this path"
    )
    args = parser.parse_args()

    scale_names = [s.strip() for s in args.scales.split(",") if s.strip()]
    rerun_scale_names = [s.strip() for s in args.rerun_scales.split(",") if s.strip()]

    layout_report = asyncio.run(compare_layouts(scale_names))
    rerun_report = asyncio.run(rerun_access_patterns(rerun_scale_names))
    _print_summary(layout_report, rerun_report)

    if args.out:
        Path(args.out).write_text(
            json.dumps(
                {
                    "layout_comparison": layout_report,
                    "access_pattern_rerun": rerun_report,
                },
                indent=2,
            )
        )
        print(f"\nfull report written to {args.out}")


if __name__ == "__main__":
    main()
