"""Shared fixtures for RobotRun registration tests.

This directory mixes pure-unit tests (test_registration.py -- fakes
only, no infra) with real-Postgres + real-MinIO integration tests
(test_registration_integration.py). None of the infra-reachability
fixtures below are autouse -- only tests/fixtures that actually touch
Postgres or MinIO request them, so the pure-unit tests here are never
gated behind infrastructure availability.
"""

from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from sceneops_core.artifacts.schemas.enums import ArtifactBackend
from sceneops_db.session import (
    dispose_async_engine,
    get_async_engine,
    get_async_sessionmaker,
    reset_async_engine_cache,
)
from sceneops_storage.backends.s3 import S3ArtifactStore
from sceneops_worker.config import WorkerSettings
from sceneops_worker.core import dependencies as dependencies_module
from sceneops_worker.core.dependencies import create_worker_context

_BUCKET = os.environ.get("MINIO_BUCKET", "sceneops")
_TEST_PREFIX = "_test-integration-robot-run-registration"


@pytest.fixture()
def unique_id():
    def _make(prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex[:10]}"

    return _make


@pytest_asyncio.fixture()
async def _fresh_database_connection():
    """Skips (not fails) if Postgres isn't reachable. Requested explicitly
    by db_session and by the concurrency test (which manages its own
    sessions directly, bypassing db_session)."""
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
    except Exception as exc:  # noqa: BLE001 - report as a skip, not a failure
        pytest.skip(f"Postgres not reachable at SCENEOPS_DATABASE_URL: {exc}")

    yield

    await dispose_async_engine()


@pytest_asyncio.fixture()
async def db_session(_fresh_database_connection):
    sessionmaker = get_async_sessionmaker()
    async with sessionmaker() as session:
        try:
            yield session
        finally:
            await session.rollback()
            await session.close()


@pytest.fixture()
def worker_settings(unique_id) -> WorkerSettings:
    """A real WorkerSettings pointed at the local MinIO stack under a
    dedicated, uniquely-prefixed test root -- never the default
    /data/artifacts local backend, and never the shared root real
    RobotRun registrations would use. Never raises on a missing env var
    (falls back to the same local-stack defaults `make local-up` uses) --
    actual reachability is _fresh_database_connection's/
    _minio_reachable's job, so a test can request just this fixture
    without triggering the KeyError a plain os.environ[...] would."""
    return WorkerSettings(
        database_url=os.environ.get(
            "SCENEOPS_DATABASE_URL",
            "postgresql+asyncpg://sceneops:sceneops@localhost:5432/sceneops",
        ),
        artifact={
            "backend": ArtifactBackend.MINIO,
            "root_uri": f"s3://{_BUCKET}/{_TEST_PREFIX}/{unique_id('root')}",
            "endpoint_url": os.environ.get(
                "MINIO_ENDPOINT_URL", "http://localhost:9000"
            ),
            "access_key_id": os.environ.get("MINIO_ROOT_USER", "minioadmin"),
            "secret_access_key": os.environ.get("MINIO_ROOT_PASSWORD", "minioadmin"),
        },
    )


@pytest_asyncio.fixture()
async def _minio_reachable(worker_settings):
    """Skips (not fails) if MinIO isn't reachable. Independent of Postgres
    reachability -- requested explicitly wherever MinIO is touched."""
    probe = S3ArtifactStore(settings=worker_settings.artifact)
    try:
        await probe.exists(f"{worker_settings.artifact_root_uri}/_connectivity_check")
    except Exception as exc:  # noqa: BLE001 - report as a skip, not a failure
        pytest.skip(f"MinIO not reachable: {exc}")


@pytest_asyncio.fixture()
async def worker_context(db_session, worker_settings, _minio_reachable):
    """A real WorkerContext (real Postgres session + real MinIO-backed
    ArtifactStore). create_worker_context() caches its ArtifactStore in a
    process-global (by design, for a long-running worker process) --
    reset it first so this test's MinIO-pointed settings are actually
    used rather than silently reusing whatever backend an earlier test in
    the same pytest process initialized."""
    dependencies_module._artifact_store = None
    dependencies_module._raw_source_store = None

    context = create_worker_context(
        db_session, settings=worker_settings, worker_id="test"
    )

    yield context

    dependencies_module._artifact_store = None
    dependencies_module._raw_source_store = None


@pytest_asyncio.fixture()
async def cleanup_minio_prefix(worker_settings):
    store = S3ArtifactStore(settings=worker_settings.artifact)
    yield
    await store.delete_prefix(worker_settings.artifact_root_uri)
