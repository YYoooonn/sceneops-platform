#!/usr/bin/env python
"""SceneOps V2 Request 5.4: bounded-cache and bulk-access benchmark.

Extends the Request 5.1/5.3 benchmark instrumentation. At each scale in
the standard ladder, under the production shard policy
(``default_shard_policy()``), measures:

    A. cache-disabled sparse access (a spread-out set of EpisodeRefs,
       DISABLED_CACHE_POLICY -- raw selective-read cost, no cache effects)
    B. bounded-cache sparse access (same access pattern, DEFAULT_CACHE_POLICY)
    C. repeated working-set access (touch the same small EpisodeRef set
       twice -- bounded cache should show the second pass as free; disabled
       should re-fetch identically both times)
    D. full/high-density EpisodeRef iteration (every episode's window,
       once) -- disabled vs bounded cache
    E. SequenceSampler.create() -- before Request 5.4 (many independent
       selective fetches) vs after (shard-grouped bulk fetches)
    F. peak (traced) memory for each of the above

Profiling/reporting tool, not a test: no pass/fail assertions, no flaky
wall-clock thresholds. Not part of `make test`.

Usage:
    uv run python tools/benchmarks/learning_data/benchmark_cache_and_bulk_access.py
    uv run python tools/benchmarks/learning_data/benchmark_cache_and_bulk_access.py --scales tiny,small
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "packages" / "sceneops-analytics"))
sys.path.insert(0, str(_REPO_ROOT / "packages" / "sceneops-core"))
sys.path.insert(0, str(_REPO_ROOT / "packages" / "sceneops-storage"))

from sceneops_analytics import (  # noqa: E402
    DEFAULT_CACHE_POLICY,
    DISABLED_CACHE_POLICY,
    SceneOpsDataset,
    SequenceSampler,
)
from sceneops_analytics.testing import (  # noqa: E402
    DEFAULT_SCALE_LADDER,
    CountingArtifactStore,
    ScaleSpec,
    feature_projection_for,
    write_scaled_dataset_artifacts,
)
from sceneops_storage import LocalArtifactStore  # noqa: E402


async def _open(artifacts, cache_policy):
    store = CountingArtifactStore(
        LocalArtifactStore(root_uri=artifacts.storage_root_uri)
    )
    dataset = await SceneOpsDataset.open(
        learning_manifest=artifacts.learning_manifest,
        learning_manifest_checksum=artifacts.learning_manifest_checksum,
        artifact_store=store,
        cache_policy=cache_policy,
    )
    return dataset, store


def _sparse_refs(refs: list, n: int) -> list:
    step = max(1, len(refs) // n)
    return refs[::step][:n]


async def _measure(label: str, coro_factory) -> dict:
    tracemalloc.reset_peak()
    t0 = time.perf_counter()
    await coro_factory()
    elapsed = time.perf_counter() - t0
    _current, peak = tracemalloc.get_traced_memory()
    return {
        "label": label,
        "wall_seconds": round(elapsed, 6),
        "traced_peak_bytes": peak,
    }


async def _run_scale(spec: ScaleSpec, tmp_root: Path) -> dict:
    report: dict = {"scale": spec.name, "num_episodes": spec.num_episodes}
    tmp_path = tmp_root / spec.name
    tmp_path.mkdir(parents=True, exist_ok=True)

    artifacts = await write_scaled_dataset_artifacts(tmp_path, spec)
    projection = feature_projection_for(spec)

    async def sparse_pass(dataset, store, refs):
        io_before = store.stats.total_bytes_read
        calls_before = store.stats.read_range_calls
        for ref in refs:
            await dataset.get_window(
                ref, 0, dataset.get_episode(ref).step_count, projection
            )
        return (
            store.stats.total_bytes_read - io_before,
            store.stats.read_range_calls - calls_before,
        )

    # ---- A/B: cache-disabled vs bounded-cache sparse access ----
    for label, policy in (
        ("A_disabled_cache", DISABLED_CACHE_POLICY),
        ("B_bounded_cache", DEFAULT_CACHE_POLICY),
    ):
        dataset, store = await _open(artifacts, policy)
        refs = _sparse_refs(dataset.episodes(), min(20, spec.num_episodes))
        tracemalloc.reset_peak()
        t0 = time.perf_counter()
        bytes_read, range_calls = await sparse_pass(dataset, store, refs)
        elapsed = time.perf_counter() - t0
        _current, peak = tracemalloc.get_traced_memory()
        report[label] = {
            "wall_seconds": round(elapsed, 6),
            "bytes": bytes_read,
            "range_calls": range_calls,
            "traced_peak_bytes": peak,
            "episode_steps_cache_len": len(dataset._episode_steps_cache),
        }

    # ---- C: repeated working-set access (same small set, twice) ----
    working_set_size = min(10, spec.num_episodes)
    for label, policy in (
        ("C1_repeated_workingset_disabled", DISABLED_CACHE_POLICY),
        ("C2_repeated_workingset_bounded", DEFAULT_CACHE_POLICY),
    ):
        dataset, store = await _open(artifacts, policy)
        refs = dataset.episodes()[:working_set_size]
        await sparse_pass(dataset, store, refs)  # first pass, warms whatever it can
        bytes_second, calls_second = await sparse_pass(
            dataset, store, refs
        )  # second pass
        report[label] = {
            "second_pass_bytes": bytes_second,
            "second_pass_range_calls": calls_second,
            "cache_hits": dataset._episode_steps_cache.stats.hits,
            "cache_misses": dataset._episode_steps_cache.stats.misses,
        }

    # ---- D: full/high-density iteration -- disabled vs bounded ----
    # Capped at 500 episodes for the largest scale purely for benchmark
    # runtime (disabled-cache means every one of these re-fetches its
    # shard's footer with zero reuse, which is O(episodes) real I/O calls)
    # -- reported honestly as `episodes_iterated`, not silently as "full."
    for label, policy in (
        ("D1_full_iteration_disabled", DISABLED_CACHE_POLICY),
        ("D2_full_iteration_bounded", DEFAULT_CACHE_POLICY),
    ):
        dataset, store = await _open(artifacts, policy)
        all_refs = dataset.episodes()[:500]
        tracemalloc.reset_peak()
        t0 = time.perf_counter()
        bytes_read, range_calls = await sparse_pass(dataset, store, all_refs)
        elapsed = time.perf_counter() - t0
        _current, peak = tracemalloc.get_traced_memory()
        report[label] = {
            "episodes_iterated": len(all_refs),
            "wall_seconds": round(elapsed, 6),
            "bytes": bytes_read,
            "range_calls": range_calls,
            "traced_peak_bytes": peak,
            "episode_steps_cache_len": len(dataset._episode_steps_cache),
        }

    # ---- E: SequenceSampler.create() ----
    dataset, store = await _open(artifacts, DEFAULT_CACHE_POLICY)
    tracemalloc.reset_peak()
    t0 = time.perf_counter()
    sampler = await SequenceSampler.create(
        dataset, projection=projection, horizon=5, stride=5
    )
    elapsed = time.perf_counter() - t0
    _current, peak = tracemalloc.get_traced_memory()
    report["E_sequence_sampler_create"] = {
        "wall_seconds": round(elapsed, 6),
        "range_calls": store.stats.read_range_calls,
        "bytes": store.stats.read_range_total,
        "traced_peak_bytes": peak,
        "num_windows": len(sampler),
    }

    return report


async def _main_async(scale_names: list[str]) -> list[dict]:
    tracemalloc.start()
    ladder_by_name = {spec.name: spec for spec in DEFAULT_SCALE_LADDER}
    reports = []
    with tempfile.TemporaryDirectory(prefix="sceneops-cache-bench-") as tmp_root_str:
        tmp_root = Path(tmp_root_str)
        for name in scale_names:
            spec = ladder_by_name[name]
            print(f"--- scale={name} ---", file=sys.stderr)
            reports.append(await _run_scale(spec, tmp_root))
    tracemalloc.stop()
    return reports


def _print_summary(reports: list[dict]) -> None:
    for report in reports:
        print(f"\n=== scale={report['scale']} (episodes={report['num_episodes']}) ===")
        for key in (
            "A_disabled_cache",
            "B_bounded_cache",
            "C1_repeated_workingset_disabled",
            "C2_repeated_workingset_bounded",
            "D1_full_iteration_disabled",
            "D2_full_iteration_bounded",
            "E_sequence_sampler_create",
        ):
            print(f"  {key}: {report[key]}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scales",
        default=",".join(spec.name for spec in DEFAULT_SCALE_LADDER),
        help="comma-separated scale names (default: all)",
    )
    parser.add_argument(
        "--out", default=None, help="write full JSON report to this path"
    )
    args = parser.parse_args()

    scale_names = [s.strip() for s in args.scales.split(",") if s.strip()]
    reports = asyncio.run(_main_async(scale_names))
    _print_summary(reports)

    if args.out:
        Path(args.out).write_text(json.dumps(reports, indent=2))
        print(f"\nfull report written to {args.out}")


if __name__ == "__main__":
    main()
