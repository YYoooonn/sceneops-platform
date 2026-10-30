"""CLI for the database-free Publisher of recordings and source snapshots.

::

    python -m sceneops_publisher publish \\
        --mcap-path /data/captures/run-001/run-001_0.mcap \\
        --run-id run-001 --robot-id robot-001 [--robot-platform PLATFORM] \\
        --source-kind kafka --source-topic sceneops.robot.telemetry.v1 \\
        [--source-clock mcap_log_time] [--root-uri s3://sceneops/artifacts/robot_runs]

    python -m sceneops_publisher publish \\
        --from-capture /recordings/capture/run-001 [--root-uri ...]

``--from-capture`` publishes a finalized capture directory from its
``capture_receipt.json`` (written by Capture atomically with finalize): the
receipt supplies run id, robot id, platform, source and clock, so a restarted
publisher needs nothing but the directory. It cannot be combined with the
explicit publication inputs above, which remain for recordings that have no
receipt (batch acquisition, fixtures, tools).

On success prints one JSON object on stdout and exits 0; on failure prints
the error on stderr and exits non-zero. The object is the machine-readable
publication result::

    run_id, manifest_uri, manifest_checksum,
    recording_uri, recording_checksum, recording_size_bytes,
    recording_written, manifest_written     (false => identical object existed)

The printed ``manifest_uri`` is what ``POST /robot-runs:register`` takes;
callers never derive storage keys themselves.

::

    python -m sceneops_publisher check --mcap-path rec.mcap

::

    python -m sceneops_publisher compare --first a.mcap --second b.mcap

checks semantic acquisition equivalence of two recordings (§29.12,
``equivalence.py``): same channels and same per-channel message multisets,
ignoring receive times, write order and file bytes; exits non-zero otherwise.

``check`` runs the L1 conformance suite (``conformance.py``) against a local MCAP,
prints its JSON report and exits non-zero if the recording does not
conform. It reads only the file: no ArtifactStore, no database.

::

    python -m sceneops_publisher scan-capture \
        --capture-root /recordings/capture

classifies every run on a capture output root (``capture_unfinished``,
``finalized_with_receipt``, ``finalized_no_receipt``,
``finalized_receipt_invalid``) and prints the JSON ``CaptureScanReport``. It is
read-only and reads no recording bytes; the API reconciler accepts this report
(``reconcile --once --capture-report``) so the platform never mounts the
capture volume.

::

    python -m sceneops_publisher publish-pending \\
        --capture-root /recordings/capture [--root-uri ...]

one-shot recovery of publication (ADR-008 §3.2): publishes every finalized capture
that has a valid receipt and is not completely published, through the same path
as ``publish --from-capture``, and prints the JSON ``PublishPendingReport``. Runs
it skips (already published, no receipt, a manifest that contradicts its
recording) are reported with their reason and never repaired; a capture whose
publish fails is reported as ``failed`` and the pass continues. Exit status: 0
when nothing failed, 2 when any publish failed (the report is still printed), 1
when the capture root or the store could not be read. It keeps no state: running
it again is the retry. Each publication attempt is also logged to stderr as one
``acquisition_recovery {json}`` record and the pass as one
``acquisition_recovery_pass {json}`` record.

::

    python -m sceneops_publisher publish-snapshot \\
        --source-dir /data/raw/lerobot/pusht --format lerobot \\
        --upstream lerobot/pusht --upstream-revision 7628202a... \\
        --include README.md --include 'data/**' [--include ...] \\
        [--annotation license=mit ...] [--root-uri s3://sceneops/artifacts/sources]

preserves the files the declaration selects from a local source tree
(ADR-015 §5, ``sceneops_sources.publication``): each file under its content id,
write-once, then the inventory, then the descriptor last as the publication
marker. Include patterns are written in the selection dialect (``*``, ``?``,
whole-segment ``**``) and each must match at least one file. On success it
prints one JSON object::

    snapshot_id, content_id, descriptor_uri, inventory_uri, descriptor_checksum,
    file_count, total_bytes, files_written, files_reused,
    inventory_written, descriptor_written     (false => identical object existed)

The printed ``descriptor_uri`` is what ``REGISTER_SOURCE_SNAPSHOT`` takes. The
source directory, retrieval time and operator are this process's own
history and are written to no object. A publish that stops before the
descriptor publishes nothing; running it again is the retry.

ArtifactStore backend/credentials come from environment variables via
``sceneops_core.config.ArtifactSettings``, e.g.::

    SCENEOPS_PUBLISHER_ARTIFACT__BACKEND=minio
    SCENEOPS_PUBLISHER_ARTIFACT__ROOT_URI=s3://sceneops/artifacts
    SCENEOPS_PUBLISHER_ARTIFACT__ENDPOINT_URL=http://localhost:9000
    SCENEOPS_PUBLISHER_ARTIFACT__ACCESS_KEY_ID=minioadmin
    SCENEOPS_PUBLISHER_ARTIFACT__SECRET_ACCESS_KEY=minioadmin

``--root-uri`` defaults to that ArtifactSettings' ``robot_run_root_uri``
(``source_root_uri`` for ``publish-snapshot``).
The root is a normalized publication input: publishing the same recording
under a different root yields a different manifest.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from sceneops_core.robots.clock import MCAP_LOG_TIME_CLOCK
from sceneops_core.robots.manifest import CaptureSource, CaptureSourceKind
from sceneops_core.sources import SnapshotDeclaration, SnapshotSelection
from sceneops_recording.recovery_log import configure_cli_logging
from sceneops_sources import publish_snapshot
from sceneops_storage import create_artifact_store

from sceneops_recording.capture_scan import scan_capture_volume
from sceneops_recording.conformance import check_l1_recording
from sceneops_recording.equivalence import compare_recordings
from sceneops_recording.from_capture import publish_from_capture
from sceneops_recording.publish_pending import publish_pending
from sceneops_recording.publisher import publish_recording
from sceneops_recording.settings import RecordingPublisherSettings


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m sceneops_publisher",
        description="Publish a finalized MCAP + canonical RobotRunManifest, or a "
        "source snapshot.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    publish = sub.add_parser("publish")
    publish.add_argument(
        "--from-capture",
        type=Path,
        default=None,
        help="Finalized capture directory holding the MCAP and its "
        "capture_receipt.json; replaces every explicit publication input.",
    )
    publish.add_argument("--mcap-path", type=Path)
    publish.add_argument("--run-id")
    publish.add_argument("--robot-id")
    publish.add_argument("--robot-platform", default=None)
    publish.add_argument(
        "--source-kind",
        choices=[kind.value for kind in CaptureSourceKind],
    )
    publish.add_argument(
        "--source-topic",
        action="append",
        default=None,
        help="Kafka topic the recording was captured from (repeatable; "
        "required iff --source-kind=kafka).",
    )
    publish.add_argument(
        "--source-clock",
        default=None,
        help=f"Default {MCAP_LOG_TIME_CLOCK}.",
    )
    publish.add_argument("--root-uri", default=None)
    check = sub.add_parser("check")
    check.add_argument("--mcap-path", required=True, type=Path)
    check.add_argument(
        "--any-encoding-profile",
        action="store_true",
        help="Do not require the ros2/cdr/ros2msg encoding profile.",
    )
    pending = sub.add_parser("publish-pending")
    pending.add_argument("--capture-root", required=True, type=Path)
    pending.add_argument("--root-uri", default=None)
    scan_capture = sub.add_parser("scan-capture")
    scan_capture.add_argument("--capture-root", required=True, type=Path)
    snapshot = sub.add_parser("publish-snapshot")
    snapshot.add_argument("--source-dir", required=True, type=Path)
    snapshot.add_argument("--format", required=True)
    snapshot.add_argument("--upstream", required=True)
    snapshot.add_argument("--upstream-revision", required=True)
    snapshot.add_argument(
        "--include",
        action="append",
        required=True,
        help="Include pattern relative to --source-dir (repeatable).",
    )
    snapshot.add_argument(
        "--annotation",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Source-declared note such as license=mit (repeatable).",
    )
    snapshot.add_argument("--root-uri", default=None)
    compare = sub.add_parser("compare")
    compare.add_argument("--first", required=True, type=Path)
    compare.add_argument("--second", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.command == "publish":
        _check_publish_inputs(parser, args)
    return args


# Publication inputs the capture receipt owns; --from-capture rejects them
# rather than letting a caller override what was durably recorded.
_EXPLICIT_PUBLISH_INPUTS = (
    "mcap_path",
    "run_id",
    "robot_id",
    "robot_platform",
    "source_kind",
    "source_topic",
    "source_clock",
)


def _check_publish_inputs(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> None:
    if args.from_capture is not None:
        supplied = [
            "--" + name.replace("_", "-")
            for name in _EXPLICIT_PUBLISH_INPUTS
            if getattr(args, name) is not None
        ]
        if supplied:
            parser.error(
                f"--from-capture takes its inputs from the capture receipt and "
                f"cannot be combined with: {', '.join(supplied)}"
            )
        return
    missing = [
        "--" + name.replace("_", "-")
        for name in ("mcap_path", "run_id", "robot_id", "source_kind")
        if getattr(args, name) is None
    ]
    if missing:
        parser.error(
            f"publish requires --from-capture or all of: "
            f"--mcap-path, --run-id, --robot-id, --source-kind (missing: "
            f"{', '.join(missing)})"
        )


async def _publish(args: argparse.Namespace) -> dict[str, object]:
    settings = RecordingPublisherSettings()
    store = create_artifact_store(settings.artifact)
    topics = sorted(set(args.source_topic)) if args.source_topic else None
    root_uri = args.root_uri or settings.artifact.robot_run_root_uri
    if args.from_capture is not None:
        publication = await publish_from_capture(
            artifact_store=store,
            root_uri=root_uri,
            capture_dir=args.from_capture,
        )
    else:
        publication = await publish_recording(
            artifact_store=store,
            root_uri=root_uri,
            recording_path=args.mcap_path,
            run_id=args.run_id,
            robot_id=args.robot_id,
            robot_platform=args.robot_platform,
            capture_source=CaptureSource(
                kind=CaptureSourceKind(args.source_kind), topics=topics
            ),
            source_clock=args.source_clock or MCAP_LOG_TIME_CLOCK,
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


def _annotations(pairs: list[str]) -> dict[str, str]:
    annotations: dict[str, str] = {}
    for pair in pairs:
        key, separator, value = pair.partition("=")
        if not separator:
            raise ValueError(f"--annotation must be KEY=VALUE, got {pair!r}")
        if key in annotations:
            raise ValueError(f"--annotation {key!r} is given twice")
        annotations[key] = value
    return annotations


async def _publish_snapshot(args: argparse.Namespace) -> dict[str, object]:
    settings = RecordingPublisherSettings()
    declaration = SnapshotDeclaration(
        format=args.format,
        upstream=args.upstream,
        upstream_revision=args.upstream_revision,
        # The selection is a set of patterns: its canonical form is sorted.
        selection=SnapshotSelection(include=tuple(sorted(set(args.include)))),
    )
    published = await publish_snapshot(
        store=create_artifact_store(settings.artifact),
        sources_root=args.root_uri or settings.artifact.source_root_uri,
        source_dir=args.source_dir,
        declaration=declaration,
        annotations=_annotations(args.annotation),
    )
    return {
        "snapshot_id": published.snapshot_id,
        "content_id": published.content_id,
        "descriptor_uri": published.descriptor_uri,
        "inventory_uri": published.inventory_uri,
        "descriptor_checksum": published.descriptor_checksum,
        "file_count": len(published.inventory.files),
        "total_bytes": published.inventory.total_bytes,
        "files_written": published.files_written,
        "files_reused": published.files_reused,
        "inventory_written": published.inventory_written,
        "descriptor_written": published.descriptor_written,
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


def _scan_capture(args: argparse.Namespace) -> int:
    try:
        report = scan_capture_volume(args.capture_root)
    except Exception as exc:  # noqa: BLE001 - CLI boundary: report and exit non-zero
        print(f"scan-capture failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report.model_dump(mode="json"), sort_keys=True))
    return 0


def _publish_pending(args: argparse.Namespace) -> int:
    configure_cli_logging()
    settings = RecordingPublisherSettings()
    try:
        report = asyncio.run(
            publish_pending(
                artifact_store=create_artifact_store(settings.artifact),
                root_uri=args.root_uri or settings.artifact.robot_run_root_uri,
                capture_root=args.capture_root,
            )
        )
    except Exception as exc:  # noqa: BLE001 - CLI boundary: report and exit non-zero
        print(f"publish-pending failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report.model_dump(mode="json"), sort_keys=True))
    return 2 if report.failed else 0


def _compare(args: argparse.Namespace) -> int:
    report = compare_recordings(args.first, args.second)
    print(json.dumps(report.to_dict(), sort_keys=True))
    if not report.equivalent:
        print(
            f"recordings are not semantically equivalent: "
            f"{len(report.differences)} difference(s)",
            file=sys.stderr,
        )
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.command == "check":
        return _check(args)
    if args.command == "compare":
        return _compare(args)
    if args.command == "scan-capture":
        return _scan_capture(args)
    if args.command == "publish-pending":
        return _publish_pending(args)
    if args.command == "publish-snapshot":
        try:
            snapshot = asyncio.run(_publish_snapshot(args))
        except Exception as exc:  # noqa: BLE001 - CLI boundary: report and exit non-zero
            print(
                f"publish-snapshot failed: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            return 1
        print(json.dumps(snapshot, sort_keys=True))
        return 0
    try:
        result = asyncio.run(_publish(args))
    except Exception as exc:  # noqa: BLE001 - CLI boundary: report and exit non-zero
        print(f"publish failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0
