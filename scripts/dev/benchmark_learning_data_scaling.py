#!/usr/bin/env python
"""SceneOps V2 Request 5.1: learning-data access path scaling benchmark.

Measures the actual, current (pre-Phase-5-redesign) cost of the primary
robot-learning workloads against SceneOpsDataset, at several synthetic
scales, using real Phase 2/2.5/2.7B-D contracts throughout (no mocked
Parquet, no handcrafted SceneOpsDataset). Every scale is built via
``sceneops_analytics.testing.scale_fixture`` (deterministic, DB-free).

This is a profiling/reporting tool, not a test: it makes no pass/fail
assertions and has no flaky wall-clock thresholds. It exists to produce
reproducible baseline numbers backing SceneOps V2 Request 5.1's audit
deliverable, not to gate CI. Not part of ``make test``.

Usage:
    uv run python scripts/dev/benchmark_learning_data_scaling.py
    uv run python scripts/dev/benchmark_learning_data_scaling.py --scales xs,s
    uv run python scripts/dev/benchmark_learning_data_scaling.py --out /tmp/report.json
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import resource
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path
from urllib.parse import urlparse

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "packages" / "sceneops-analytics"))
sys.path.insert(0, str(_REPO_ROOT / "packages" / "sceneops-core"))
sys.path.insert(0, str(_REPO_ROOT / "packages" / "sceneops-storage"))

from sceneops_analytics.learning_dataset import SceneOpsDataset, SequenceSampler  # noqa: E402
from sceneops_analytics.testing import (  # noqa: E402
    CountingArtifactStore,
    DEFAULT_SCALE_LADDER,
    ScaleSpec,
    episode_ref,
    write_scaled_dataset_artifacts,
)
from sceneops_core.episodes.learning import FeatureProjection  # noqa: E402
from sceneops_storage import LocalArtifactStore  # noqa: E402


def _uri_to_path(uri: str) -> Path:
    parsed = urlparse(uri)
    return Path(parsed.path) if parsed.scheme == "file" else Path(uri)


@contextlib.contextmanager
def _phase(results: dict, name: str):
    """Times one phase and records its *peak* traced-Python-memory delta
    (tracemalloc.reset_peak() isolates this phase's peak from prior
    phases' allocations, which linger as live objects but aren't "peak
    growth" attributable to this phase)."""
    tracemalloc.reset_peak()
    start = time.perf_counter()
    yield
    elapsed = time.perf_counter() - start
    _current, peak = tracemalloc.get_traced_memory()
    results[name] = {"wall_seconds": round(elapsed, 6), "traced_peak_bytes": peak}


def _io_snapshot(store: CountingArtifactStore) -> dict:
    return {
        "read_bytes_calls": store.stats.read_bytes_calls,
        "read_bytes_total": store.stats.read_bytes_total,
        "distinct_uris_read": store.stats.distinct_uris_read,
    }


def _io_delta(before: dict, after: dict) -> dict:
    return {key: after[key] - before[key] for key in before}


async def _run_scale(spec: ScaleSpec, tmp_root: Path) -> dict:
    report: dict = {"scale": spec.name, "spec": vars(spec), "phases": {}, "io": {}}
    phases = report["phases"]
    io = report["io"]

    tmp_path = tmp_root / spec.name
    tmp_path.mkdir(parents=True, exist_ok=True)

    build_start = time.perf_counter()
    artifacts = await write_scaled_dataset_artifacts(tmp_path, spec)
    report["build_seconds"] = round(time.perf_counter() - build_start, 6)

    table_bytes = {}
    for table_name, uri in artifacts.learning_manifest.table_uris.items():
        table_bytes[table_name] = os.path.getsize(_uri_to_path(uri))
    report["table_file_bytes"] = table_bytes
    report["table_row_counts"] = artifacts.learning_manifest.row_counts

    full_projection = artifacts.feature_projection
    narrow_projection = FeatureProjection(
        observation_channels=[spec.observation_channels[0]],
        action_channels=[spec.action_channels[0]],
    )

    refs = [episode_ref(spec, i) for i in range(spec.num_episodes)]
    ref_c = refs[0]
    ref_d = refs[min(1, len(refs) - 1)]
    ref_e_narrow = refs[min(2, len(refs) - 1)]
    ref_e_full = refs[min(3, len(refs) - 1)] if len(refs) > 3 else refs[0]

    # ---- Pass 1: one long-lived dataset instance, staged workloads A-G ----
    base_store = LocalArtifactStore(root_uri=artifacts.storage_root_uri)
    store = CountingArtifactStore(base_store)

    with _phase(phases, "A_open_dataset_enumerate_refs"):
        dataset = await SceneOpsDataset.open(
            learning_manifest=artifacts.learning_manifest,
            learning_manifest_checksum=artifacts.learning_manifest_checksum,
            artifact_store=store,
        )
        _ = dataset.episodes()
    io_after_open = _io_snapshot(store)
    io["A_open_dataset_enumerate_refs"] = io_after_open

    with _phase(phases, "B_fetch_one_episode_revision_metadata"):
        _ = dataset.get_episode(ref_c)
    io["B_fetch_one_episode_revision_metadata"] = _io_delta(
        io_after_open, _io_snapshot(store)
    )

    io_before_c = _io_snapshot(store)
    with _phase(phases, "C_fetch_all_steps_one_episode_cold_tables"):
        window_c = await dataset.get_window(
            ref_c, 0, dataset.get_episode(ref_c).step_count, full_projection
        )
    io_after_c = _io_snapshot(store)
    io["C_fetch_all_steps_one_episode_cold_tables"] = _io_delta(io_before_c, io_after_c)
    assert window_c.observation  # sanity: projection actually ran

    io_before_d = io_after_c
    horizon_d = min(10, spec.steps_per_episode)
    with _phase(phases, "D_fixed_window_second_episode_warm_tables"):
        await dataset.get_window(ref_d, 0, horizon_d, full_projection)
    io["D_fixed_window_second_episode_warm_tables"] = _io_delta(
        io_before_d, _io_snapshot(store)
    )

    io_before_e = _io_snapshot(store)
    horizon_e = min(10, spec.steps_per_episode)
    with _phase(phases, "E1_window_narrow_projection_1ch"):
        await dataset.get_window(ref_e_narrow, 0, horizon_e, narrow_projection)
    io["E1_window_narrow_projection_1ch"] = _io_delta(io_before_e, _io_snapshot(store))

    io_before_e2 = _io_snapshot(store)
    with _phase(phases, "E2_window_full_projection_same_horizon"):
        await dataset.get_window(ref_e_full, 0, horizon_e, full_projection)
    io["E2_window_full_projection_same_horizon"] = _io_delta(
        io_before_e2, _io_snapshot(store)
    )

    io_before_f = _io_snapshot(store)
    horizon_f = min(5, spec.steps_per_episode)
    with _phase(phases, "F1_sampler_create_all_episodes"):
        sampler = await SequenceSampler.create(
            dataset, projection=full_projection, horizon=horizon_f, stride=horizon_f
        )
    with _phase(phases, "F2_sampler_iterate_all_windows"):
        window_count = len(sampler)
        for i in range(window_count):
            await sampler.get(i)
    phases["F2_sampler_iterate_all_windows"]["window_count"] = window_count
    io["F_sampler_create_and_iterate_all"] = _io_delta(io_before_f, _io_snapshot(store))

    with _phase(phases, "G_curate_filter_episodes_in_memory"):
        _ = [ref for ref in dataset.episodes() if dataset.get_episode(ref).step_count > 0]
    io["G_curate_filter_episodes_in_memory"] = _io_delta(
        io_before_f, _io_snapshot(store)
    )  # unchanged from F's end; isolates G's own (zero) I/O

    # ---- Pass 2: fresh dataset instance, single-pass full-dataset export ----
    export_store = CountingArtifactStore(LocalArtifactStore(root_uri=artifacts.storage_root_uri))
    with _phase(phases, "H_cold_open_plus_full_dataset_export"):
        export_dataset = await SceneOpsDataset.open(
            learning_manifest=artifacts.learning_manifest,
            learning_manifest_checksum=artifacts.learning_manifest_checksum,
            artifact_store=export_store,
        )
        exported_steps = 0
        for ref in export_dataset.episodes():
            metadata = export_dataset.get_episode(ref)
            if metadata.step_count == 0:
                continue
            window = await export_dataset.get_window(
                ref, 0, metadata.step_count, full_projection
            )
            exported_steps += len(window.timestamps_us)
    phases["H_cold_open_plus_full_dataset_export"]["exported_steps"] = exported_steps
    io["H_cold_open_plus_full_dataset_export"] = _io_snapshot(export_store)

    report["ru_maxrss_kb_after_scale"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return report


async def _main_async(scale_names: list[str]) -> list[dict]:
    ladder_by_name = {spec.name: spec for spec in DEFAULT_SCALE_LADDER}
    unknown = [name for name in scale_names if name not in ladder_by_name]
    if unknown:
        raise SystemExit(
            f"unknown scale(s) {unknown}; choose from {sorted(ladder_by_name)}"
        )

    tracemalloc.start()
    reports = []
    with tempfile.TemporaryDirectory(prefix="sceneops-bench-") as tmp_root_str:
        tmp_root = Path(tmp_root_str)
        for name in scale_names:
            spec = ladder_by_name[name]
            print(f"--- running scale={spec.name} "
                  f"(episodes={spec.num_episodes} steps/ep={spec.steps_per_episode} "
                  f"channels={spec.num_observation_channels + spec.num_action_channels}) ---",
                  file=sys.stderr)
            reports.append(await _run_scale(spec, tmp_root))
    tracemalloc.stop()
    return reports


def _print_summary(reports: list[dict]) -> None:
    for report in reports:
        print(f"\n=== scale={report['scale']} ===")
        print(f"  build_seconds        : {report['build_seconds']}")
        print(f"  table_file_bytes     : {report['table_file_bytes']}")
        print(f"  table_row_counts     : {report['table_row_counts']}")
        for phase_name, phase in report["phases"].items():
            print(f"  {phase_name:45s} wall={phase['wall_seconds']:>10.6f}s "
                  f"traced_peak={phase['traced_peak_bytes']:>10d}B")
        print("  io deltas:")
        for key, val in report["io"].items():
            print(f"    {key:45s} {val}")
        print(f"  ru_maxrss_kb_after_scale: {report['ru_maxrss_kb_after_scale']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scales",
        default=",".join(spec.name for spec in DEFAULT_SCALE_LADDER),
        help="comma-separated scale names to run (default: all)",
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
