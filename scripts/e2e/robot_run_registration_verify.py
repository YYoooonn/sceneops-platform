#!/usr/bin/env python3
"""Real streaming-capture -> RobotRun registration E2E verification.

Runs on the HOST via `uv run` -- reaches Postgres/MinIO on their
host-published local-stack ports (mirrors scripts/e2e/smoke_streaming.py's
own host-vs-in-network split; docs/architecture/streaming-transport.md
§9.3). Independently re-derives canonical state directly from Postgres/
MinIO via their own repositories/ArtifactStore -- the registration CLI's
own printed output is only a starting point for what to look up, never
trusted as proof by itself.

Usage (normally invoked by scripts/e2e/e2e_robot_run_registration.sh):
    uv run python scripts/e2e/robot_run_registration_verify.py \\
        --robot-id ROBOT --robot-run-id RUN \\
        --expected-checksum sha256:...
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from mcap.reader import make_reader

from sceneops_core.artifacts.schemas import ArtifactOwnerType
from sceneops_core.config import ArtifactSettings
from sceneops_db.postgres.artifacts import PostgresArtifactRefRepository
from sceneops_db.postgres.episodes import PostgresEpisodeRepository
from sceneops_db.postgres.robots import PostgresRobotRunRepository
from sceneops_db.session import get_async_sessionmaker
from sceneops_storage.backends.s3 import S3ArtifactStore
from sceneops_worker.datasets.ingestion.rosbag_raw_log import RosbagAdapter

_PASS = 0
_FAIL = 0

_EXPECTED_CHANNELS = {
    "/vehicle/odom",
    "/vehicle/imu",
    "/vehicle/status",
    "/vehicle/control",
    "/mission/status",
}


def _check(label: str, condition: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if condition:
        print(f"  ✅  {label}")
        _PASS += 1
    else:
        suffix = f" -- {detail}" if detail else ""
        print(f"  ❌  {label}{suffix}")
        _FAIL += 1


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="RobotRun registration E2E verification"
    )
    parser.add_argument("--robot-id", required=True)
    parser.add_argument("--robot-run-id", required=True)
    parser.add_argument("--expected-checksum", required=True)
    args = parser.parse_args()

    print("=== RobotRun registration E2E verification ===")
    print(f"  robot_id={args.robot_id} robot_run_id={args.robot_run_id}")
    print()

    sessionmaker = get_async_sessionmaker()
    async with sessionmaker() as session:
        run_repo = PostgresRobotRunRepository(session)
        artifact_repo = PostgresArtifactRefRepository(session)
        episode_repo = PostgresEpisodeRepository(session)

        print("--- canonical DB state ---")
        robot_run = await run_repo.get(args.robot_run_id)
        _check("RobotRun exists", robot_run is not None)
        if robot_run is None:
            print("cannot continue without a RobotRun row")
            return 1
        _check(
            "RobotRun.status == completed",
            robot_run.status.value == "completed",
            f"got {robot_run.status.value}",
        )

        artifacts = await artifact_repo.list(
            owner_type=ArtifactOwnerType.ROBOT_RUN,
            owner_id=args.robot_run_id,
            limit=10,
        )
        _check(
            "exactly one ArtifactRecord for this RobotRun",
            len(artifacts) == 1,
            f"got {len(artifacts)}",
        )
        artifact = artifacts[0] if artifacts else None
        if artifact is not None:
            _check(
                "artifact checksum matches CaptureResult's own sha256",
                artifact.checksum == args.expected_checksum,
                f"artifact={artifact.checksum} expected={args.expected_checksum}",
            )
            _check(
                "RobotRun.mcap_uri == artifact.uri",
                robot_run.mcap_uri == artifact.uri,
            )

        episodes = await episode_repo.list(robot_run_id=args.robot_run_id, limit=10)
        _check("zero Episodes created for this RobotRun", len(episodes) == 0)
        print()

    if artifact is None:
        return 1

    print("--- stored artifact readback (real MinIO) ---")
    settings = ArtifactSettings(
        backend="minio",
        root_uri="s3://sceneops/artifacts",
        endpoint_url=f"http://localhost:{os.environ.get('MINIO_API_PORT', '9000')}",
        access_key_id=os.environ.get("MINIO_ROOT_USER", "minioadmin"),
        secret_access_key=os.environ.get("MINIO_ROOT_PASSWORD", "minioadmin"),
    )
    store = S3ArtifactStore(settings=settings)

    exists = await store.exists(artifact.uri)
    _check("stored object exists in MinIO", exists, artifact.uri)
    if not exists:
        return 1

    stored_bytes = await store.read_bytes(artifact.uri)
    stored_checksum = f"sha256:{hashlib.sha256(stored_bytes).hexdigest()}"
    _check(
        "stored bytes checksum matches CaptureResult's own sha256",
        stored_checksum == args.expected_checksum,
        f"stored={stored_checksum} expected={args.expected_checksum}",
    )
    print()

    print("--- RosbagAdapter compatibility (retrieved bytes, no DB writes) ---")
    with tempfile.TemporaryDirectory() as tmp:
        local_path = Path(tmp) / "retrieved.mcap"
        local_path.write_bytes(stored_bytes)

        with open(local_path, "rb") as f:
            reader = make_reader(f)
            channels = {channel.topic for _s, channel, _m in reader.iter_messages()}
        _check(
            "retrieved MCAP exposes all 5 expected channels",
            _EXPECTED_CHANNELS.issubset(channels),
            f"got {channels}",
        )

        adapter = RosbagAdapter(source_store=MagicMock(), source_root_uri=str(local_path))
        source = adapter.extract_episode_source(
            robot_id=args.robot_id, robot_run_id=args.robot_run_id
        )
        _check("RosbagAdapter opens the retrieved MCAP without error", source is not None)
        _check(
            "retrieved bag: robot_states non-empty",
            len(source.robot_states) > 0,
            f"got {len(source.robot_states)}",
        )
        _check(
            "retrieved bag: missions non-empty",
            len(source.missions) > 0,
            f"got {len(source.missions)}",
        )
    print()

    print("=" * 60)
    print(f"  RobotRun registration E2E verification complete: {_PASS} passed / {_FAIL} failed")
    print("=" * 60)
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
