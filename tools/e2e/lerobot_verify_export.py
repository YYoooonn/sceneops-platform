#!/usr/bin/env python
"""Verify a LeRobot export of a pinned learning data export against the
export it was made from.

Runs inside the LeRobot integration image (``make e2e-episode-learning``
invokes it with ``docker compose run --entrypoint python lerobot-integration``):
that image has the official ``lerobot`` reader and the SceneOps analytics
package, never a database. Three claims are checked:

  1. the container's IntegrationResult names the requested target and
     canonical dataset, and duplicates nothing as an ArtifactRef;
  2. its ExternalExportReport counts exactly the episodes and steps of the
     learning export;
  3. read back through LeRobot's own official ``LeRobotDataset`` API, every
     frame of every episode equals the dense window SceneOpsDataset reads from
     the same export for the same projection (float32-rounded), in the
     export's canonical episode order, with episode-local frame indices.

Usage (inside the image):
    python /workspace/tools/e2e/lerobot_verify_export.py \\
        --result-file /data/runs/.../result.json --export-root /data/runs/.../<repo-id> \\
        --manifest-uri s3://... --manifest-checksum sha256:... \\
        --observation-channels a,b --action-channels c,d
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import numpy as np
from lerobot.datasets.lerobot_dataset import LeRobotDataset

from sceneops_analytics.external_adapters import ExternalExportReport
from sceneops_analytics.external_adapters.lerobot.entrypoint import (
    LeRobotContainerSettings,
)
from sceneops_analytics.learning_dataset import SceneOpsDataset
from sceneops_core.episodes.learning import FeatureProjection
from sceneops_core.episodes.learning_export import LearningDataExportManifest
from sceneops_core.integration_runtime import IntegrationOperation, IntegrationResult
from sceneops_storage import create_artifact_store


class VerificationError(AssertionError):
    pass


def check(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def _channels(value: str) -> list[str]:
    return [c for c in value.split(",") if c]


async def _expected(args: argparse.Namespace, projection: FeatureProjection):
    store = create_artifact_store(LeRobotContainerSettings().artifact)
    manifest = LearningDataExportManifest.model_validate(
        json.loads(await store.read_bytes(args.manifest_uri))
    )
    dataset = await SceneOpsDataset.open(
        learning_manifest=manifest,
        learning_manifest_checksum=args.manifest_checksum,
        artifact_store=store,
    )
    episodes = []
    for ref in dataset.episodes():
        metadata = dataset.get_episode(ref)
        window = await dataset.get_window(ref, 0, metadata.step_count, projection)
        episodes.append((ref, metadata, window))
    return episodes


def _run(args: argparse.Namespace) -> dict:
    result = IntegrationResult.model_validate(json.loads(Path(args.result_file).read_text()))
    check(result.operation is IntegrationOperation.EXPORT, "operation is not export")
    check(result.external_ref.format == "lerobot", "external_ref.format is not lerobot")
    check(
        result.external_ref.uri == args.export_root,
        f"external_ref.uri={result.external_ref.uri!r}, expected {args.export_root!r}",
    )
    check(
        result.produced_artifacts == {},
        "the LeRobot dataset root must never be duplicated as an ArtifactRef",
    )

    projection = FeatureProjection(
        observation_channels=_channels(args.observation_channels),
        action_channels=_channels(args.action_channels),
    )
    episodes = asyncio.run(_expected(args, projection))
    total_steps = sum(m.step_count for _, m, _ in episodes)

    report = ExternalExportReport.model_validate(result.result_metadata)
    check(
        report.exported_episode_count == len(episodes),
        f"exported_episode_count={report.exported_episode_count}, expected {len(episodes)}",
    )
    check(
        report.exported_step_count == total_steps,
        f"exported_step_count={report.exported_step_count}, expected {total_steps}",
    )
    check(
        [r.episode_id for r in report.source_episode_refs]
        == [ref.episode_id for ref, _, _ in episodes],
        "source_episode_refs are not the export's episodes in canonical order",
    )

    repo_id = result.external_ref.external_name or Path(args.export_root).name
    dataset = LeRobotDataset(repo_id=repo_id, root=Path(args.export_root))
    check(
        dataset.meta.total_episodes == len(episodes),
        f"total_episodes={dataset.meta.total_episodes}, expected {len(episodes)}",
    )
    check(len(dataset) == total_steps, f"len(dataset)={len(dataset)}, expected {total_steps}")
    obs_dim = len(episodes[0][2].observation[0])
    action_dim = len(episodes[0][2].action[0])
    check(
        tuple(dataset.meta.features["observation.state"]["shape"]) == (obs_dim,),
        f"observation.state shape {dataset.meta.features['observation.state']['shape']} != ({obs_dim},)",
    )
    check(
        tuple(dataset.meta.features["action"]["shape"]) == (action_dim,),
        f"action shape {dataset.meta.features['action']['shape']} != ({action_dim},)",
    )

    frame = 0
    for episode_index, (ref, metadata, window) in enumerate(episodes):
        for step in range(metadata.step_count):
            item = dataset[frame]
            check(
                item["episode_index"].item() == episode_index,
                f"frame {frame}: episode_index={item['episode_index'].item()}, expected {episode_index}",
            )
            check(
                item["frame_index"].item() == step,
                f"frame {frame}: frame_index={item['frame_index'].item()}, expected {step}",
            )
            for key, expected in (
                ("observation.state", window.observation[step]),
                ("action", window.action[step]),
            ):
                actual = item[key].numpy().astype(np.float64)
                wanted = np.asarray(expected, dtype=np.float32).astype(np.float64)
                check(
                    np.allclose(actual, wanted, rtol=1e-5, atol=1e-5),
                    f"frame {frame}: {key}={actual.tolist()} != export {wanted.tolist()}",
                )
            frame += 1
    return {
        "ok": True,
        "total_episodes": dataset.meta.total_episodes,
        "total_frames": len(dataset),
        "fps": dataset.meta.fps,
        "observation_dim": obs_dim,
        "action_dim": action_dim,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result-file", required=True)
    parser.add_argument("--export-root", required=True)
    parser.add_argument("--manifest-uri", required=True)
    parser.add_argument("--manifest-checksum", required=True)
    parser.add_argument("--observation-channels", default="")
    parser.add_argument("--action-channels", default="")
    args = parser.parse_args()
    try:
        print(json.dumps(_run(args)))
        return 0
    except VerificationError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
