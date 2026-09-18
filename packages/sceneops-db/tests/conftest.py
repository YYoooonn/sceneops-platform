"""Shared fixtures for sceneops-db integration tests against a real Postgres.

These tests require SCENEOPS_DATABASE_URL to point at a real, migrated
Postgres instance. `make test-integration` sets it automatically against the
local stack (`make local-up` must be running first). If it's missing or the
database is unreachable, tests are skipped rather than erroring.

The engine/sessionmaker in sceneops_db.session are process-lifetime
singletons (by design — that's the right shape for a long-running API/worker
process). pytest-asyncio gives each test function its own event loop by
default, so that singleton must be reset every test, or the second test to
run reuses a connection pool bound to the first test's (now-closed) loop.
"""

from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from sceneops_db.session import (
    dispose_async_engine,
    get_async_engine,
    get_async_sessionmaker,
    reset_async_engine_cache,
)


@pytest.fixture()
def unique_id():
    """A per-test-call identity generator, isolated from other developers'
    persistent local-stack data and from other test runs.

    A fixture (not a plain importable helper) deliberately: this test
    directory has no __init__.py, so test modules can't do a package-
    relative import of it — importlib import-mode would otherwise collide
    "tests.conftest" here with packages/sceneops-storage/tests/conftest.py's
    identically-named module when both are collected in one `pytest` run
    (as `make test-integration` does)."""

    def _make(prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex[:10]}"

    return _make


@pytest_asyncio.fixture(autouse=True)
async def _fresh_database_connection():
    """Runs before/after every test: fresh engine bound to this test's event
    loop, skip (not fail) if Postgres isn't reachable, dispose afterward."""
    if not os.environ.get("SCENEOPS_DATABASE_URL"):
        pytest.skip(
            "SCENEOPS_DATABASE_URL not set — sceneops-db integration tests need "
            "a real Postgres instance. Run via `make test-integration` against "
            "a running `make local-up` stack."
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
async def db_session():
    """A real session, rolled back (not committed) at teardown so tests
    never depend on — or pollute — persistent local-stack state beyond the
    uniquely-identified rows they explicitly create."""
    sessionmaker = get_async_sessionmaker()
    async with sessionmaker() as session:
        try:
            yield session
        finally:
            await session.rollback()
            await session.close()
