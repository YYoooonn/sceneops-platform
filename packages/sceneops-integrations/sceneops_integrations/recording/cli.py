"""CLI for the database-free Recording Publisher.

::

    python -m sceneops_integrations.recording publish \\
        --mcap-path /data/captures/run-001/run-001_0.mcap \\
        --run-id run-001 --robot-id robot-001 [--robot-platform PLATFORM] \\
        --source-kind kafka --source-topic sceneops.robot.telemetry.v1 \\
        [--source-clock mcap_log_time] [--root-uri s3://sceneops/artifacts/robot_runs]

On success prints one JSON object on stdout and exits 0; on failure prints
the error on stderr and exits non-zero. The object is the machine-readable
publication result::

    run_id, manifest_uri, manifest_checksum,
    recording_uri, recording_checksum, recording_size_bytes,
    recording_written, manifest_written     (false => identical object existed)

The printed ``manifest_uri`` is what ``POST /robot-runs:register`` takes;
callers never derive storage keys themselves.

::

    python -m sceneops_integrations.recording check --mcap-path rec.mcap

runs the L1 conformance suite (``conformance.py``) against a local MCAP,
prints its JSON report and exits non-zero if the recording does not
conform. It reads only the file: no ArtifactStore, no database.

ArtifactStore backend/credentials come from environment variables via
``sceneops_core.config.ArtifactSettings``, e.g.::

    SCENEOPS_PUBLISHER_ARTIFACT__BACKEND=minio
    SCENEOPS_PUBLISHER_ARTIFACT__ROOT_URI=s3://sceneops/artifacts
    SCENEOPS_PUBLISHER_ARTIFACT__ENDPOINT_URL=http://localhost:9000
    SCENEOPS_PUBLISHER_ARTIFACT__ACCESS_KEY_ID=minioadmin
    SCENEOPS_PUBLISHER_ARTIFACT__SECRET_ACCESS_KEY=minioadmin

``--root-uri`` defaults to that ArtifactSettings' ``robot_run_root_uri``.
The root is a normalized publication input: publishing the same recording
under a different root yields a different manifest.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from sceneops_core.config import ArtifactSettings
from sceneops_core.robots.clock import MCAP_LOG_TIME_CLOCK
from sceneops_core.robots.manifest import CaptureSource, CaptureSourceKind
from sceneops_storage import create_artifact_store

from .conformance import check_l1_recording
from .publisher import publish_recording


class RecordingPublisherSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SCENEOPS_PUBLISHER_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    artifact: ArtifactSettings = Field(default_factory=ArtifactSettings)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m sceneops_integrations.recording",
        description="Publish a finalized MCAP + canonical RobotRunManifest.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    publish = sub.add_parser("publish")
    publish.add_argument("--mcap-path", required=True, type=Path)
    publish.add_argument("--run-id", required=True)
    publish.add_argument("--robot-id", required=True)
    publish.add_argument("--robot-platform", default=None)
    publish.add_argument(
        "--source-kind",
        required=True,
        choices=[kind.value for kind in CaptureSourceKind],
    )
    publish.add_argument(
        "--source-topic",
        action="append",
        default=None,
        help="Kafka topic the recording was captured from (repeatable; "
        "required iff --source-kind=kafka).",
    )
    publish.add_argument("--source-clock", default=MCAP_LOG_TIME_CLOCK)
    publish.add_argument("--root-uri", default=None)
    check = sub.add_parser("check")
    check.add_argument("--mcap-path", required=True, type=Path)
    check.add_argument(
        "--any-encoding-profile",
        action="store_true",
        help="Do not require the ros2/cdr/ros2msg encoding profile.",
    )
    return parser.parse_args(argv)


async def _publish(args: argparse.Namespace) -> dict[str, object]:
    settings = RecordingPublisherSettings()
    store = create_artifact_store(settings.artifact)
    topics = sorted(set(args.source_topic)) if args.source_topic else None
    publication = await publish_recording(
        artifact_store=store,
        root_uri=args.root_uri or settings.artifact.robot_run_root_uri,
        recording_path=args.mcap_path,
        run_id=args.run_id,
        robot_id=args.robot_id,
        robot_platform=args.robot_platform,
        capture_source=CaptureSource(
            kind=CaptureSourceKind(args.source_kind), topics=topics
        ),
        source_clock=args.source_clock,
    )
    return {
        "run_id": publication.manifest.run_id,
        "manifest_uri": publication.manifest_uri,
        "manifest_checksum": publication.manifest_checksum,
        "recording_uri": publication.recording_uri,
        "recording_checksum": publication.manifest.recording.checksum,
        "recording_size_bytes": publication.manifest.recording.size_bytes,
        "recording_written": publication.recording_written,
        "manifest_written": publication.manifest_written,
    }


def _check(args: argparse.Namespace) -> int:
    report = check_l1_recording(
        args.mcap_path, require_ros2_profile=not args.any_encoding_profile
    )
    print(json.dumps(report.to_dict(), sort_keys=True))
    if not report.conforms:
        print(
            f"recording does not conform: {len(report.violations)} violation(s)",
            file=sys.stderr,
        )
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.command == "check":
        return _check(args)
    try:
        result = asyncio.run(_publish(args))
    except Exception as exc:  # noqa: BLE001 - CLI boundary: report and exit non-zero
        print(f"publish failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0
