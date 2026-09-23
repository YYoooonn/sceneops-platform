#!/usr/bin/env python
"""Bootstrap/verify the shared E2E fixture catalog (SceneOps V2 Request
3.2C) against real PostgreSQL + MinIO -- so future E2Es can start from
known fixture state instead of recreating ad-hoc datasets independently.

Uses the exact fixture identities frozen in this directory's
e2e_fixture_bootstrap module (which itself mirrors scripts/e2e/lib.sh's
resolve_e2e_fixture, SceneOps V2 Request 3.2B) -- this script is a thin CLI
wrapper: all bootstrap/verify decision logic lives in that module, reusable
directly from Python (e.g. a future E2E's own `ensure_e2e_fixture(...)`
call). Moved here from sceneops_analytics.testing (SceneOps V2 Request
3.2C.1 §1) so the production sceneops-analytics package never needs to
depend on sceneops-db just to support E2E fixture setup.

Usage:
    uv run python scripts/e2e/bootstrap_e2e_fixtures.py --fixture interop --verify
    uv run python scripts/e2e/bootstrap_e2e_fixtures.py --fixture all --json
    uv run python scripts/e2e/bootstrap_e2e_fixtures.py --fixture core --verify-only

Env overrides (normal environment configuration supplies real
credentials -- nothing here is hardcoded):
    SCENEOPS_DATABASE_URL   real Postgres DSN (same variable
                            sceneops-db reads everywhere else). Point this
                            at "localhost", not the container-internal
                            "postgres" hostname, when running this script
                            from the host -- e.g.:
                              postgresql+asyncpg://sceneops:sceneops@localhost:5432/sceneops
    MINIO_ENDPOINT_URL      (default: http://localhost:9000 -- host-side;
                            the container-internal alias is "minio", not
                            usable from a plain host-side `uv run`)
    MINIO_ROOT_USER         (default: minioadmin)
    MINIO_ROOT_PASSWORD     (default: minioadmin)
    MINIO_BUCKET            (default: sceneops)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

from e2e_fixture_bootstrap import (
    FixtureConflictError,
    FixtureVerificationError,
    bootstrap_e2e_fixtures,
    verify_e2e_fixture,
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


def _print_human(title: str, rows: list[dict]) -> None:
    print(f"=== {title} ===")
    for row in rows:
        print(json.dumps(row, indent=2, default=str))
        print()


async def _run(args: argparse.Namespace) -> int:
    artifact_store, analytics_root_uri = _build_artifact_store()
    exit_code = 0

    async with async_session_scope() as session:
        if not args.verify_only:
            try:
                bootstrap_results = await bootstrap_e2e_fixtures(
                    args.fixture,
                    session=session,
                    artifact_store=artifact_store,
                    analytics_root_uri=analytics_root_uri,
                )
            except FixtureConflictError as exc:
                print(f"❌ fixture conflict: {exc}", file=sys.stderr)
                return 1
            except FixtureVerificationError as exc:
                print(f"❌ fixture verification failed: {exc}", file=sys.stderr)
                return 1

            rows = [r.to_json_dict() for r in bootstrap_results]
            if args.json:
                print(json.dumps(rows, indent=2, default=str))
            else:
                _print_human("bootstrap", rows)

        if args.verify or args.verify_only:
            verify_results = await verify_e2e_fixture(
                args.fixture, session=session, artifact_store=artifact_store
            )
            rows = [r.to_json_dict() for r in verify_results]
            if args.json:
                print(json.dumps(rows, indent=2, default=str))
            else:
                _print_human("verify", rows)
            if any(not r.ok for r in verify_results):
                exit_code = 1

    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--fixture",
        choices=["all", "core", "interop", "raw-log"],
        default="all",
        help="Which shared E2E fixture to bootstrap/verify (default: all).",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Also independently verify the fixture after bootstrapping it.",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Skip bootstrap; only verify existing fixture state.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON instead of human-readable output.",
    )
    args = parser.parse_args()

    async def _main() -> int:
        # dispose_async_engine() must run in the SAME event loop that
        # created the engine (a second, separate asyncio.run() call here
        # would try to close asyncpg connections bound to the first,
        # already-closed loop and raise "Event loop is closed").
        try:
            return await _run(args)
        finally:
            await dispose_async_engine()

    return asyncio.run(_main())


if __name__ == "__main__":
    raise SystemExit(main())
