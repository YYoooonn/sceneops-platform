#!/usr/bin/env python
"""SceneOps V2 Request 5.5: incremental vs full-rebuild write amplification.

Builds a ~1000-episode base export (default scale fixture, single revision
per episode, SceneOps V2 Request 5.2 physical layout), then for each of
four target scenarios:

    +10 new       -- 10 additional episodes, no revisions
    +100 new      -- 100 additional episodes, no revisions
    +10 revisions -- 10 new aligned revisions of already-included episodes
    mixed         -- 10 new episodes AND 10 new revisions together

... plans and executes an incremental export (``plan_incremental_export`` +
``write_incremental_sharded_learning_tables``) and, separately, a full
rebuild over the same final target set (``write_sharded_learning_tables``
from scratch), both against a ``CountingArtifactStore`` so writes are
measured directly rather than inferred from Parquet file sizes on disk.

Reports, per scenario: reused/new/rewritten shard counts, bytes reused vs
newly written, ArtifactStore write() calls, full-rebuild bytes, and the
write-amplification ratio (incremental bytes written / full-rebuild
bytes written) -- the key number this request's design bet on being << 1.

Profiling/reporting tool, not a test: no pass/fail assertions. Not part of
`make test`.

Usage:
    uv run python tools/benchmarks/learning_data/benchmark_incremental_export.py
    uv run python tools/benchmarks/learning_data/benchmark_incremental_export.py --num-episodes 1000
    uv run python tools/benchmarks/learning_data/benchmark_incremental_export.py --out /tmp/report.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "packages" / "sceneops-analytics"))
sys.path.insert(0, str(_REPO_ROOT / "packages" / "sceneops-core"))
sys.path.insert(0, str(_REPO_ROOT / "packages" / "sceneops-storage"))

from sceneops_analytics import (  # noqa: E402
    AnalyticsTableWriter,
    write_incremental_sharded_learning_tables,
    write_sharded_learning_tables,
)
from sceneops_analytics.testing import CountingArtifactStore  # noqa: E402
from sceneops_analytics.testing.scale_fixture import (  # noqa: E402
    ScaleSpec,
    build_scaled_entries,
    write_scaled_dataset_artifacts,
)
from sceneops_core.episodes.learning_export import (  # noqa: E402
    AlignedArtifactRevision,
    default_shard_policy,
    learning_data_export_id,
    plan_incremental_export,
)
from sceneops_core.episodes.learning_export.schemas import (  # noqa: E402
    LearningDataExportConfig,
)
from sceneops_storage import LocalArtifactStore  # noqa: E402

_POLICY = default_shard_policy()


def _base_spec(num_episodes: int) -> ScaleSpec:
    return ScaleSpec(
        name="incr-base",
        num_episodes=num_episodes,
        steps_per_episode=60,
        num_observation_channels=8,
        num_action_channels=5,
        length_jitter_fraction=0.3,
        extra_revision_every=0,
    )


def _target_entries_new_episodes(num_episodes: int, extra: int):
    spec = _base_spec(num_episodes + extra)
    return build_scaled_entries(spec)


def _target_entries_with_revisions(num_episodes: int, revision_every: int):
    spec = ScaleSpec(
        name="incr-target-rev",
        num_episodes=num_episodes,
        steps_per_episode=60,
        num_observation_channels=8,
        num_action_channels=5,
        length_jitter_fraction=0.3,
        extra_revision_every=revision_every,
    )
    return build_scaled_entries(spec)


def _revision_for(checksum: str, artifact) -> AlignedArtifactRevision:
    return AlignedArtifactRevision(
        episode_id=artifact.aligned_episode.episode_id,
        aligned_artifact_id=f"art-{checksum}",
        aligned_artifact_checksum=checksum,
    )


async def _run_scenario(
    *,
    name: str,
    tmp_path: Path,
    base_manifest,
    base_entries,
    target_entries,
) -> dict:
    base_checksums = {checksum for checksum, _ in base_entries}
    delta_entries = [
        (checksum, artifact)
        for checksum, artifact in target_entries
        if checksum not in base_checksums
    ]
    all_revisions = [_revision_for(c, a) for c, a in target_entries]

    plan = plan_incremental_export(base_manifest, all_revisions)

    export_config = LearningDataExportConfig()
    incremental_export_id = learning_data_export_id(
        aligned_checksums=[r.aligned_artifact_checksum for r in all_revisions],
        export_config=export_config,
    )

    incremental_root = str(tmp_path / f"{name}-incremental")
    incremental_store = CountingArtifactStore(LocalArtifactStore(root_uri=incremental_root))
    incremental_writer = AnalyticsTableWriter(
        artifact_store=incremental_store, root_uri=incremental_root
    )
    incremental_shard_index = await write_incremental_sharded_learning_tables(
        incremental_writer,
        dataset_id="bench-incr",
        dataset_version="v1",
        export_id=incremental_export_id,
        delta_entries=delta_entries,
        policy=_POLICY,
        plan=plan,
    )

    full_root = str(tmp_path / f"{name}-full")
    full_store = CountingArtifactStore(LocalArtifactStore(root_uri=full_root))
    full_writer = AnalyticsTableWriter(artifact_store=full_store, root_uri=full_root)
    full_shard_index = await write_sharded_learning_tables(
        full_writer,
        dataset_id="bench-full",
        dataset_version="v1",
        export_id=incremental_export_id,
        entries=target_entries,
        policy=_POLICY,
    )

    reused_shards = len(plan.reused_learning_steps_shards) + len(
        plan.reused_learning_signals_shards
    )
    new_shards = len(incremental_shard_index.learning_steps) + len(
        incremental_shard_index.learning_signals
    ) - reused_shards
    bytes_reused = sum(
        shard.size_bytes for shard in plan.reused_learning_steps_shards
    ) + sum(shard.size_bytes for shard in plan.reused_learning_signals_shards)
    full_rebuild_bytes = sum(
        shard.size_bytes for shard in full_shard_index.learning_steps
    ) + sum(shard.size_bytes for shard in full_shard_index.learning_signals)

    return {
        "scenario": name,
        "base_episode_count": len({r.episode_id for r in base_manifest.inputs}),
        "target_episode_count": len({r.episode_id for r in all_revisions}),
        "delta_entry_count": len(delta_entries),
        "reused_shard_count": reused_shards,
        "new_shard_count": new_shards,
        "rewritten_shard_count": 0,  # append-only strategy never rewrites
        "bytes_reused": bytes_reused,
        "bytes_written_incremental": incremental_store.stats.write_bytes_total,
        "write_calls_incremental": incremental_store.stats.write_bytes_calls,
        "bytes_written_full_rebuild": full_store.stats.write_bytes_total,
        "write_calls_full_rebuild": full_store.stats.write_bytes_calls,
        "full_rebuild_shard_bytes_reference": full_rebuild_bytes,
        "write_amplification_ratio": (
            incremental_store.stats.write_bytes_total / full_store.stats.write_bytes_total
            if full_store.stats.write_bytes_total
            else float("nan")
        ),
    }


async def run(num_episodes: int) -> list[dict]:
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        base_spec = _base_spec(num_episodes)
        base_entries = build_scaled_entries(base_spec)
        base_artifacts = await write_scaled_dataset_artifacts(
            tmp_path / "base", base_spec, shard_policy=_POLICY
        )
        base_manifest = base_artifacts.learning_manifest

        scenarios = [
            (
                "+10 new",
                _target_entries_new_episodes(num_episodes, 10),
            ),
            (
                "+100 new",
                _target_entries_new_episodes(num_episodes, 100),
            ),
            (
                "+10 revisions",
                _target_entries_with_revisions(
                    num_episodes, revision_every=num_episodes // 10
                ),
            ),
        ]
        mixed_new = _target_entries_new_episodes(num_episodes, 10)
        mixed_rev = _target_entries_with_revisions(
            num_episodes, revision_every=num_episodes // 10
        )
        mixed_seen: set[str] = set()
        mixed_entries = []
        for checksum, artifact in [*base_entries, *mixed_new, *mixed_rev]:
            if checksum in mixed_seen:
                continue
            mixed_seen.add(checksum)
            mixed_entries.append((checksum, artifact))
        scenarios.append(("mixed (+10 new, +10 revisions)", mixed_entries))

        results = []
        for name, target_entries in scenarios:
            result = await _run_scenario(
                name=name,
                tmp_path=tmp_path,
                base_manifest=base_manifest,
                base_entries=base_entries,
                target_entries=target_entries,
            )
            results.append(result)
        return results


def _print_report(results: list[dict]) -> None:
    header = (
        f"{'scenario':<32}{'reused':>8}{'new':>6}{'rewrit':>8}"
        f"{'bytes_reused':>14}{'bytes_incr':>12}{'bytes_full':>12}{'amp':>8}"
    )
    print(header)
    print("-" * len(header))
    for r in results:
        print(
            f"{r['scenario']:<32}{r['reused_shard_count']:>8}{r['new_shard_count']:>6}"
            f"{r['rewritten_shard_count']:>8}{r['bytes_reused']:>14}"
            f"{r['bytes_written_incremental']:>12}{r['bytes_written_full_rebuild']:>12}"
            f"{r['write_amplification_ratio']:>8.3f}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--num-episodes", type=int, default=1000)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    results = asyncio.run(run(args.num_episodes))
    _print_report(results)
    if args.out:
        args.out.write_text(json.dumps(results, indent=2))
        print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
