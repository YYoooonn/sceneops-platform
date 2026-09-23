#!/usr/bin/env python
"""Build a real IntegrationRequest JSON for the LeRobot integration
container's smoke test (SceneOps V2 Request 4.2 §7) -- Step 1 of
scripts/e2e/lerobot_container_smoke.sh.

Runs in the MAIN SceneOps workspace venv (needs sceneops-db to query real
Postgres via ensure_e2e_fixture) -- deliberately NOT inside the isolated
LeRobot container, which must stay DB-free (SceneOps V2 Request 4.2 §4).
Reuses e2e_fixture_bootstrap.ensure_e2e_fixture (SceneOps V2 Request 3.2C)
completely unchanged, exactly like scripts/e2e/e2e_lerobot_resolve.py.

Prints one IntegrationRequest (sceneops_core.integration_runtime, Request
4.1/4.1A) as JSON to stdout; the container reads it directly via
--request-file, with no parsed manifest object or DB handle crossing the
process boundary -- only the resolved learning manifest's uri+checksum,
exactly like the two-process LeRobot round-trip E2E's own Step 1/Step 2
split (scripts/e2e/e2e_lerobot_roundtrip.sh).

Usage:
    uv run python scripts/e2e/lerobot_container_build_request.py \\
        --export-root-uri /data/runs/lerobot-container-smoke/test-e2e-lerobot-container-smoke \\
        --repo-id test-e2e-lerobot-container-smoke

Env overrides -- identical to scripts/e2e/e2e_lerobot_resolve.py:
    SCENEOPS_DATABASE_URL   real Postgres DSN
    MINIO_ENDPOINT_URL      (default: http://localhost:9000)
    MINIO_ROOT_USER         (default: minioadmin)
    MINIO_ROOT_PASSWORD     (default: minioadmin)
    MINIO_BUCKET            (default: sceneops)
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from e2e_fixture_bootstrap import (
    FixtureConflictError,
    FixtureVerificationError,
    ensure_e2e_fixture,
)
from sceneops_analytics.external_adapters import (
    ExternalExportConfig,
    UnsupportedSemanticPolicy,
)
from sceneops_analytics.testing.interop_dataset import INTEROP_FEATURE_PROJECTION
from sceneops_core.artifacts.schemas import ArtifactKind, ArtifactRef
from sceneops_core.config import ArtifactBackend, ArtifactSettings
from sceneops_core.datasets.schemas.external import ExternalDatasetRef
from sceneops_core.integration_runtime import (
    CanonicalDatasetRef,
    IntegrationOperation,
    IntegrationRequest,
)
from sceneops_db.session import async_session_scope, dispose_async_engine
from sceneops_storage import S3ArtifactStore


def _build_artifact_store() -> tuple[S3ArtifactStore, str]:
    bucket = os.environ.get("MINIO_BUCKET", "sceneops")
    settings = ArtifactSettings(
        backend=ArtifactBackend.MINIO,
        root_uri=f"s3://{bucket}/artifacts",
        endpoint_url=os.environ.get("MINIO_ENDPOINT_URL", "http://localhost:9000"),
        access_key_id=os.environ.get("MINIO_ROOT_USER", "minioadmin"),
        secret_access_key=os.environ.get("MINIO_ROOT_PASSWORD", "minioadmin"),
    )
    return S3ArtifactStore(settings=settings), settings.analytics_root_uri


async def _run(*, export_root_uri: str, repo_id: str) -> int:
    artifact_store, analytics_root_uri = _build_artifact_store()

    async with async_session_scope() as session:
        try:
            [result] = await ensure_e2e_fixture(
                "interop",
                session=session,
                artifact_store=artifact_store,
                analytics_root_uri=analytics_root_uri,
            )
        except FixtureConflictError as exc:
            print(f"❌ interop fixture conflict: {exc}", file=sys.stderr)
            return 1
        except FixtureVerificationError as exc:
            print(f"❌ interop fixture verification failed: {exc}", file=sys.stderr)
            return 1

    if not result.learning_manifest_uri or not result.learning_manifest_checksum:
        print(
            "❌ ensure_e2e_fixture('interop', ...) returned no manifest "
            f"uri/checksum -- cannot proceed: {result.to_json_dict()}",
            file=sys.stderr,
        )
        return 1

    request = IntegrationRequest(
        operation=IntegrationOperation.EXPORT,
        external_ref=ExternalDatasetRef(
            format="lerobot",
            format_version="3.0",
            uri=export_root_uri,
            external_name=repo_id,
        ),
        canonical_ref=CanonicalDatasetRef(
            dataset_id=result.dataset_id, dataset_version=result.dataset_version
        ),
        canonical_inputs={
            "learning_manifest": ArtifactRef(
                kind=ArtifactKind.LEARNING_DATA_EXPORT_MANIFEST,
                uri=result.learning_manifest_uri,
                checksum=result.learning_manifest_checksum,
            )
        },
        config=ExternalExportConfig(
            projection=INTEROP_FEATURE_PROJECTION,
            unsupported_semantic_policy=UnsupportedSemanticPolicy.RECORD,
        ).model_dump(mode="json"),
        metadata={"requested_by": "lerobot_container_smoke"},
    )

    print(f"✅ interop fixture ready: {result.detail}", file=sys.stderr)
    print(request.model_dump_json(by_alias=True, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--export-root-uri",
        required=True,
        help="Export target path as seen INSIDE the container "
        "(e.g. /data/runs/.../<repo-id>).",
    )
    parser.add_argument("--repo-id", required=True)
    args = parser.parse_args()

    async def _main() -> int:
        try:
            return await _run(
                export_root_uri=args.export_root_uri, repo_id=args.repo_id
            )
        finally:
            await dispose_async_engine()

    return asyncio.run(_main())


if __name__ == "__main__":
    raise SystemExit(main())
