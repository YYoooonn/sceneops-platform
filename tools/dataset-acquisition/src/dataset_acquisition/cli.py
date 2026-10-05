"""Command line: external dataset -> one finalized local MCAP (batch) or a
timed ROS 2 replay (streaming).

::

    dataset-acquisition nuscenes \\
        --dataroot data/raw/nuscenes --version v1.0-mini \\
        --source-unit scene-0061 --output out/scene-0061.mcap \\
        [--channels camera,lidar,pose,can,mission]

    dataset-acquisition nuscenes ... --replay [--rate 2.0]

    dataset-acquisition reference {inspect,verify,prepare} \\
        --corpus config/reference/nuscenes-mini-v1 --dataroot data/raw/nuscenes \\
        --cache-root data/reference (--scope smoke-1 | --fixture scene-0061)

    dataset-acquisition nuscenes-labels \\
        --dataroot data/raw/nuscenes --version v1.0-mini \\
        --source-unit scene-0061 --robot-run-id run-1 \\
        --label-set-id nuscenes-v1.0-mini-scene-0061 --output out/labels.json

``reference`` works on a versioned corpus of fixtures (``reference.py``):
``inspect`` prints a fixture's definition and live source facts, ``verify``
checks source, definition, tool identity and the cached recording against
``corpus.lock.json`` (read-only), ``prepare`` materializes missing recordings
and verifies them, and only ``prepare --update-lock`` writes the lock. One JSON
line per fixture goes to stdout; exit 1 if any fixture disagrees.

Batch prints one JSON summary (path, sha256, size, message and per-topic
counts) on stdout and exits 0. Replay prints one ``replay_summary`` JSON
line. On failure both print the error on stderr and exit 1.

The tool stops at the local MCAP or the ROS 2 topics. Publication and
registration are the platform's: ``python -m sceneops_integrations.recording
publish`` and then ``REGISTER_ROBOT_RUN``; for replay, the platform's ROS 2
bridge and capture.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .events import AcquisitionError
from .mcap_sink import write_mcap
from .nuscenes import CHANNEL_GROUPS, NuScenesAdapter, NuScenesSelection

REPLAY_SUMMARY_PREFIX = "replay_summary "


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="dataset-acquisition",
        description="Convert an external dataset into an L1 acquisition recording "
        "(MCAP) or replay it onto ROS 2 topics.",
    )
    sub = parser.add_subparsers(dest="format", required=True)
    nuscenes = sub.add_parser(
        "nuscenes", help="nuScenes (camera, lidar, ego pose, CAN bus)"
    )
    nuscenes.add_argument("--dataroot", required=True, type=Path)
    nuscenes.add_argument("--version", default="v1.0-mini")
    nuscenes.add_argument(
        "--source-unit",
        required=True,
        help="nuScenes scene name selecting the source records, e.g. scene-0061",
    )
    sink = nuscenes.add_mutually_exclusive_group(required=True)
    sink.add_argument("--output", type=Path, help="batch: write a finalized MCAP here")
    sink.add_argument(
        "--replay",
        action="store_true",
        help="streaming: publish the events on ROS 2 topics (needs the replay image)",
    )
    nuscenes.add_argument(
        "--channels",
        default=",".join(CHANNEL_GROUPS),
        help=f"comma-separated channel groups (default: all of {','.join(CHANNEL_GROUPS)})",
    )
    nuscenes.add_argument(
        "--rate",
        type=float,
        default=1.0,
        help="replay speed relative to the source timeline; 0 publishes without pacing",
    )
    nuscenes.add_argument(
        "--wait-subscribers-seconds",
        type=float,
        default=60.0,
        help="replay: fail if a topic has no subscriber after this long",
    )
    labels = sub.add_parser(
        "nuscenes-labels",
        help="nuScenes annotations of one unit as a label set document "
        "(post-acquisition labels, imported separately from the recording)",
    )
    labels.add_argument("--dataroot", required=True, type=Path)
    labels.add_argument("--version", default="v1.0-mini")
    labels.add_argument("--source-unit", required=True)
    labels.add_argument(
        "--robot-run-id",
        required=True,
        help="the RobotRun the unit's recording was registered as; labels anchor on it",
    )
    labels.add_argument("--label-set-id", required=True)
    labels.add_argument("--output", required=True, type=Path)
    labels.add_argument(
        "--anchor-channel",
        default="LIDAR_TOP",
        help="nuScenes channel whose key frame observation each sample's labels anchor on",
    )
    reference = sub.add_parser(
        "reference",
        help="versioned reference corpus: fingerprints, cached recordings, lock",
    )
    reference.add_argument("command", choices=("inspect", "verify", "prepare"))
    reference.add_argument(
        "--corpus", required=True, type=Path, help="corpus directory"
    )
    reference.add_argument("--dataroot", required=True, type=Path)
    reference.add_argument(
        "--cache-root",
        required=True,
        type=Path,
        help="parent of <corpus_id>/recordings, the local recording cache",
    )
    reference.add_argument("--scope", help="a scope the corpus defines")
    reference.add_argument(
        "--fixture", action="append", default=[], help="a fixture id (repeatable)"
    )
    reference.add_argument(
        "--update-lock",
        action="store_true",
        help="prepare only: record what the current source and tool produce in "
        "corpus.lock.json (the only way the lock is written)",
    )
    return parser.parse_args(argv)


def _reference(args: argparse.Namespace) -> int:
    from . import reference

    if args.update_lock and args.command != "prepare":
        raise AcquisitionError("--update-lock applies to `reference prepare` only")
    corpus = reference.load_corpus(args.corpus)
    fixtures = corpus.select(args.scope, args.fixture)
    if args.command == "prepare":
        return reference.prepare(
            args.corpus,
            fixtures,
            dataroot=args.dataroot,
            cache_root=args.cache_root,
            update_lock=args.update_lock,
        )
    run = reference.inspect if args.command == "inspect" else reference.verify
    return run(
        args.corpus, fixtures, dataroot=args.dataroot, cache_root=args.cache_root
    )


def _write_labels(args: argparse.Namespace) -> int:
    from .labels import label_document

    try:
        adapter = NuScenesAdapter(
            NuScenesSelection(
                dataroot=args.dataroot,
                version=args.version,
                source_unit=args.source_unit,
                channel_groups=frozenset({"lidar"}),
            )
        )
        document = label_document(
            adapter,
            robot_run_id=args.robot_run_id,
            label_set_id=args.label_set_id,
            anchor_channel=args.anchor_channel,
        )
    except AcquisitionError as exc:
        print(f"label export failed: {exc}", file=sys.stderr)
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")
    print(
        json.dumps(
            {
                "path": str(args.output),
                "label_set_id": args.label_set_id,
                "coverage_count": len(document["coverage"]),
                "label_count": len(document["labels"]),
            },
            sort_keys=True,
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.format == "nuscenes-labels":
        return _write_labels(args)
    if args.format == "reference":
        try:
            return _reference(args)
        except AcquisitionError as exc:
            print(f"reference failed: {exc}", file=sys.stderr)
            return 1
    try:
        selection = NuScenesSelection(
            dataroot=args.dataroot,
            version=args.version,
            source_unit=args.source_unit,
            channel_groups=frozenset(
                g.strip() for g in args.channels.split(",") if g.strip()
            ),
        )
        adapter = NuScenesAdapter(selection)
        if args.replay:
            from .ros2_replay import replay, ros2_publishers

            with ros2_publishers() as factory:
                summary = replay(
                    adapter,
                    factory,
                    rate=args.rate,
                    wait_subscribers_seconds=args.wait_subscribers_seconds,
                )
            print(REPLAY_SUMMARY_PREFIX + json.dumps(summary.to_dict(), sort_keys=True))
            return 0
        summary = write_mcap(adapter.events(), args.output, origin=adapter.origin())
    except AcquisitionError as exc:
        print(f"acquisition failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary.to_dict(), sort_keys=True))
    return 0
