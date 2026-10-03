"""Command line: external dataset -> one finalized local MCAP.

::

    dataset-acquisition nuscenes \\
        --dataroot data/raw/nuscenes --version v1.0-mini \\
        --source-unit scene-0061 --output out/scene-0061.mcap \\
        [--channels camera,lidar,pose,can,mission]

Prints one JSON summary (path, sha256, size, message and per-topic counts)
on stdout and exits 0; on failure prints the error on stderr and exits 1.
The tool stops at the local MCAP. Publication and registration are the
platform's: ``python -m sceneops_integrations.recording publish`` and then
``REGISTER_ROBOT_RUN``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .events import AcquisitionError
from .mcap_sink import write_mcap
from .nuscenes import CHANNEL_GROUPS, NuScenesAdapter, NuScenesSelection


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="dataset-acquisition",
        description="Convert an external dataset into an L1 acquisition recording (MCAP).",
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
    nuscenes.add_argument("--output", required=True, type=Path)
    nuscenes.add_argument(
        "--channels",
        default=",".join(CHANNEL_GROUPS),
        help=f"comma-separated channel groups (default: all of {','.join(CHANNEL_GROUPS)})",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
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
