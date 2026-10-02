#!/usr/bin/env python3
"""Real streaming-capture -> Recording Publisher -> REGISTER_ROBOT_RUN E2E
verification.

Runs on the HOST via `uv run` -- reaches Postgres/MinIO on their
host-published local-stack ports (mirrors scripts/e2e/smoke_streaming.py's
own host-vs-in-network split; docs/architecture/streaming-transport.md
§9.3). Independently re-derives canonical state directly from Postgres/
MinIO via their own repositories/ArtifactStore -- the publisher's and the
registration Job's own output is only a starting point for what to look
up, never trusted as proof by itself.

Usage (normally invoked by scripts/e2e/e2e_robot_run_registration.sh):
    uv run python scripts/e2e/robot_run_registration_verify.py \\
        --robot-id ROBOT --robot-run-id RUN --manifest-uri s3://... \\
        --expected-checksum sha256:... [--expected-topic TOPIC]
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import sys
from unittest.mock import MagicMock

from sceneops_core.artifacts.schemas import ArtifactOwnerType
from sceneops_core.common.ids import (
    robot_run_manifest_artifact_id,
    robot_run_recording_artifact_id,
)
from sceneops_core.config import ArtifactSettings
from sceneops_core.robots.manifest import load_canonical_robot_run_manifest
from sceneops_db.postgres.artifacts import PostgresArtifactRefRepository
from sceneops_db.postgres.episodes import PostgresEpisodeRepository
from sceneops_db.postgres.robots import PostgresRobotRunRepository
from sceneops_db.session import get_async_sessionmaker
from sceneops_storage.backends.s3 import S3ArtifactStore
from sceneops_worker.datasets.ingestion.rosbag_raw_log import RosbagAdapter
from sceneops_worker.robots.resolver import resolve_recording
from sceneops_worker.stores.artifacts import ArtifactRecordStore
from sceneops_worker.stores.robots import RobotStore

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


def _sha256(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="RobotRun publication + registration E2E verification"
    )
    parser.add_argument("--robot-id", required=True)
    parser.add_argument("--robot-run-id", required=True)
    parser.add_argument("--manifest-uri", required=True)
    parser.add_argument("--expected-checksum", required=True)
    parser.add_argument("--expected-topic", default=None)
    args = parser.parse_args()

    print("=== RobotRun publication + registration E2E verification ===")
    print(f"  robot_id={args.robot_id} robot_run_id={args.robot_run_id}")
    print()

    settings = ArtifactSettings(
        backend="minio",
        root_uri="s3://sceneops/artifacts",
        endpoint_url=f"http://localhost:{os.environ.get('MINIO_API_PORT', '9000')}",
        access_key_id=os.environ.get("MINIO_ROOT_USER", "minioadmin"),
        secret_access_key=os.environ.get("MINIO_ROOT_PASSWORD", "minioadmin"),
    )
    store = S3ArtifactStore(settings=settings)

    print("--- published RobotRunManifest (real MinIO) ---")
    manifest_bytes = await store.read_bytes(args.manifest_uri)
    manifest = load_canonical_robot_run_manifest(manifest_bytes)
    _check("manifest bytes are canonical RobotRunManifest v1", True)
    _check("manifest.run_id matches", manifest.run_id == args.robot_run_id)
    _check("manifest.robot_id matches", manifest.robot_id == args.robot_id)
    _check(
        "manifest recording checksum == CaptureResult sha256",
        manifest.recording.checksum == args.expected_checksum,
        f"manifest={manifest.recording.checksum} expected={args.expected_checksum}",
    )
    _check(
        "manifest declares all 5 expected channels",
        _EXPECTED_CHANNELS.issubset({c.topic for c in manifest.channels}),
    )
    if args.expected_topic is not None:
        _check(
            "manifest capture source is the Kafka topic",
            manifest.capture.source.kind.value == "kafka"
            and manifest.capture.source.topics == [args.expected_topic],
        )
    print()

    sessionmaker = get_async_sessionmaker()
    async with sessionmaker() as session:
        run_repo = PostgresRobotRunRepository(session)
        artifact_repo = PostgresArtifactRefRepository(session)
        episode_repo = PostgresEpisodeRepository(session)

        print("--- canonical DB state ---")
        robot_run = await run_repo.get(args.robot_run_id)
        _check("RobotRunRecord exists", robot_run is not None)
        if robot_run is None:
            print("cannot continue without a RobotRunRecord")
            return 1
        _check(
            "RobotRun.manifest_checksum == sha256(manifest bytes)",
            robot_run.manifest_checksum == _sha256(manifest_bytes),
        )
        _check(
            "RobotRun time range == manifest",
            (robot_run.started_at, robot_run.ended_at)
            == (manifest.started_at, manifest.ended_at),
        )
        _check(
            "RobotRun.source_clock == manifest",
            robot_run.source_clock == manifest.capture.source_clock,
        )
        _check(
            "deterministic artifact ids",
            robot_run.recording_artifact_id
            == robot_run_recording_artifact_id(args.robot_run_id)
            and robot_run.manifest_artifact_id
            == robot_run_manifest_artifact_id(args.robot_run_id),
        )

        artifacts = await artifact_repo.list(
            owner_type=ArtifactOwnerType.ROBOT_RUN,
            owner_id=args.robot_run_id,
            limit=10,
        )
        by_kind = {a.kind: a for a in artifacts}
        _check(
            "exactly two ArtifactRecords: recording + manifest",
            len(artifacts) == 2
            and set(by_kind) == {"robot_run_recording", "robot_run_manifest"},
            f"got {[a.kind for a in artifacts]}",
        )
        recording = by_kind.get("robot_run_recording")
        manifest_artifact = by_kind.get("robot_run_manifest")
        if recording is not None:
            _check(
                "recording ArtifactRecord matches manifest (uri/checksum/size)",
                (recording.uri, recording.checksum, recording.size_bytes)
                == (
                    manifest.recording.uri,
                    manifest.recording.checksum,
                    manifest.recording.size_bytes,
                ),
            )
        if manifest_artifact is not None:
            _check(
                "manifest ArtifactRecord points at the published manifest",
                manifest_artifact.uri == args.manifest_uri
                and manifest_artifact.checksum == _sha256(manifest_bytes),
            )

        episodes = await episode_repo.list(robot_run_id=args.robot_run_id, limit=10)
        _check("zero Episodes created for this RobotRun", len(episodes) == 0)
        print()

    if recording is None:
        return 1

    print("--- stored recording readback (real MinIO) ---")
    stored_bytes = await store.read_bytes(recording.uri)
    _check(
        "stored bytes checksum matches CaptureResult's own sha256",
        _sha256(stored_bytes) == args.expected_checksum,
    )
    print()

    print("--- verified recording resolver -> RosbagAdapter (no DB writes) ---")
    async with sessionmaker() as session:
        async with resolve_recording(
            robot_run_id=args.robot_run_id,
            robot_store=RobotStore(session),
            artifact_record_store=ArtifactRecordStore(session),
            artifact_store=store,
        ) as resolved:
            _check(
                "resolved recording checksum == CaptureResult sha256",
                resolved.checksum == args.expected_checksum,
            )
            _check(
                "resolved robot_id is the RobotRun's robot",
                resolved.robot_id == args.robot_id,
            )
            adapter = RosbagAdapter(
                source_store=MagicMock(), source_root_uri=str(resolved.local_path)
            )
            source = adapter.extract_episode_source(
                robot_id=resolved.robot_id, robot_run_id=args.robot_run_id
            )
        _check(
            "resolved local copy removed after use",
            not resolved.local_path.exists(),
        )
    _check(
        "resolved bag: robot_states non-empty",
        len(source.robot_states) > 0,
        f"got {len(source.robot_states)}",
    )
    _check(
        "resolved bag: missions non-empty",
        len(source.missions) > 0,
        f"got {len(source.missions)}",
    )
    print()

    print("=" * 60)
    print(
        "  RobotRun publication + registration E2E verification complete: "
        f"{_PASS} passed / {_FAIL} failed"
    )
    print("=" * 60)
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
