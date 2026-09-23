#!/usr/bin/env python
"""LeRobot round-trip E2E, Step 2 (SceneOps V2 Request 3.4): open the real
persistent "interop" SceneOpsDataset (resolved by
scripts/e2e/e2e_lerobot_resolve.py, Step 1), export it through
LeRobotDatasetAdapter, reopen the result with LeRobot's own official
reader, and compare every claim (structure, features, task, FPS/timestamps,
episode revisions, semantic-loss classification) against the frozen
Request 3.2 golden expectations
(sceneops_analytics.testing.interop_dataset).

Runs ONLY inside tools/lerobot-integration's isolated venv (SceneOps V2
Request 3.3A) -- this is the reason Step 1/Step 2 are two separate
scripts/processes: this one imports ``lerobot`` and
``sceneops_analytics.external_adapters.lerobot``, neither available (nor
wanted) in the main workspace venv, and deliberately never imports
``sceneops_db`` (not installed here, and must never become a dependency of
this isolated environment).

Takes the manifest's real uri+checksum as CLI args (produced by Step 1) and
independently re-fetches+re-verifies the manifest bytes itself via a real
``S3ArtifactStore`` pointed at the same MinIO -- this script never receives
a parsed manifest object or a LocalArtifactStore fixture from Step 1, only
those two strings, so the entire "open a real, persistent SceneOpsDataset"
path (SceneOps V2 Request 3.4 §2) genuinely happens through real
infrastructure in this process, not something Step 1 did on its behalf.

Usage (always from within tools/lerobot-integration/, see
scripts/e2e/e2e_lerobot_roundtrip.sh):
    uv run python ../../scripts/e2e/e2e_lerobot_export.py \\
        --manifest-uri s3://... --manifest-checksum sha256:...

Env overrides -- identical to scripts/e2e/e2e_lerobot_resolve.py:
    MINIO_ENDPOINT_URL, MINIO_ROOT_USER, MINIO_ROOT_PASSWORD, MINIO_BUCKET

Output location (SceneOps V2 Request 3.4 §3): <repo_root>/data/runs/
e2e-lerobot/<repo-id>/ -- data/ is entirely gitignored and already the
platform's convention for disposable run output (makefiles/cleanup.mk's
``clean-artifacts``). This script owns that one exact directory: it is
removed (never anything broader) at the start of every run for
idempotency, matching LeRobotDataset.create()'s own requirement that its
target directory not already exist. The exported LeRobot dataset is never
registered as a SceneOps Dataset/DatasetVersion, never gets an
ArtifactRecord, and no production DB entity is created for it.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import sys
import traceback
from pathlib import Path

import numpy as np
from lerobot.datasets.lerobot_dataset import LeRobotDataset as OfficialLeRobotDataset

from sceneops_analytics.external_adapters import (
    ExternalExportConfig,
    MappingKind,
    SemanticField,
    UnsupportedSemanticPolicy,
)
from sceneops_analytics.external_adapters.lerobot import LeRobotDatasetAdapter
from sceneops_analytics.learning_dataset import SceneOpsDataset
from sceneops_analytics.testing.interop_dataset import (
    EPISODE_A_REV1_REF,
    EPISODE_A_REV2_REF,
    EPISODE_B_REF,
    INTEROP_FEATURE_PROJECTION,
    compute_expected_interop_episodes,
)
from sceneops_core.episodes.learning_export import LearningDataExportManifest
from sceneops_core.config import ArtifactBackend, ArtifactSettings
from sceneops_storage import S3ArtifactStore

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "data" / "runs" / "e2e-lerobot"
DEFAULT_REPO_ID = "test-e2e-lerobot-interop"

# The frozen Request 3.3 semantic-loss classification this E2E must observe
# in the real report -- literal SemanticField/MappingKind values (the
# existing enums/contracts), never a re-derivation of *why* each field maps
# that way (that logic lives, and stays, in LeRobotDatasetAdapter.
# semantic_capabilities()). See adapter.py's own capability comments for
# the reasoning behind each entry.
EXPECTED_LOSSY_OR_UNSUPPORTED: dict[SemanticField, MappingKind] = {
    SemanticField.EPISODE_IDENTITY: MappingKind.LOSSY_EXPLICIT,
    SemanticField.TIMESTAMPS: MappingKind.LOSSY_EXPLICIT,
    SemanticField.TASK_OUTCOME_METADATA: MappingKind.LOSSY_EXPLICIT,
    SemanticField.SIGNAL_STATUS: MappingKind.UNSUPPORTED,
    SemanticField.SOURCE_REVISION_TRACEABILITY: MappingKind.LOSSY_EXPLICIT,
}
# LOSSLESS fields are never reported on ExternalExportReport.semantic_losses
# at all (ExternalDatasetAdapter._resolve_semantic_losses, Request 3.1) --
# their absence from the report IS the lossless claim.
EXPECTED_LOSSLESS_FIELDS: set[SemanticField] = {
    SemanticField.STEP_ORDERING,
    SemanticField.OBSERVATION_ACTION_NAMESPACE,
    SemanticField.FEATURE_ORDERING,
}

EXPECTED_EPISODE_ORDER = [EPISODE_A_REV1_REF, EPISODE_A_REV2_REF, EPISODE_B_REF]


class RoundtripAssertionError(AssertionError):
    """A round-trip claim did not hold (SceneOps V2 Request 3.4 §7) --
    always caught at the top level and reported as a clear, single-line
    failure with a non-zero exit code, never a silent pass or a bare
    traceback."""


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise RoundtripAssertionError(message)


def _sha256_prefixed(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _build_artifact_store() -> S3ArtifactStore:
    bucket = os.environ.get("MINIO_BUCKET", "sceneops")
    settings = ArtifactSettings(
        backend=ArtifactBackend.MINIO,
        root_uri=f"s3://{bucket}/artifacts",
        endpoint_url=os.environ.get("MINIO_ENDPOINT_URL", "http://localhost:9000"),
        access_key_id=os.environ.get("MINIO_ROOT_USER", "minioadmin"),
        secret_access_key=os.environ.get("MINIO_ROOT_PASSWORD", "minioadmin"),
    )
    return S3ArtifactStore(settings=settings)


async def _open_persistent_interop_dataset(
    *, manifest_uri: str, manifest_checksum: str, artifact_store: S3ArtifactStore
) -> SceneOpsDataset:
    """SceneOps V2 Request 3.4 §2: open the real, persistent SceneOpsDataset
    through the normal SceneOpsDataset.open() path -- never a
    LocalArtifactStore/in-memory fixture. ``manifest_uri``/
    ``manifest_checksum`` are exactly what Step 1
    (e2e_lerobot_resolve.py, running in the main workspace venv with real
    Postgres access) resolved from the real ArtifactRecord; this process
    independently re-fetches and re-verifies the manifest bytes itself."""
    manifest_bytes = await artifact_store.read_bytes(manifest_uri)
    actual_checksum = _sha256_prefixed(manifest_bytes)
    _check(
        actual_checksum == manifest_checksum,
        f"manifest checksum mismatch for {manifest_uri}: "
        f"expected={manifest_checksum} actual={actual_checksum} "
        "(persisted fixture is corrupted)",
    )

    manifest = LearningDataExportManifest.model_validate(json.loads(manifest_bytes))
    return await SceneOpsDataset.open(
        learning_manifest=manifest,
        learning_manifest_checksum=manifest_checksum,
        artifact_store=artifact_store,
    )


def _verify_export_report(report) -> None:
    """SceneOps V2 Request 3.4 §4/§5/§11: everything checkable from
    ExternalExportReport alone, before ever touching the LeRobot output on
    disk."""
    _check(
        report.exported_episode_count == 3,
        f"exported_episode_count={report.exported_episode_count}, expected 3",
    )
    _check(
        report.exported_step_count == 22,
        f"exported_step_count={report.exported_step_count}, expected 22 (8+8+6)",
    )
    _check(
        report.source_episode_refs == EXPECTED_EPISODE_ORDER,
        f"source_episode_refs={report.source_episode_refs!r}, expected "
        f"{EXPECTED_EPISODE_ORDER!r} -- the two 'ep-a' revisions must both "
        "appear, in canonical (episode_id, checksum)-sorted order",
    )
    _check(report.feature_schema is not None, "feature_schema is None")
    _check(
        report.feature_schema.observation_dim == 7,
        f"observation_dim={report.feature_schema.observation_dim}, expected 7",
    )
    _check(
        report.feature_schema.action_dim == 4,
        f"action_dim={report.feature_schema.action_dim}, expected 4",
    )

    losses_by_field = {loss.field: loss.mapping for loss in report.semantic_losses}
    for field, expected_mapping in EXPECTED_LOSSY_OR_UNSUPPORTED.items():
        _check(
            field in losses_by_field,
            f"semantic_losses is missing expected entry for {field.value!r}",
        )
        _check(
            losses_by_field[field] == expected_mapping,
            f"semantic_losses[{field.value!r}]={losses_by_field[field]!r}, "
            f"expected {expected_mapping!r}",
        )
    for field in EXPECTED_LOSSLESS_FIELDS:
        _check(
            field not in losses_by_field,
            f"semantic_losses unexpectedly reports {field.value!r} "
            "(LOSSLESS fields must never appear)",
        )


def _verify_official_readback(root: Path, *, repo_id: str) -> dict:
    """SceneOps V2 Request 3.4 §6: read back exclusively through LeRobot's
    own official ``LeRobotDataset`` API -- never by hand-parsing its
    Parquet/metadata files. Filesystem inspection (below, in the returned
    summary dict) is diagnostic only, never load-bearing for an
    assertion."""
    ds = OfficialLeRobotDataset(repo_id=repo_id, root=root)

    _check(
        ds.meta.total_episodes == 3,
        f"total_episodes={ds.meta.total_episodes}, expected 3",
    )
    _check(len(ds) == 22, f"len(dataset)={len(ds)}, expected 22")
    _check(
        ds.meta.fps == 10, f"fps={ds.meta.fps}, expected 10 (interop fixture's 10.0 Hz)"
    )
    _check(
        tuple(ds.meta.features["observation.state"]["shape"]) == (7,),
        f"observation.state shape={ds.meta.features['observation.state']['shape']}, expected (7,)",
    )
    _check(
        tuple(ds.meta.features["action"]["shape"]) == (4,),
        f"action shape={ds.meta.features['action']['shape']}, expected (4,)",
    )

    expected_by_ref = compute_expected_interop_episodes()
    expected_in_order = [expected_by_ref[ref] for ref in EXPECTED_EPISODE_ORDER]

    frame_idx = 0
    previous_episode_observation_first_frame: list[float] | None = None
    for episode_index, expected_episode in enumerate(expected_in_order):
        episode_start = frame_idx
        for step_index, expected_step in enumerate(expected_episode.steps):
            item = ds[frame_idx]
            _check(
                item["episode_index"].item() == episode_index,
                f"frame {frame_idx}: episode_index={item['episode_index'].item()}, "
                f"expected {episode_index}",
            )
            _check(
                item["frame_index"].item() == step_index,
                f"frame {frame_idx}: frame_index={item['frame_index'].item()}, "
                f"expected {step_index} (within-episode frame ordering)",
            )

            actual_obs = item["observation.state"].numpy().astype(np.float64).tolist()
            actual_action = item["action"].numpy().astype(np.float64).tolist()
            expected_obs_f32 = (
                np.asarray(expected_step.observation, dtype=np.float32)
                .astype(np.float64)
                .tolist()
            )
            expected_action_f32 = (
                np.asarray(expected_step.action, dtype=np.float32)
                .astype(np.float64)
                .tolist()
            )
            _check(
                np.allclose(actual_obs, expected_obs_f32, rtol=1e-5, atol=1e-5),
                f"frame {frame_idx}: observation.state={actual_obs} != expected "
                f"{expected_obs_f32} (float32-rounded golden value)",
            )
            _check(
                np.allclose(actual_action, expected_action_f32, rtol=1e-5, atol=1e-5),
                f"frame {frame_idx}: action={actual_action} != expected "
                f"{expected_action_f32} (float32-rounded golden value)",
            )

            _check(
                item["task"] == expected_episode.task,
                f"frame {frame_idx}: task={item['task']!r}, expected {expected_episode.task!r}",
            )

            # LeRobot's per-frame "timestamp" is a *relative*, episode-local
            # value derived from frame_index/fps (see writer.py's module
            # docstring) -- it is compared here only against that same
            # relative expectation (step_index / fps), never against
            # SceneOps' absolute timestamp_us, which would misrepresent it
            # as lossless (SceneOps V2 Request 3.4 §4's explicit warning).
            actual_timestamp = item["timestamp"].item()
            expected_timestamp = step_index / ds.meta.fps
            _check(
                abs(actual_timestamp - expected_timestamp) < 1e-4,
                f"frame {frame_idx}: timestamp={actual_timestamp}, expected "
                f"~{expected_timestamp} (step_index/fps)",
            )

            if step_index == 0:
                if episode_index == 1:
                    # The second "ep-a" revision must be real, distinct
                    # content from the first -- not a coincidentally-equal
                    # duplicate (SceneOps V2 Request 3.4 §4 episode-revision
                    # check).
                    _check(
                        previous_episode_observation_first_frame != actual_obs,
                        "EPISODE_A_REV1 and EPISODE_A_REV2 have identical "
                        "first-frame observation.state -- revisions are not "
                        "actually distinguishable in the exported dataset",
                    )
                previous_episode_observation_first_frame = actual_obs

            frame_idx += 1

        _check(
            frame_idx - episode_start == expected_episode.step_count,
            f"episode_index={episode_index}: wrote "
            f"{frame_idx - episode_start} frames, expected "
            f"{expected_episode.step_count}",
        )

    return {
        "total_episodes": ds.meta.total_episodes,
        "total_frames": len(ds),
        "fps": ds.meta.fps,
        "observation_state_shape": list(ds.meta.features["observation.state"]["shape"]),
        "action_shape": list(ds.meta.features["action"]["shape"]),
        # Diagnostic only, not load-bearing for any assertion above.
        "on_disk_files": sorted(
            str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()
        ),
    }


async def _run(args: argparse.Namespace) -> int:
    output_root = Path(args.output_dir) if args.output_dir else DEFAULT_OUTPUT_ROOT
    export_root = output_root / args.repo_id

    print("--- 1. Open real persistent SceneOpsDataset ---", file=sys.stderr)
    artifact_store = _build_artifact_store()
    dataset = await _open_persistent_interop_dataset(
        manifest_uri=args.manifest_uri,
        manifest_checksum=args.manifest_checksum,
        artifact_store=artifact_store,
    )
    print(f"  episodes={len(dataset)}", file=sys.stderr)

    print(
        f"--- 2. Export via LeRobotDatasetAdapter -> {export_root} ---", file=sys.stderr
    )
    # Cleanup rule (SceneOps V2 Request 3.4 §3): only this one known
    # test-owned directory is ever removed, and only at the start of a run
    # (LeRobotDataset.create() requires the target not already exist).
    shutil.rmtree(export_root, ignore_errors=True)
    export_root.parent.mkdir(parents=True, exist_ok=True)

    adapter = LeRobotDatasetAdapter(repo_id=args.repo_id, root=export_root)
    config = ExternalExportConfig(
        projection=INTEROP_FEATURE_PROJECTION,
        unsupported_semantic_policy=UnsupportedSemanticPolicy.RECORD,
    )
    report = await adapter.export(dataset, config)
    print(
        f"  exported_episode_count={report.exported_episode_count} "
        f"exported_step_count={report.exported_step_count}",
        file=sys.stderr,
    )

    print("--- 3. Verify ExternalExportReport ---", file=sys.stderr)
    _verify_export_report(report)
    print("  OK", file=sys.stderr)

    print("--- 4. Reopen with official LeRobot reader + verify ---", file=sys.stderr)
    summary = _verify_official_readback(export_root, repo_id=args.repo_id)
    print("  OK", file=sys.stderr)

    print(
        json.dumps(
            {
                "ok": True,
                "export_root": str(export_root),
                "dataset_ref": adapter.dataset_ref.model_dump(mode="json"),
                "report": {
                    "exported_episode_count": report.exported_episode_count,
                    "exported_step_count": report.exported_step_count,
                    "source_episode_refs": [
                        {
                            "episode_id": r.episode_id,
                            "aligned_artifact_checksum": r.aligned_artifact_checksum,
                        }
                        for r in report.source_episode_refs
                    ],
                },
                "readback": summary,
            },
            indent=2,
        )
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest-uri", required=True)
    parser.add_argument("--manifest-checksum", required=True)
    parser.add_argument("--repo-id", default=DEFAULT_REPO_ID)
    parser.add_argument(
        "--output-dir",
        default=None,
        help=f"defaults to {DEFAULT_OUTPUT_ROOT}",
    )
    args = parser.parse_args()

    try:
        return asyncio.run(_run(args))
    except RoundtripAssertionError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 -- top-level: always fail clearly, never a bare traceback exit
        traceback.print_exc(file=sys.stderr)
        print(
            f"❌ LeRobot round-trip failed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
