#!/usr/bin/env python
"""Verification of one LearningDataExportManifest (v2-sharded): the manifest
bytes match the pinned checksum, every shard matches its recorded checksum and
row count, and the export reopens through SceneOpsDataset with real reads.

Runs inside the worker image (``make e2e-episode-learning`` invokes it through
``docker compose run worker-cli``), so it reads the ArtifactStore with the
worker's own settings; the host needs neither uv nor object-store access. It
is handed only the manifest ArtifactRecord's uri and checksum, resolved
through the API.

Usage (inside the worker image):
    python /workspace/scripts/e2e/verify_learning_export.py \\
        --manifest-uri s3://sceneops/artifacts/... \\
        --manifest-checksum sha256:... \\
        --expected-episode-count 1 \\
        --open-dataset

Prints one JSON document on stdout, including the dense-projectable channels
actually read (``dataset.projection``).
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys

from sceneops_core.episodes.learning import EpisodeRef, FeatureProjection
from sceneops_core.episodes.learning.errors import NativeLearningDatasetError
from sceneops_core.episodes.learning_export import (
    LEARNING_DATA_LAYOUT_VERSION_SHARDED,
    LearningDataExportManifest,
)
from sceneops_storage import create_artifact_store
from sceneops_worker.config import get_settings

_UNSUPPORTED_CHANNEL_RE = re.compile(r"channel '([^']+)' \((observation|action)\)")


async def _resolve_dense_projectable_projection(
    dataset, ref: EpisodeRef, observation_channels: list[str], action_channels: list[str]
) -> tuple[FeatureProjection, int]:
    """Not every declared observation/action channel is guaranteed
    dense-projectable for a given real Episode -- v1 dense projection only
    supports numeric_scalar/numeric_vector kinds (a quaternion
    `orientation` channel is UnsupportedFeatureKindError), and real
    CAN-derived telemetry can have a channel unresolved at the very first
    aligned step specifically (an association-policy boundary artifact, not
    a whole-episode gap -- see
    packages/sceneops-core/sceneops_core/episodes/learning/projection.py).
    Neither is a bug. Probes the LAST step first (least likely to hit that
    startup artifact) and falls back to the first step, dropping whichever
    channel the error names and retrying, so this acceptance check proves
    *some* real channels round-trip with real resolved values rather than
    hardcoding which of this Episode's channels/steps happen to work.
    Returns (projection, start_step actually used)."""
    obs, act = list(observation_channels), list(action_channels)
    metadata = dataset.get_episode(ref)
    horizon = 1 if metadata.step_count > 0 else 0
    candidate_start_steps = sorted({metadata.step_count - 1, 0}, reverse=True)
    for _ in range(len(obs) + len(act) + 1):
        projection = FeatureProjection(observation_channels=obs, action_channels=act)
        last_exc: NativeLearningDatasetError | None = None
        for start_step in candidate_start_steps:
            try:
                await dataset.get_window(ref, start_step, horizon, projection)
                return projection, start_step
            except NativeLearningDatasetError as exc:
                last_exc = exc
        # Every candidate start step failed for the current channel set --
        # drop whichever channel the (last) error named and retry the outer
        # loop with one fewer channel, same as the single-step version.
        assert last_exc is not None
        match = _UNSUPPORTED_CHANNEL_RE.search(str(last_exc))
        if not match:
            raise last_exc
        channel, namespace = match.group(1), match.group(2)
        if namespace == "observation" and channel in obs:
            obs.remove(channel)
        elif namespace == "action" and channel in act:
            act.remove(channel)
        else:
            raise last_exc
    raise NativeLearningDatasetError(
        "exhausted all channels while looking for a dense-projectable subset"
    )


async def _discover_channels(manifest, store) -> tuple[list[str], list[str]]:
    """The observation and action channels that are numeric (scalar / vector)
    and resolved at every step of every episode, read from every
    ``learning_signals`` shard. Only those can be projected densely without a
    missing-value policy."""
    import io

    import polars as pl

    frames = []
    for shard in manifest.shard_index.learning_signals:
        frames.append(
            pl.read_parquet(io.BytesIO(await store.read_bytes(shard.uri))).select(
                "namespace", "channel", "value_kind", "status"
            )
        )
    signals = pl.concat(frames)
    per_channel = signals.group_by("namespace", "channel").agg(
        (pl.col("status") == "resolved").all().alias("all_resolved"),
        pl.col("value_kind").is_in(["numeric_scalar", "numeric_vector"]).all().alias("numeric"),
    )
    usable = per_channel.filter(pl.col("all_resolved") & pl.col("numeric"))
    channels: dict[str, list[str]] = {"observation": [], "action": []}
    for namespace, channel in usable.select("namespace", "channel").sort("namespace", "channel").iter_rows():
        channels[namespace].append(channel)
    return channels["observation"], channels["action"]


def _sha256_prefixed(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _build_artifact_store():
    return create_artifact_store(get_settings().artifact)


async def _run(args: argparse.Namespace) -> int:
    store = _build_artifact_store()
    errors: list[str] = []
    checks: list[str] = []

    manifest_bytes = await store.read_bytes(args.manifest_uri)
    actual_checksum = _sha256_prefixed(manifest_bytes)
    if actual_checksum != args.manifest_checksum:
        print(
            f"❌ manifest checksum mismatch: recorded={args.manifest_checksum} "
            f"actual={actual_checksum}",
            file=sys.stderr,
        )
        return 1
    checks.append("manifest_checksum_matches")

    manifest = LearningDataExportManifest.model_validate(json.loads(manifest_bytes))

    if manifest.layout_version != LEARNING_DATA_LAYOUT_VERSION_SHARDED:
        errors.append(
            f"layout_version={manifest.layout_version!r}, expected "
            f"{LEARNING_DATA_LAYOUT_VERSION_SHARDED!r}"
        )
    if manifest.episode_count != args.expected_episode_count:
        errors.append(
            f"episode_count={manifest.episode_count}, expected "
            f"{args.expected_episode_count}"
        )
    if len(manifest.inputs) != args.expected_episode_count:
        errors.append(
            f"len(inputs)={len(manifest.inputs)}, expected "
            f"{args.expected_episode_count}"
        )
    if manifest.base_export_id is not None:
        errors.append(f"base_export_id={manifest.base_export_id!r}, expected null")
    if manifest.shard_index is None:
        errors.append("shard_index is missing on a v2-sharded manifest")

    if errors:
        for e in errors:
            print(f"❌ {e}", file=sys.stderr)
        return 1
    checks.append("manifest_fields_match_contract")

    shard_counts: dict[str, int] = {}
    for table_name, shards in (
        ("learning_steps", manifest.shard_index.learning_steps),
        ("learning_signals", manifest.shard_index.learning_signals),
    ):
        shard_counts[table_name] = len(shards)
        for shard in shards:
            shard_bytes = await store.read_bytes(shard.uri)
            actual = _sha256_prefixed(shard_bytes)
            if actual != shard.checksum:
                errors.append(
                    f"{table_name} shard {shard.shard_index} checksum mismatch: "
                    f"recorded={shard.checksum} actual={actual}"
                )
        row_sum = sum(s.row_count for s in shards)
        expected_row_sum = manifest.row_counts.get(table_name)
        if expected_row_sum is not None and row_sum != expected_row_sum:
            errors.append(
                f"{table_name}: sum of shard row_counts={row_sum} != "
                f"manifest.row_counts[{table_name}]={expected_row_sum}"
            )

    if errors:
        for e in errors:
            print(f"❌ {e}", file=sys.stderr)
        return 1
    checks.append("shard_checksums_and_row_counts_match")
    print(f"  shard_counts={shard_counts}", file=sys.stderr)

    dataset_summary: dict | None = None
    if args.open_dataset:
        from sceneops_analytics.learning_dataset import SceneOpsDataset

        dataset = await SceneOpsDataset.open(
            learning_manifest=manifest,
            learning_manifest_checksum=args.manifest_checksum,
            artifact_store=store,
        )
        checks.append("sceneops_dataset_opened")

        refs = dataset.episodes()
        if len(dataset) != args.expected_episode_count:
            print(
                f"❌ SceneOpsDataset len={len(dataset)}, expected "
                f"{args.expected_episode_count}",
                file=sys.stderr,
            )
            return 1
        checks.append("sceneops_dataset_len_matches")

        observation_channels = (
            args.observation_channels.split(",") if args.observation_channels else []
        )
        action_channels = args.action_channels.split(",") if args.action_channels else []
        if not observation_channels and not action_channels:
            observation_channels, action_channels = await _discover_channels(
                manifest, store
            )

        sample_positions = sorted({0, len(refs) // 2, len(refs) - 1})
        sampled: list[dict] = []
        for pos in sample_positions:
            ref: EpisodeRef = refs[pos]
            metadata = dataset.get_episode(ref)
            horizon = min(metadata.step_count, 1)
            # Resolved per-ref, not once and reused -- which real channels
            # are dense-projectable can differ episode to episode (real
            # sensor data; see the helper's own docstring).
            try:
                projection, start_step = await _resolve_dense_projectable_projection(
                    dataset, ref, observation_channels, action_channels
                )
            except NativeLearningDatasetError as exc:
                print(
                    f"❌ episode {ref.episode_id}: could not resolve a "
                    f"dense-projectable FeatureProjection: {exc}",
                    file=sys.stderr,
                )
                return 1
            window = await dataset.get_window(ref, start_step, horizon, projection)
            if len(window.timestamps_us) < 1:
                print(
                    f"❌ episode {ref.episode_id}: expected >=1 learning step, "
                    f"got {len(window.timestamps_us)}",
                    file=sys.stderr,
                )
                return 1
            sampled.append(
                {
                    "position": pos,
                    "episode_id": ref.episode_id,
                    "step_count": metadata.step_count,
                    "sampled_steps": len(window.timestamps_us),
                    "projection": {
                        "observation_channels": list(projection.observation_channels),
                        "action_channels": list(projection.action_channels),
                    },
                    "observation_dims": len(window.observation[0])
                    if window.observation
                    else 0,
                    "action_dims": len(window.action[0]) if window.action else 0,
                }
            )
        checks.append("first_middle_last_episode_reads_ok")
        dataset_summary = {
            "episode_count": len(dataset),
            "step_count": sum(dataset.get_episode(r).step_count for r in refs),
            "dense_projection": {
                "observation_channels": observation_channels,
                "action_channels": action_channels,
            },
            "sampled": sampled,
        }

    print(json.dumps({"ok": True, "checks": checks, "shard_counts": shard_counts, "dataset": dataset_summary}, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest-uri", required=True)
    parser.add_argument("--manifest-checksum", required=True)
    parser.add_argument("--expected-episode-count", type=int, required=True)
    parser.add_argument("--open-dataset", action="store_true")
    parser.add_argument("--observation-channels", default="")
    parser.add_argument("--action-channels", default="")
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
