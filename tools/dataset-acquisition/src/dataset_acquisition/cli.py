"""Command line: external dataset -> one finalized local MCAP (batch); a
locked reference MCAP -> a timed ROS 2 replay (streaming).

::

    dataset-acquisition nuscenes \\
        --dataroot data/raw/nuscenes --version v1.0-mini \\
        --source-unit scene-0061 --output out/scene-0061.mcap \\
        [--channels camera,lidar,pose,can,mission]

    dataset-acquisition reference {inspect,verify,prepare,resolve} \\
        --corpus config/reference/nuscenes-mini-v1 --dataroot data/raw/nuscenes \\
        --cache-root data/reference (--scope smoke-1 | --fixture scene-0061)

    dataset-acquisition reference render-labels \\
        --corpus config/reference/nuscenes-mini-v1 --cache-root data/reference \\
        --fixture scene-0061 --robot-run-id run-1 \\
        --label-set-id labels-scene-0061 --output out/labels.json

    dataset-acquisition reference replay \\
        --corpus config/reference/nuscenes-mini-v1 --cache-root data/reference \\
        --fixture scene-0061 [--rate 2.0]

``reference`` works on a versioned corpus of fixtures (``reference.py``):
``inspect`` prints a fixture's definition and live source facts, ``verify``
checks source, definition, tool identity and the cached recording against
``corpus.lock.json`` (read-only), ``prepare`` materializes missing recordings
and verifies them, and only ``prepare --update-lock`` writes the lock.
``resolve`` is the consumer's entry point: it needs no source, checks the lock
and the cached recordings (``--lock-only``: the lock alone) and prints each
fixture's locked facts and recording path (``--with-labels``: also the verified
reference label artifact). ``render-labels`` is the other no-source command: the
locked reference label artifact of one fixture and a target RobotRun id become a
``sceneops.label_set/v1`` document. One JSON line per fixture goes to stdout;
exit 1 if any fixture disagrees. ``replay`` is the streaming counterpart of
``resolve``: it verifies one fixture's cached recording against the lock, then
publishes that MCAP (``mcap_source``) on ROS 2 topics. It reads no source dataset.

``nuscenes`` prints one JSON summary (path, sha256, size, message and per-topic
counts) on stdout and exits 0. ``reference replay`` prints one ``replay_source``
and one ``replay_summary`` JSON line. On failure both print the error on stderr
and exit 1. The only runtime replay source is a locked reference MCAP: a raw
dataset is converted to an MCAP first and never replayed directly.

The tool stops at the local MCAP or the ROS 2 topics. Publication and
registration are the platform's: ``python -m sceneops_publisher
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

REPLAY_SOURCE_PREFIX = "replay_source "
REPLAY_SUMMARY_PREFIX = "replay_summary "


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="dataset-acquisition",
        description="Convert an external dataset into an L1 acquisition recording "
        "(MCAP), or replay a locked reference recording onto ROS 2 topics.",
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
    nuscenes.add_argument(
        "--output", required=True, type=Path, help="write a finalized MCAP here"
    )
    nuscenes.add_argument(
        "--channels",
        default=",".join(CHANNEL_GROUPS),
        help=f"comma-separated channel groups (default: all of {','.join(CHANNEL_GROUPS)})",
    )
    reference = sub.add_parser(
        "reference",
        help="versioned reference corpus: fingerprints, cached recordings, lock",
    )
    reference.add_argument(
        "command",
        choices=("inspect", "verify", "prepare", "resolve", "render-labels", "replay"),
    )
    reference.add_argument(
        "--corpus", required=True, type=Path, help="corpus directory"
    )
    reference.add_argument(
        "--dataroot", type=Path, help="the source dataset (not used by `resolve`)"
    )
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
        "--lock-only",
        action="store_true",
        help="resolve only: check the corpus against the lock and read no recording",
    )
    reference.add_argument(
        "--with-labels",
        action="store_true",
        help="resolve only: also verify each fixture's reference label artifact",
    )
    reference.add_argument(
        "--robot-run-id",
        help="render-labels only: the RobotRun the labels anchor on",
    )
    reference.add_argument(
        "--label-set-id", help="render-labels only: the label set's id"
    )
    reference.add_argument(
        "--output", type=Path, help="render-labels only: write the document here"
    )
    reference.add_argument(
        "--rate",
        type=float,
        help="replay only: speed relative to the source timeline, 0 for no pacing "
        "(default: the fixture's replay definition)",
    )
    reference.add_argument(
        "--wait-subscribers-seconds",
        type=float,
        help="replay only: fail if a topic has no subscriber after this long "
        "(default: the fixture's replay definition)",
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
    if args.lock_only and args.command != "resolve":
        raise AcquisitionError("--lock-only applies to `reference resolve` only")
    if args.with_labels and args.command != "resolve":
        raise AcquisitionError("--with-labels applies to `reference resolve` only")
    render_options = (args.robot_run_id, args.label_set_id, args.output)
    if args.command != "render-labels" and any(render_options):
        raise AcquisitionError(
            "--robot-run-id, --label-set-id and --output apply to "
            "`reference render-labels` only"
        )
    replay_options = (args.rate, args.wait_subscribers_seconds)
    if args.command != "replay" and any(o is not None for o in replay_options):
        raise AcquisitionError(
            "--rate and --wait-subscribers-seconds apply to `reference replay` only"
        )
    corpus = reference.load_corpus(args.corpus)
    fixtures = corpus.select(args.scope, args.fixture)
    if args.command == "replay":
        if len(fixtures) != 1:
            raise AcquisitionError(
                "`reference replay` replays exactly one fixture (one RobotRun per "
                f"replay), got {len(fixtures)}"
            )
        return _reference_replay(args, fixtures[0])
    if args.command == "render-labels":
        if len(fixtures) != 1 or not all(render_options):
            raise AcquisitionError(
                "`reference render-labels` needs exactly one --fixture, "
                "--robot-run-id, --label-set-id and --output"
            )
        return reference.render_labels(
            args.corpus,
            fixtures[0],
            cache_root=args.cache_root,
            robot_run_id=args.robot_run_id,
            label_set_id=args.label_set_id,
            output=args.output,
        )
    if args.command == "resolve":
        return reference.resolve(
            args.corpus,
            fixtures,
            cache_root=args.cache_root,
            check_recordings=not args.lock_only,
            check_labels=args.with_labels,
        )
    if args.dataroot is None:
        raise AcquisitionError(f"`reference {args.command}` needs --dataroot")
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


def _reference_replay(args: argparse.Namespace, fixture_id: str) -> int:
    """Replay a fixture's locked recording. Nothing is published before the
    cached MCAP has been verified against the lock."""
    from . import reference
    from .mcap_source import McapAdapter
    from .ros2_replay import replay, ros2_publishers

    locked = reference.locked_recording(
        args.corpus, fixture_id, cache_root=args.cache_root
    )
    rate = args.rate if args.rate is not None else locked.replay["rate"]
    wait = (
        args.wait_subscribers_seconds
        if args.wait_subscribers_seconds is not None
        else locked.replay["wait_subscribers_seconds"]
    )
    print(
        REPLAY_SOURCE_PREFIX
        + json.dumps(
            {
                "fixture_id": locked.fixture_id,
                "source_unit": locked.source_unit,
                "path": str(locked.path),
                "recording": locked.recording,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    with ros2_publishers() as factory:
        summary = replay(
            McapAdapter(locked.path),
            factory,
            rate=rate,
            wait_subscribers_seconds=wait,
        )
    print(REPLAY_SUMMARY_PREFIX + json.dumps(summary.to_dict(), sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
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
        summary = write_mcap(adapter.events(), args.output, origin=adapter.origin())
    except AcquisitionError as exc:
        print(f"acquisition failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary.to_dict(), sort_keys=True))
    return 0
