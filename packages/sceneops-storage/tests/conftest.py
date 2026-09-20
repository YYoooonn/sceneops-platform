"""Shared fixtures for sceneops-storage integration tests against real MinIO.

Requires a reachable MinIO instance (the local stack's `minio` service,
`make local-up`). Reads connection info from the same env vars
.env.example documents (MINIO_ENDPOINT_URL / MINIO_ROOT_USER /
MINIO_ROOT_PASSWORD / MINIO_BUCKET), with defaults matching the local
stack's host-side ports. Skips (not fails) if MinIO is unreachable.
"""

from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio

from sceneops_core.artifacts.schemas.enums import ArtifactBackend
from sceneops_core.config import StorageSettings
from sceneops_storage.backends.s3 import S3ArtifactStore

BUCKET = os.environ.get("MINIO_BUCKET", "sceneops")
_TEST_PREFIX = "_test-integration"


@pytest.fixture()
def bucket() -> str:
    return BUCKET


@pytest.fixture()
def unique_key():
    """A per-test-call S3 key generator under a dedicated test prefix,
    isolated from real dataset/artifact data in the same bucket.

    A fixture (not a plain importable helper) deliberately: this test
    directory has no __init__.py, so test modules can't do a package-
    relative import of it — importlib import-mode would otherwise collide
    "tests.conftest" here with packages/sceneops-db/tests/conftest.py's
    identically-named module when both are collected in one `pytest` run
    (as `make test-integration` does)."""

    def _make(*parts: str) -> str:
        return "/".join([_TEST_PREFIX, uuid.uuid4().hex[:12], *parts])

    return _make


@pytest_asyncio.fixture()
async def store():
    settings = StorageSettings(
        backend=ArtifactBackend.MINIO,
        root_uri=f"s3://{BUCKET}",
        endpoint_url=os.environ.get("MINIO_ENDPOINT_URL", "http://localhost:9000"),
        region=None,
        access_key_id=os.environ.get("MINIO_ROOT_USER", "minioadmin"),
        secret_access_key=os.environ.get("MINIO_ROOT_PASSWORD", "minioadmin"),
    )
    artifact_store = S3ArtifactStore(settings=settings)

    try:
        await artifact_store.exists(f"s3://{BUCKET}/{_TEST_PREFIX}/_connectivity_check")
    except Exception as exc:  # noqa: BLE001 - report as a skip, not a failure
        pytest.skip(f"MinIO not reachable: {exc}")

    created_prefixes: list[str] = []
    yield artifact_store, created_prefixes

    for prefix in created_prefixes:
        await artifact_store.delete_prefix(f"s3://{BUCKET}/{prefix}")
