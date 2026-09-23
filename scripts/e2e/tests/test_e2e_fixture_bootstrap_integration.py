"""Live-infrastructure integration test for the persistent E2E fixture
bootstrap (SceneOps V2 Request 3.2C, hardened by Request 3.2C.1). Requires
SCENEOPS_DATABASE_URL and the MinIO env vars pointing at a real, migrated
Postgres + reachable MinIO -- `make test-integration` sets these against a
running `make local-up` stack. Skips (not fails) if either is unreachable,
matching packages/sceneops-db/tests/conftest.py and
packages/sceneops-storage/tests/conftest.py's own convention.

Deliberately does NOT roll back or clean up afterward: bootstrapping
test-e2e-core/test-e2e-interop/test-e2e-raw-log here IS the intended
persistent deliverable (shared fixtures other E2Es/tests can rely on
already existing), not test pollution -- see
docs/development/local-development.md's E2E fixture catalog section. A
second run of this same test is itself the idempotency proof: it must
reuse and re-verify, never duplicate, what a prior run (test or
`make e2e-bootstrap`) already persisted.

No conftest.py here on purpose -- this test directory (like
sceneops-db/sceneops-storage's) has no __init__.py, so a same-named
"tests/conftest.py" would collide under --import-mode=importlib when
`make test-integration` collects all test directories in one run. Fixtures
live directly in this module instead; a sys.path insertion (not a
conftest.py) makes the sibling e2e_fixture_bootstrap module importable,
since scripts/e2e is not an installed package.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text

_SCRIPTS_E2E_DIR = Path(__file__).resolve().parents[1]
if str(_SCRIPTS_E2E_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_E2E_DIR))

from e2e_fixture_bootstrap import (  # noqa: E402
    CORE_DATASET_ID,
    CORE_DATASET_VERSION,
    INTEROP_DATASET_ID,
    INTEROP_DATASET_VERSION,
    RAW_LOG_DATASET_ID,
    RAW_LOG_DATASET_VERSION,
    bootstrap_e2e_fixtures,
    verify_e2e_fixture,
)
from sceneops_core.config import ArtifactBackend, ArtifactSettings  # noqa: E402
from sceneops_db.session import (  # noqa: E402
    async_session_scope,
    dispose_async_engine,
    get_async_engine,
    reset_async_engine_cache,
)
from sceneops_storage.backends.s3 import S3ArtifactStore  # noqa: E402

BUCKET = os.environ.get("MINIO_BUCKET", "sceneops")


@pytest_asyncio.fixture(autouse=True)
async def _require_real_postgres():
    if not os.environ.get("SCENEOPS_DATABASE_URL"):
        pytest.skip(
            "SCENEOPS_DATABASE_URL not set -- run via `make test-integration` "
            "against a running `make local-up` stack."
        )
    reset_async_engine_cache()
    try:
        engine = get_async_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 -- report as a skip, not a failure
        pytest.skip(f"Postgres not reachable at SCENEOPS_DATABASE_URL: {exc}")

    yield

    await dispose_async_engine()


def _artifact_store() -> tuple[S3ArtifactStore, str]:
    settings = ArtifactSettings(
        backend=ArtifactBackend.MINIO,
        root_uri=f"s3://{BUCKET}/artifacts",
        endpoint_url=os.environ.get("MINIO_ENDPOINT_URL", "http://localhost:9000"),
        access_key_id=os.environ.get("MINIO_ROOT_USER", "minioadmin"),
        secret_access_key=os.environ.get("MINIO_ROOT_PASSWORD", "minioadmin"),
    )
    return S3ArtifactStore(settings=settings), settings.analytics_root_uri


async def _require_real_minio(artifact_store: S3ArtifactStore) -> None:
    try:
        await artifact_store.exists(f"s3://{BUCKET}/artifacts/_e2e_bootstrap_probe")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"MinIO not reachable: {exc}")


async def test_interop_bootstrap_idempotent_and_verifiable_against_real_infra():
    artifact_store, analytics_root_uri = _artifact_store()
    await _require_real_minio(artifact_store)

    async with async_session_scope() as session:
        [first] = await bootstrap_e2e_fixtures(
            "interop",
            session=session,
            artifact_store=artifact_store,
            analytics_root_uri=analytics_root_uri,
        )
        await session.commit()

    assert first.dataset_id == INTEROP_DATASET_ID
    assert first.dataset_version == INTEROP_DATASET_VERSION
    assert first.learning_manifest_artifact_id is not None
    assert first.episode_refs is not None
    assert len(first.episode_refs) == 3

    # Idempotency + strengthened reuse semantics (SceneOps V2 Request
    # 3.2C.1 §2): a second bootstrap call (this run, or a prior one) must
    # independently re-verify, then reuse -- never duplicate -- the
    # persisted fixture. A successful return here already proves
    # verification passed internally.
    async with async_session_scope() as session:
        [second] = await bootstrap_e2e_fixtures(
            "interop",
            session=session,
            artifact_store=artifact_store,
            analytics_root_uri=analytics_root_uri,
        )
        await session.commit()

    assert second.created is False
    assert second.learning_manifest_artifact_id == first.learning_manifest_artifact_id
    assert second.learning_manifest_checksum == first.learning_manifest_checksum
    assert second.learning_manifest_uri == first.learning_manifest_uri

    # A standalone verify call must independently agree -- opens the real
    # persisted snapshot through a real SceneOpsDataset and matches
    # Request 3.2's frozen golden expectations.
    async with async_session_scope() as session:
        [verification] = await verify_e2e_fixture(
            "interop", session=session, artifact_store=artifact_store
        )

    assert verification.ok, verification.errors
    assert verification.errors == []
    assert verification.checks == [
        "dataset_version_exists",
        "manifest_artifact_exists",
        "manifest_checksum_matches",
        "table_checksums_match",
        "sceneops_dataset_opened",
        "episode_refs_match",
        "step_counts_timestamps_features_match",
    ]


async def test_core_and_raw_log_bootstrap_idempotent_and_verifiable():
    artifact_store, analytics_root_uri = _artifact_store()

    async with async_session_scope() as session:
        first = await bootstrap_e2e_fixtures(
            "core",
            session=session,
            artifact_store=artifact_store,
            analytics_root_uri=analytics_root_uri,
        )
        await session.commit()
    async with async_session_scope() as session:
        second = await bootstrap_e2e_fixtures(
            "raw-log",
            session=session,
            artifact_store=artifact_store,
            analytics_root_uri=analytics_root_uri,
        )
        await session.commit()

    assert first[0].dataset_id == CORE_DATASET_ID
    assert first[0].dataset_version == CORE_DATASET_VERSION
    assert second[0].dataset_id == RAW_LOG_DATASET_ID
    assert second[0].dataset_version == RAW_LOG_DATASET_VERSION
    assert first[0].dataset_id != second[0].dataset_id

    async with async_session_scope() as session:
        core_verify = await verify_e2e_fixture(
            "core", session=session, artifact_store=artifact_store
        )
    async with async_session_scope() as session:
        raw_log_verify = await verify_e2e_fixture(
            "raw-log", session=session, artifact_store=artifact_store
        )

    assert core_verify[0].ok, core_verify[0].errors
    assert raw_log_verify[0].ok, raw_log_verify[0].errors
