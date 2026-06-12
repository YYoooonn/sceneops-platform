#!/usr/bin/env python
"""Integration check: verify S3ArtifactStore works against the local MinIO service.

Usage:
    uv run python scripts/checks/check_minio.py
    uv run python scripts/checks/check_minio.py --endpoint http://localhost:9000
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sceneops_core.config import ArtifactBackend, ArtifactSettings
from sceneops_storage.s3 import S3ArtifactStore

BUCKET = "sceneops"
CHECK_PREFIX = f"s3://{BUCKET}/_checks"
TEST_URI = f"{CHECK_PREFIX}/store_check.json"
TEST_PAYLOAD = {"check": "s3_artifact_store", "status": "ok"}


async def run_checks(endpoint: str) -> None:
    settings = ArtifactSettings(
        backend=ArtifactBackend.MINIO,
        root_uri=f"s3://{BUCKET}",
        endpoint_url=endpoint,
        access_key_id="minioadmin",
        secret_access_key="minioadmin",
    )
    store = S3ArtifactStore(settings=settings)

    print(f"endpoint : {endpoint}")
    print(f"bucket   : {BUCKET}")
    print()

    print(f"[1/5] write_json  {TEST_URI}")
    await store.write_json(TEST_URI, TEST_PAYLOAD)

    print(f"[2/5] exists      {TEST_URI}")
    assert await store.exists(TEST_URI), "Object should exist after write"

    print(f"[3/5] read_json   {TEST_URI}")
    result = await store.read_json(TEST_URI)
    assert result == TEST_PAYLOAD, f"Payload mismatch: {result}"

    print(f"[4/5] list_json   {CHECK_PREFIX}/")
    uris = await store.list_json(f"{CHECK_PREFIX}/")
    assert TEST_URI in uris, f"Expected {TEST_URI} in listing, got: {uris}"

    print(f"[5/5] delete_prefix {CHECK_PREFIX}/")
    await store.delete_prefix(f"{CHECK_PREFIX}/")
    assert not await store.exists(TEST_URI), "Object should be gone after delete"

    print()
    print("S3ArtifactStore (MinIO): OK")


def main() -> None:
    parser = argparse.ArgumentParser(description="Check MinIO connectivity")
    parser.add_argument(
        "--endpoint",
        default="http://localhost:9000",
        help="MinIO endpoint URL (default: http://localhost:9000)",
    )
    args = parser.parse_args()

    try:
        asyncio.run(run_checks(args.endpoint))
    except Exception as exc:
        print(f"\nFAIL: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
