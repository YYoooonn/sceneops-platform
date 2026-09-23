#!/usr/bin/env python
"""Resolve the persisted "interop" E2E fixture's real
LearningDataExportManifest ArtifactRecord (SceneOps V2 Request 3.4) --
Step 1 of the LeRobot round-trip E2E (scripts/e2e/e2e_lerobot_roundtrip.sh).

Runs in the MAIN SceneOps workspace venv (needs sceneops-db to query real
Postgres) -- deliberately NOT in tools/lerobot-integration, which has no
sceneops-db and must stay LeRobot-only (SceneOps V2 Request 3.3A). Prints
the manifest's real uri+checksum as JSON to stdout so the orchestrating
shell script can hand them to Step 2
(scripts/e2e/e2e_lerobot_export.py), which runs entirely inside
tools/lerobot-integration's isolated venv and does the actual LeRobot
export + official read-back + golden comparison.

Reuses e2e_fixture_bootstrap.ensure_e2e_fixture (SceneOps V2 Request 3.2C)
completely unchanged -- never re-derives or duplicates its create/reuse/
verify contract. A successful ``ensure_e2e_fixture("interop", ...)`` call
already returns the manifest's real, already-verified uri/checksum
directly on its FixtureBootstrapResult (see that module's
_bootstrap_interop/_verify_interop), so this script's only job is to call
it and forward those two fields. If the fixture is missing/corrupted,
ensure_e2e_fixture raises FixtureConflictError/FixtureVerificationError --
this script does not attempt to repair or regenerate it beyond that
existing contract, only reports the failure clearly and exits non-zero.

Usage:
    uv run python scripts/e2e/e2e_lerobot_resolve.py

Env overrides -- identical to scripts/e2e/bootstrap_e2e_fixtures.py:
    SCENEOPS_DATABASE_URL   real Postgres DSN
    MINIO_ENDPOINT_URL      (default: http://localhost:9000)
    MINIO_ROOT_USER         (default: minioadmin)
    MINIO_ROOT_PASSWORD     (default: minioadmin)
    MINIO_BUCKET            (default: sceneops)
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

from e2e_fixture_bootstrap import (
    FixtureConflictError,
    FixtureVerificationError,
    ensure_e2e_fixture,
)
from sceneops_core.config import ArtifactBackend, ArtifactSettings
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


async def _run() -> int:
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

    print(f"✅ interop fixture ready: {result.detail}", file=sys.stderr)
    print(
        json.dumps(
            {
                "dataset_id": result.dataset_id,
                "dataset_version": result.dataset_version,
                "manifest_uri": result.learning_manifest_uri,
                "manifest_checksum": result.learning_manifest_checksum,
            }
        )
    )
    return 0


def main() -> int:
    async def _main() -> int:
        try:
            return await _run()
        finally:
            await dispose_async_engine()

    return asyncio.run(_main())


if __name__ == "__main__":
    raise SystemExit(main())
