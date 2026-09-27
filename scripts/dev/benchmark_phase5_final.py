#!/usr/bin/env python
"""SceneOps V2 Request 5.6: final Phase 5 scale benchmark.

Validates the completed Phase 5 architecture (sharded layout [5.2],
selective reads [5.3], bounded cache/bulk access [5.4], incremental
export [5.5]) at larger scales than any single prior request measured
together, and produces the numbers backing this request's
distributed-processing-boundary analysis.

Extends ``DEFAULT_SCALE_LADDER`` (tiny/small/medium/large) with one new
``xlarge`` tier (25,000 episodes -- 2.5x "large"'s episode count, same
per-episode shape) to see whether any measured workload's *scaling
behavior* (not just absolute time) changes as EpisodeRef count grows
further. Chosen empirically: a standalone timing of "large" (10,000
episodes) took ~87s to build; 25,000 was the largest step up that stayed
within a few minutes end-to-end for this benchmark run, consistent with
Request 5.2's own note that the per-row Python table builders (not
Phase 5's actual read/write architecture) are this benchmark harness's
own practical ceiling, not the production architecture's.

Every phase runs against a ``_TimedCountingArtifactStore`` (this module
only -- test/benchmark-support code, never imported by production code)
that tracks both call/byte counts (mirrors
``sceneops_analytics.testing.CountingArtifactStore``) *and* wall-clock
time spent inside each awaited ArtifactStore call. Per phase, this gives
a clean, non-invasive split without touching any production code:

    io_wall_seconds       = time actually spent inside artifact_store calls
    reconstruction_seconds = phase.wall_seconds - io_wall_seconds

... i.e. Parquet decode + Python/Pydantic object construction + (for
export phases) Polars/PyArrow table construction, whatever isn't time
spent waiting on the store itself.

Profiling/reporting tool, not a test: no pass/fail assertions, no flaky
wall-clock thresholds. Not part of `make test`.

Usage:
    uv run python scripts/dev/benchmark_phase5_final.py
    uv run python scripts/dev/benchmark_phase5_final.py --scales tiny,small
    uv run python scripts/dev/benchmark_phase5_final.py --out /tmp/report.json
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import platform
import resource
import sys
import tempfile
import time
import tracemalloc
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "packages" / "sceneops-analytics"))
sys.path.insert(0, str(_REPO_ROOT / "packages" / "sceneops-core"))
sys.path.insert(0, str(_REPO_ROOT / "packages" / "sceneops-storage"))

from sceneops_analytics import (  # noqa: E402
    AnalyticsTableWriter,
    write_incremental_sharded_learning_tables,
    write_sharded_learning_tables,
)
from sceneops_analytics.learning_dataset import SceneOpsDataset, SequenceSampler  # noqa: E402
from sceneops_analytics.learning_dataset.dataset import _build_steps_from_tables  # noqa: E402
from sceneops_analytics.learning_dataset.parquet_range_reader import (  # noqa: E402
    read_episode_row_group,
)
from sceneops_analytics.testing import (  # noqa: E402
    DEFAULT_SCALE_LADDER,
    ScaleSpec,
    build_scaled_entries,
    episode_ref,
    write_scaled_dataset_artifacts,
)
from sceneops_core.artifacts.contracts import ArtifactStore  # noqa: E402
from sceneops_core.common.schemas import ArtifactUri  # noqa: E402
from sceneops_core.episodes.learning_export import (  # noqa: E402
    AlignedArtifactRevision,
    LearningDataExportConfig,
    default_shard_policy,
    learning_data_export_id,
    plan_incremental_export,
)
from sceneops_storage import LocalArtifactStore  # noqa: E402

XLARGE_SPEC = ScaleSpec(
    name="xlarge",
    num_episodes=25_000,
    steps_per_episode=30,
    num_observation_channels=6,
    num_action_channels=4,
    num_core_observation_channels=4,
    num_core_action_channels=3,
    length_jitter_fraction=0.3,
    extra_revision_every=500,
)
SCALE_LADDER = (*DEFAULT_SCALE_LADDER, XLARGE_SPEC)
_POLICY = default_shard_policy()
# ru_maxrss's unit is platform-dependent: kilobytes on Linux, bytes on
# macOS/BSD -- reported honestly here (as bytes always, converting on
# Linux) rather than perpetuating the "_kb" naming Request 5.1's original
# script used (which is only correct on Linux).
_RU_MAXRSS_IS_KB = platform.system() == "Linux"


@dataclass
class TimedIoStats:
    read_bytes_calls: int = 0
    read_bytes_total: int = 0
    read_range_calls: int = 0
    read_range_total: int = 0
    write_bytes_calls: int = 0
    write_bytes_total: int = 0
    io_wall_seconds: float = 0.0
    per_uri_range_calls: Counter[str] = field(default_factory=Counter)

    def snapshot(self) -> dict:
        return {
            "read_bytes_calls": self.read_bytes_calls,
            "read_bytes_total": self.read_bytes_total,
            "read_range_calls": self.read_range_calls,
            "read_range_total": self.read_range_total,
            "write_bytes_calls": self.write_bytes_calls,
            "write_bytes_total": self.write_bytes_total,
            "io_wall_seconds": round(self.io_wall_seconds, 6),
            "distinct_uris_range_read": len(self.per_uri_range_calls),
        }


class _TimedCountingArtifactStore(ArtifactStore):
    """Like ``CountingArtifactStore`` (call/byte counts) plus wall-clock
    time spent inside each awaited call -- lets every phase below report
    an I/O-wall-time vs reconstruction-wall-time split without touching
    any production code. Benchmark-only, mirrors
    ``sceneops_analytics.testing.CountingArtifactStore``'s forwarding
    behavior exactly (every non-timed method passes straight through)."""

    def __init__(self, inner: ArtifactStore) -> None:
        self._inner = inner
        self.stats = TimedIoStats()

    def join_uri(self, root: ArtifactUri, *parts: str) -> ArtifactUri:
        return self._inner.join_uri(root, *parts)

    async def exists(self, uri: ArtifactUri) -> bool:
        return await self._inner.exists(uri)

    async def read_json(self, uri: ArtifactUri):
        return await self._inner.read_json(uri)

    async def write_json(self, uri: ArtifactUri, payload) -> None:
        await self._inner.write_json(uri, payload)

    async def read_bytes(self, uri: ArtifactUri) -> bytes:
        start = time.perf_counter()
        data = await self._inner.read_bytes(uri)
        self.stats.io_wall_seconds += time.perf_counter() - start
        self.stats.read_bytes_calls += 1
        self.stats.read_bytes_total += len(data)
        return data

    async def read_range(self, uri: ArtifactUri, offset: int, length: int) -> bytes:
        start = time.perf_counter()
        data = await self._inner.read_range(uri, offset, length)
        self.stats.io_wall_seconds += time.perf_counter() - start
        self.stats.read_range_calls += 1
        self.stats.read_range_total += len(data)
        self.stats.per_uri_range_calls[uri] += 1
        return data

    async def write_bytes(self, uri: ArtifactUri, data: bytes) -> None:
        start = time.perf_counter()
        await self._inner.write_bytes(uri, data)
        self.stats.io_wall_seconds += time.perf_counter() - start
        self.stats.write_bytes_calls += 1
        self.stats.write_bytes_total += len(data)

    async def list_json(self, uri: ArtifactUri) -> list[ArtifactUri]:
        return await self._inner.list_json(uri)

    async def delete_prefix(self, uri: ArtifactUri) -> None:
        await self._inner.delete_prefix(uri)

    def public_url(self, uri: ArtifactUri) -> str:
        return self._inner.public_url(uri)


def _uri_to_path(uri: str) -> Path:
    parsed = urlparse(uri)
    return Path(parsed.path) if parsed.scheme == "file" else Path(uri)


@contextlib.contextmanager
def _phase(results: dict, io_stats: TimedIoStats, name: str):
    tracemalloc.reset_peak()
    io_wall_before = io_stats.io_wall_seconds
    start = time.perf_counter()
    yield
    elapsed = time.perf_counter() - start
    io_wall_delta = io_stats.io_wall_seconds - io_wall_before
    _current, peak = tracemalloc.get_traced_memory()
    results[name] = {
        "wall_seconds": round(elapsed, 6),
        "io_wall_seconds": round(io_wall_delta, 6),
        "reconstruction_seconds": round(max(0.0, elapsed - io_wall_delta), 6),
        "traced_peak_bytes": peak,
    }


async def _run_scale(spec: ScaleSpec, tmp_root: Path) -> dict:
    report: dict = {"scale": spec.name, "spec": vars(spec), "phases": {}, "io": {}}
    phases = report["phases"]
    io = report["io"]

    tmp_path = tmp_root / spec.name
    tmp_path.mkdir(parents=True, exist_ok=True)

    build_start = time.perf_counter()
    artifacts = await write_scaled_dataset_artifacts(tmp_path, spec, shard_policy=_POLICY)
    report["build_seconds"] = round(time.perf_counter() - build_start, 6)

    manifest = artifacts.learning_manifest
    table_bytes = {}
    for table_name, uri in manifest.table_uris.items():
        table_bytes[table_name] = os.path.getsize(_uri_to_path(uri))
    report["table_file_bytes"] = table_bytes
    report["table_row_counts"] = manifest.row_counts
    shard_index = manifest.shard_index
    object_count = (
        len(manifest.table_uris)  # learning_episodes (never sharded)
        + len(shard_index.learning_steps)
        + len(shard_index.learning_signals)
        + 1  # manifest.json
    )
    report["object_count"] = object_count
    report["shard_counts"] = {
        "learning_steps": len(shard_index.learning_steps),
        "learning_signals": len(shard_index.learning_signals),
    }
    report["total_storage_bytes"] = sum(table_bytes.values()) + sum(
        shard.size_bytes
        for shard in (*shard_index.learning_steps, *shard_index.learning_signals)
    )

    full_projection = artifacts.feature_projection
    refs = [episode_ref(spec, i) for i in range(spec.num_episodes)]
    ref_cold = refs[0]
    ref_warm = refs[min(1, len(refs) - 1)]

    base_store = LocalArtifactStore(root_uri=artifacts.storage_root_uri)
    store = _TimedCountingArtifactStore(base_store)
    stats = store.stats

    with _phase(phases, stats, "A_open_dataset_enumerate_refs"):
        dataset = await SceneOpsDataset.open(
            learning_manifest=manifest,
            learning_manifest_checksum=artifacts.learning_manifest_checksum,
            artifact_store=store,
        )
        _ = dataset.episodes()
    io["A_open_dataset_enumerate_refs"] = stats.snapshot()

    io_before = stats.snapshot()
    with _phase(phases, stats, "B_cold_single_episode_full_window"):
        window = await dataset.get_window(
            ref_cold, 0, dataset.get_episode(ref_cold).step_count, full_projection
        )
    assert window.observation
    io["B_cold_single_episode_full_window"] = _io_delta(io_before, stats.snapshot())

    io_before = stats.snapshot()
    with _phase(phases, stats, "C_warm_repeated_access_same_episode"):
        for _ in range(5):
            await dataset.get_window(
                ref_cold, 0, dataset.get_episode(ref_cold).step_count, full_projection
            )
    io["C_warm_repeated_access_same_episode"] = _io_delta(io_before, stats.snapshot())

    io_before = stats.snapshot()
    with _phase(phases, stats, "D_cold_second_episode"):
        await dataset.get_window(
            ref_warm, 0, dataset.get_episode(ref_warm).step_count, full_projection
        )
    io["D_cold_second_episode"] = _io_delta(io_before, stats.snapshot())

    horizon = min(10, spec.steps_per_episode)
    io_before = stats.snapshot()
    with _phase(phases, stats, "E_sampler_create"):
        sampler = await SequenceSampler.create(
            dataset, projection=full_projection, horizon=horizon, stride=horizon
        )
    io["E_sampler_create"] = _io_delta(io_before, stats.snapshot())

    io_before = stats.snapshot()
    with _phase(phases, stats, "F_sampler_iterate_all_windows"):
        window_count = len(sampler)
        for i in range(window_count):
            await sampler.get(i)
    phases["F_sampler_iterate_all_windows"]["window_count"] = window_count
    io["F_sampler_iterate_all_windows"] = _io_delta(io_before, stats.snapshot())

    # ---- fresh dataset instance: cold full-dataset (high-density) iteration ----
    export_store = _TimedCountingArtifactStore(
        LocalArtifactStore(root_uri=artifacts.storage_root_uri)
    )
    with _phase(phases, export_store.stats, "G_cold_open_plus_full_dataset_iteration"):
        full_dataset = await SceneOpsDataset.open(
            learning_manifest=manifest,
            learning_manifest_checksum=artifacts.learning_manifest_checksum,
            artifact_store=export_store,
        )
        exported_steps = 0
        for ref in full_dataset.episodes():
            metadata = full_dataset.get_episode(ref)
            if metadata.step_count == 0:
                continue
            window = await full_dataset.get_window(
                ref, 0, metadata.step_count, full_projection
            )
            exported_steps += len(window.timestamps_us)
    phases["G_cold_open_plus_full_dataset_iteration"]["exported_steps"] = exported_steps
    io["G_cold_open_plus_full_dataset_iteration"] = export_store.stats.snapshot()

    # ---- full export write cost (fresh export_id, entire entry set) ----
    entries = build_scaled_entries(spec)
    export_config = LearningDataExportConfig()
    full_export_id = learning_data_export_id(
        aligned_checksums=[c for c, _ in entries], export_config=export_config
    )
    full_write_store = _TimedCountingArtifactStore(
        LocalArtifactStore(root_uri=str(tmp_path / "full-export"))
    )
    full_writer = AnalyticsTableWriter(
        artifact_store=full_write_store, root_uri=str(tmp_path / "full-export")
    )
    with _phase(phases, full_write_store.stats, "H_full_export_write"):
        await write_sharded_learning_tables(
            full_writer,
            dataset_id="bench-full",
            dataset_version="v1",
            export_id=full_export_id,
            entries=entries,
            policy=_POLICY,
        )
    io["H_full_export_write"] = full_write_store.stats.snapshot()

    # ---- incremental export write cost (small delta: ~1% new episodes) ----
    delta_count = max(1, spec.num_episodes // 100)
    delta_spec = ScaleSpec(
        name=f"{spec.name}-delta",
        num_episodes=spec.num_episodes + delta_count,
        steps_per_episode=spec.steps_per_episode,
        num_observation_channels=spec.num_observation_channels,
        num_action_channels=spec.num_action_channels,
        num_core_observation_channels=spec.num_core_observation_channels,
        num_core_action_channels=spec.num_core_action_channels,
        length_jitter_fraction=spec.length_jitter_fraction,
        extra_revision_every=spec.extra_revision_every,
    )
    delta_entries_all = build_scaled_entries(delta_spec)
    base_checksums = {c for c, _ in entries}
    delta_entries = [(c, a) for c, a in delta_entries_all if c not in base_checksums]
    all_revisions = [
        AlignedArtifactRevision(
            episode_id=a.aligned_episode.episode_id,
            aligned_artifact_id=f"art-{c}",
            aligned_artifact_checksum=c,
        )
        for c, a in delta_entries_all
    ]
    incremental_export_id = learning_data_export_id(
        aligned_checksums=[r.aligned_artifact_checksum for r in all_revisions],
        export_config=export_config,
    )
    plan = plan_incremental_export(manifest, all_revisions)
    incr_store = _TimedCountingArtifactStore(
        LocalArtifactStore(root_uri=str(tmp_path / "incremental-export"))
    )
    incr_writer = AnalyticsTableWriter(
        artifact_store=incr_store, root_uri=str(tmp_path / "incremental-export")
    )
    with _phase(phases, incr_store.stats, "I_incremental_export_write"):
        incremental_shard_index = await write_incremental_sharded_learning_tables(
            incr_writer,
            dataset_id="bench-incremental",
            dataset_version="v1",
            export_id=incremental_export_id,
            delta_entries=delta_entries,
            policy=_POLICY,
            plan=plan,
        )
    io["I_incremental_export_write"] = incr_store.stats.snapshot()
    report["incremental_delta_episode_count"] = delta_count
    report["incremental_new_shard_count"] = len(
        incremental_shard_index.learning_steps
    ) - len(plan.reused_learning_steps_shards) + len(
        incremental_shard_index.learning_signals
    ) - len(plan.reused_learning_signals_shards)
    report["incremental_reused_shard_count"] = len(
        plan.reused_learning_steps_shards
    ) + len(plan.reused_learning_signals_shards)

    ru_maxrss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    report["ru_maxrss_bytes_after_scale"] = (
        ru_maxrss * 1024 if _RU_MAXRSS_IS_KB else ru_maxrss
    )
    return report


def _io_delta(before: dict, after: dict) -> dict:
    delta = {}
    for key in before:
        if isinstance(before[key], (int, float)):
            delta[key] = after[key] - before[key]
        else:
            delta[key] = after[key]
    return delta


async def _reconstruction_isolation(spec: ScaleSpec, tmp_root: Path, sample_size: int = 50) -> dict:
    """Isolates pure Python/Pydantic ``LearningStep`` object-graph
    construction cost (``_build_steps_from_tables``) from Parquet-decode +
    I/O cost (``read_episode_row_group``), for a sample of episodes, at
    one scale -- confirming/quantifying Request 5.4's qualitative finding
    that large-scale ``SequenceSampler.create()`` iteration is dominated
    by Python object construction, not I/O (SceneOps V2 Request 5.6 §4).
    """
    tmp_path = tmp_root / f"{spec.name}-reconstruction"
    tmp_path.mkdir(parents=True, exist_ok=True)
    artifacts = await write_scaled_dataset_artifacts(tmp_path, spec, shard_policy=_POLICY)
    manifest = artifacts.learning_manifest

    store = LocalArtifactStore(root_uri=artifacts.storage_root_uri)
    dataset = await SceneOpsDataset.open(
        learning_manifest=manifest,
        learning_manifest_checksum=artifacts.learning_manifest_checksum,
        artifact_store=store,
    )

    refs = dataset.episodes()[:sample_size]
    io_seconds = 0.0
    reconstruction_seconds = 0.0
    cached_metadata: dict[str, object] = {}

    for ref in refs:
        steps_shard, steps_member = dataset._shard_lookup["learning_steps"][ref]
        signals_shard, signals_member = dataset._shard_lookup["learning_signals"][ref]

        start = time.perf_counter()
        steps_table, steps_meta = await read_episode_row_group(
            store,
            steps_shard.uri,
            steps_shard.size_bytes,
            steps_member.row_group_index,
            cached_metadata=cached_metadata.get(steps_shard.uri),
        )
        cached_metadata[steps_shard.uri] = steps_meta
        signals_table, signals_meta = await read_episode_row_group(
            store,
            signals_shard.uri,
            signals_shard.size_bytes,
            signals_member.row_group_index,
            cached_metadata=cached_metadata.get(signals_shard.uri),
        )
        cached_metadata[signals_shard.uri] = signals_meta
        io_seconds += time.perf_counter() - start

        start = time.perf_counter()
        _build_steps_from_tables(
            steps_table,
            signals_table,
            ref=ref,
            expected_step_count=dataset.get_episode(ref).step_count,
        )
        reconstruction_seconds += time.perf_counter() - start

    return {
        "scale": spec.name,
        "sample_size": len(refs),
        "io_plus_decode_seconds_total": round(io_seconds, 6),
        "python_reconstruction_seconds_total": round(reconstruction_seconds, 6),
        "io_plus_decode_seconds_per_episode": round(io_seconds / len(refs), 6),
        "python_reconstruction_seconds_per_episode": round(
            reconstruction_seconds / len(refs), 6
        ),
        "reconstruction_fraction_of_total": round(
            reconstruction_seconds / (io_seconds + reconstruction_seconds), 4
        ),
    }


async def _main_async(scale_names: list[str]) -> dict:
    ladder_by_name = {spec.name: spec for spec in SCALE_LADDER}
    unknown = [name for name in scale_names if name not in ladder_by_name]
    if unknown:
        raise SystemExit(
            f"unknown scale(s) {unknown}; choose from {sorted(ladder_by_name)}"
        )

    tracemalloc.start()
    scale_reports = []
    with tempfile.TemporaryDirectory(prefix="sceneops-bench-5.6-") as tmp_root_str:
        tmp_root = Path(tmp_root_str)
        for name in scale_names:
            spec = ladder_by_name[name]
            print(
                f"--- running scale={spec.name} "
                f"(episodes={spec.num_episodes} steps/ep={spec.steps_per_episode}) ---",
                file=sys.stderr,
            )
            scale_reports.append(await _run_scale(spec, tmp_root))

        reconstruction_reports = []
        for name in scale_names[-2:]:  # largest two scales only -- expensive
            spec = ladder_by_name[name]
            print(f"--- reconstruction isolation scale={spec.name} ---", file=sys.stderr)
            reconstruction_reports.append(
                await _reconstruction_isolation(spec, tmp_root)
            )
    tracemalloc.stop()
    return {"scales": scale_reports, "reconstruction_isolation": reconstruction_reports}


def _print_summary(report: dict) -> None:
    for scale_report in report["scales"]:
        print(f"\n=== scale={scale_report['scale']} ===")
        print(f"  build_seconds        : {scale_report['build_seconds']}")
        print(f"  object_count         : {scale_report['object_count']}")
        print(f"  shard_counts         : {scale_report['shard_counts']}")
        print(f"  total_storage_bytes  : {scale_report['total_storage_bytes']}")
        print(
            f"  incremental (+{scale_report['incremental_delta_episode_count']} eps): "
            f"reused_shards={scale_report['incremental_reused_shard_count']} "
            f"new_shards={scale_report['incremental_new_shard_count']}"
        )
        for phase_name, phase in scale_report["phases"].items():
            print(
                f"  {phase_name:45s} wall={phase['wall_seconds']:>10.6f}s "
                f"io={phase['io_wall_seconds']:>10.6f}s "
                f"recon={phase['reconstruction_seconds']:>10.6f}s"
            )
        print("  io deltas:")
        for key, val in scale_report["io"].items():
            print(f"    {key:45s} {val}")
        print(
            "  ru_maxrss_bytes_after_scale: "
            f"{scale_report['ru_maxrss_bytes_after_scale']}"
        )

    print("\n=== Python reconstruction isolation ===")
    for r in report["reconstruction_isolation"]:
        print(
            f"  scale={r['scale']:8s} sample={r['sample_size']:4d} "
            f"io/ep={r['io_plus_decode_seconds_per_episode']:.6f}s "
            f"recon/ep={r['python_reconstruction_seconds_per_episode']:.6f}s "
            f"recon_fraction={r['reconstruction_fraction_of_total']:.2%}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scales",
        default=",".join(spec.name for spec in SCALE_LADDER),
        help="comma-separated scale names to run (default: all, including xlarge)",
    )
    parser.add_argument("--out", default=None, help="write full JSON report to this path")
    args = parser.parse_args()

    scale_names = [s.strip() for s in args.scales.split(",") if s.strip()]
    report = asyncio.run(_main_async(scale_names))
    _print_summary(report)

    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2))
        print(f"\nfull report written to {args.out}")


if __name__ == "__main__":
    main()
