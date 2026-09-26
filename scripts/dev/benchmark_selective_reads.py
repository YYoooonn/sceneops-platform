#!/usr/bin/env python
"""SceneOps V2 Request 5.3: realized I/O improvement from selective
EpisodeRef/window reads over the Request 5.2 sharded layout.

Extends the Request 5.1/5.2 benchmark instrumentation
(``CountingArtifactStore``) to measure, at each scale in the standard
ladder, under the *production* shard policy (``default_shard_policy()``):

    A. cold open (dataset.open() cost -- metadata only, unchanged from 5.1)
    B. cold single-episode access (first touch of any episode)
    C. warm second episode in the SAME shard as B (metadata reuse)
    D. cold episode in ANOTHER shard (fresh footer fetch)
    E. fixed-size window vs full-episode window on a fresh episode (both
       should fetch identical bytes -- one row group per episode is the
       floor, Request 5.3 §4's documented limitation)
    F. narrow vs wide FeatureProjection on two fresh, comparable episodes
       (should also fetch identical bytes -- learning_signals' tall/long
       schema still has no column to skip, Request 5.1 §9/finding
       reconfirmed after row-group selectivity)

Every measurement reports bytes/objects actually fetched, compared
against the "old baseline" (100% of that scale's total steps+signals
bytes -- Request 5.1's measured single-episode-reads-everything finding).

Profiling/reporting tool, not a test: no pass/fail assertions, no flaky
wall-clock thresholds. Not part of `make test`.

Usage:
    uv run python scripts/dev/benchmark_selective_reads.py
    uv run python scripts/dev/benchmark_selective_reads.py --scales tiny,small
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "packages" / "sceneops-analytics"))
sys.path.insert(0, str(_REPO_ROOT / "packages" / "sceneops-core"))
sys.path.insert(0, str(_REPO_ROOT / "packages" / "sceneops-storage"))

from sceneops_analytics import SceneOpsDataset  # noqa: E402
from sceneops_analytics.testing import (  # noqa: E402
    DEFAULT_SCALE_LADDER,
    CountingArtifactStore,
    ScaleSpec,
    feature_projection_for,
    write_scaled_dataset_artifacts,
)
from sceneops_core.episodes.learning import FeatureProjection  # noqa: E402
from sceneops_storage import LocalArtifactStore  # noqa: E402


def _total_export_bytes(shard_index) -> int:
    return sum(
        shard.size_bytes
        for table_name in ("learning_steps", "learning_signals")
        for shard in getattr(shard_index, table_name)
    )


def _shard_for_ref(shard_index, ref, table_name="learning_steps"):
    for shard in getattr(shard_index, table_name):
        for member in shard.episodes:
            if member.episode_ref == ref:
                return shard
    return None


async def _run_scale(spec: ScaleSpec, tmp_root: Path) -> dict:
    report: dict = {"scale": spec.name, "num_episodes": spec.num_episodes}
    tmp_path = tmp_root / spec.name
    tmp_path.mkdir(parents=True, exist_ok=True)

    build_start = time.perf_counter()
    artifacts = await write_scaled_dataset_artifacts(tmp_path, spec)
    report["build_seconds"] = round(time.perf_counter() - build_start, 3)

    shard_index = artifacts.learning_manifest.shard_index
    total_export_bytes = _total_export_bytes(shard_index)
    report["total_export_bytes"] = total_export_bytes
    report["num_shards"] = len(shard_index.learning_steps)
    refs = sorted(
        {m.episode_ref for shard in shard_index.learning_steps for m in shard.episodes},
        key=lambda r: (r.episode_id, r.aligned_artifact_checksum),
    )

    projection = feature_projection_for(spec)

    # ---- A: cold open ----
    store = CountingArtifactStore(LocalArtifactStore(root_uri=artifacts.storage_root_uri))
    t0 = time.perf_counter()
    dataset = await SceneOpsDataset.open(
        learning_manifest=artifacts.learning_manifest,
        learning_manifest_checksum=artifacts.learning_manifest_checksum,
        artifact_store=store,
    )
    report["A_open"] = {
        "wall_seconds": round(time.perf_counter() - t0, 6),
        "bytes": store.stats.read_bytes_total + store.stats.read_range_total,
        "amplification_vs_old_baseline": None,  # open() never touched steps/signals before either
    }

    # ---- B: cold single-episode access ----
    ref_b = refs[0]
    step_count_b = dataset.get_episode(ref_b).step_count
    io_before = store.stats.total_bytes_read
    t0 = time.perf_counter()
    await dataset.get_window(ref_b, 0, step_count_b, projection)
    bytes_b = store.stats.total_bytes_read - io_before
    report["B_cold_single_episode"] = {
        "wall_seconds": round(time.perf_counter() - t0, 6),
        "bytes": bytes_b,
        "old_baseline_bytes": total_export_bytes,
        "reduction_factor": round(total_export_bytes / bytes_b, 1) if bytes_b else None,
    }

    # ---- C: warm second episode, same shard as B ----
    shard_b = _shard_for_ref(shard_index, ref_b)
    same_shard_ref = next(
        (m.episode_ref for m in shard_b.episodes if m.episode_ref != ref_b), None
    )
    if same_shard_ref is not None:
        step_count_c = dataset.get_episode(same_shard_ref).step_count
        io_before = store.stats.total_bytes_read
        t0 = time.perf_counter()
        await dataset.get_window(same_shard_ref, 0, step_count_c, projection)
        bytes_c = store.stats.total_bytes_read - io_before
        report["C_warm_second_episode_same_shard"] = {
            "wall_seconds": round(time.perf_counter() - t0, 6),
            "bytes": bytes_c,
            "old_baseline_bytes": total_export_bytes,
        }
    else:
        report["C_warm_second_episode_same_shard"] = {"note": "only one episode in this shard"}

    # ---- D: cold episode in another shard ----
    other_shard_ref = next(
        (
            m.episode_ref
            for shard in shard_index.learning_steps
            if shard.shard_index != shard_b.shard_index
            for m in shard.episodes
        ),
        None,
    )
    if other_shard_ref is not None:
        step_count_d = dataset.get_episode(other_shard_ref).step_count
        io_before = store.stats.total_bytes_read
        t0 = time.perf_counter()
        await dataset.get_window(other_shard_ref, 0, step_count_d, projection)
        bytes_d = store.stats.total_bytes_read - io_before
        report["D_cold_episode_another_shard"] = {
            "wall_seconds": round(time.perf_counter() - t0, 6),
            "bytes": bytes_d,
            "old_baseline_bytes": total_export_bytes,
            "reduction_factor": round(total_export_bytes / bytes_d, 1) if bytes_d else None,
        }
    else:
        report["D_cold_episode_another_shard"] = {"note": "only one shard total"}

    async def _fresh_dataset() -> tuple[SceneOpsDataset, CountingArtifactStore]:
        # A brand-new SceneOpsDataset + store per side of a comparison, so
        # neither side benefits from the other's cached shard metadata --
        # otherwise, at scales with few shards, the second measurement
        # unfairly looks "warm" regardless of what it actually is.
        fresh_store = CountingArtifactStore(
            LocalArtifactStore(root_uri=artifacts.storage_root_uri)
        )
        fresh_dataset = await SceneOpsDataset.open(
            learning_manifest=artifacts.learning_manifest,
            learning_manifest_checksum=artifacts.learning_manifest_checksum,
            artifact_store=fresh_store,
        )
        return fresh_dataset, fresh_store

    # ---- E: fixed window vs full episode, each measured cold ----
    ref_narrow_window = refs[min(1, len(refs) - 1)]
    ref_full_window = refs[min(2, len(refs) - 1)]
    horizon = min(3, dataset.get_episode(ref_narrow_window).step_count)

    narrow_dataset, narrow_store = await _fresh_dataset()
    await narrow_dataset.get_window(ref_narrow_window, 0, horizon, projection)
    bytes_narrow_window = narrow_store.stats.total_bytes_read

    full_dataset, full_store = await _fresh_dataset()
    step_count_full = full_dataset.get_episode(ref_full_window).step_count
    await full_dataset.get_window(ref_full_window, 0, step_count_full, projection)
    bytes_full_window = full_store.stats.total_bytes_read

    report["E_fixed_window_vs_full_episode"] = {
        "narrow_window_horizon": horizon,
        "narrow_window_bytes": bytes_narrow_window,
        "full_episode_bytes": bytes_full_window,
        "identical": bytes_narrow_window == bytes_full_window,
        "note": "one row group per episode is the floor -- a sub-range window "
        "fetches the whole episode's row group regardless of horizon",
    }

    # ---- F: narrow vs wide FeatureProjection, each measured cold ----
    narrow_projection = FeatureProjection(
        observation_channels=projection.observation_channels[:1],
        action_channels=projection.action_channels[:1],
    )
    ref_narrow_proj = refs[min(3, len(refs) - 1)]
    ref_wide_proj = refs[min(4, len(refs) - 1)]

    narrow_proj_dataset, narrow_proj_store = await _fresh_dataset()
    step_count_np = narrow_proj_dataset.get_episode(ref_narrow_proj).step_count
    await narrow_proj_dataset.get_window(ref_narrow_proj, 0, step_count_np, narrow_projection)
    bytes_narrow_proj = narrow_proj_store.stats.total_bytes_read

    wide_proj_dataset, wide_proj_store = await _fresh_dataset()
    step_count_wp = wide_proj_dataset.get_episode(ref_wide_proj).step_count
    await wide_proj_dataset.get_window(ref_wide_proj, 0, step_count_wp, projection)
    bytes_wide_proj = wide_proj_store.stats.total_bytes_read

    report["F_narrow_vs_wide_projection"] = {
        "narrow_projection_bytes": bytes_narrow_proj,
        "wide_projection_bytes": bytes_wide_proj,
        "roughly_equal": abs(bytes_narrow_proj - bytes_wide_proj) / max(bytes_wide_proj, 1) < 0.15,
        "note": "learning_signals' tall/long schema has no per-channel column "
        "to project away -- narrowing FeatureProjection still fetches the "
        "same row-group bytes, confirming Request 5.1 §9 after row-group "
        "selectivity",
    }

    return report


async def _main_async(scale_names: list[str]) -> list[dict]:
    ladder_by_name = {spec.name: spec for spec in DEFAULT_SCALE_LADDER}
    reports = []
    with tempfile.TemporaryDirectory(prefix="sceneops-selective-bench-") as tmp_root_str:
        tmp_root = Path(tmp_root_str)
        for name in scale_names:
            spec = ladder_by_name[name]
            print(f"--- scale={name} ---", file=sys.stderr)
            reports.append(await _run_scale(spec, tmp_root))
    return reports


def _print_summary(reports: list[dict]) -> None:
    for report in reports:
        print(f"\n=== scale={report['scale']} (episodes={report['num_episodes']}, "
              f"shards={report['num_shards']}) ===")
        print(f"  build_seconds={report['build_seconds']} "
              f"total_export_bytes={report['total_export_bytes']}")
        for key in (
            "A_open", "B_cold_single_episode", "C_warm_second_episode_same_shard",
            "D_cold_episode_another_shard",
        ):
            print(f"  {key}: {report[key]}")
        print(f"  E_fixed_window_vs_full_episode: {report['E_fixed_window_vs_full_episode']}")
        print(f"  F_narrow_vs_wide_projection: {report['F_narrow_vs_wide_projection']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scales",
        default=",".join(spec.name for spec in DEFAULT_SCALE_LADDER),
        help="comma-separated scale names (default: all)",
    )
    parser.add_argument("--out", default=None, help="write full JSON report to this path")
    args = parser.parse_args()

    scale_names = [s.strip() for s in args.scales.split(",") if s.strip()]
    reports = asyncio.run(_main_async(scale_names))
    _print_summary(reports)

    if args.out:
        Path(args.out).write_text(json.dumps(reports, indent=2))
        print(f"\nfull report written to {args.out}")


if __name__ == "__main__":
    main()
