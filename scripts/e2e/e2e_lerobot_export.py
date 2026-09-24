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

The golden verification itself (``verify_export_report``/
``verify_official_readback``, Request 3.4 §4-§7/§11) lives in
``scripts/e2e/lerobot_roundtrip_golden.py`` -- shared unchanged with
``scripts/e2e/e2e_lerobot_container_verify.py`` (SceneOps V2 Request 4.3),
which checks an ``IntegrationResult`` the LeRobot integration container
produced against the exact same expectations, never a second copy of them.
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

from lerobot_roundtrip_golden import (
    RoundtripAssertionError,
    check,
    verify_export_report,
    verify_official_readback,
)
from sceneops_analytics.external_adapters import (
    ExternalExportConfig,
    UnsupportedSemanticPolicy,
)
from sceneops_analytics.external_adapters.lerobot import LeRobotDatasetAdapter
from sceneops_analytics.learning_dataset import SceneOpsDataset
from sceneops_analytics.testing.interop_dataset import INTEROP_FEATURE_PROJECTION
from sceneops_core.episodes.learning_export import LearningDataExportManifest
from sceneops_core.config import ArtifactBackend, ArtifactSettings
from sceneops_storage import S3ArtifactStore

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "data" / "runs" / "e2e-lerobot"
DEFAULT_REPO_ID = "test-e2e-lerobot-interop"


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
    check(
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
    verify_export_report(report)
    print("  OK", file=sys.stderr)

    print("--- 4. Reopen with official LeRobot reader + verify ---", file=sys.stderr)
    summary = verify_official_readback(export_root, repo_id=args.repo_id)
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
