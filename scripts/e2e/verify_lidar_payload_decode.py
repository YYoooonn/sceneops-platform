"""Decodes one canonical lidar payload (a PointCloud2 CDR message in the
ArtifactStore) with the worker's decoder and compares it with the source
nuScenes ``.pcd.bin`` points it came from (ADR-007 §33.5).

Runs inside the worker image (it has the ArtifactStore settings and the
decoder). Prints one JSON line.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import numpy as np

from sceneops_storage import create_artifact_store
from sceneops_worker.config import get_settings
from sceneops_worker.inference.detection.pointcloud2 import (
    POINTCLOUD2_CDR_MEDIA_TYPE,
    decode_lidar_xyz,
)


def _source_files(root: Path) -> list[Path]:
    return sorted(
        p
        for folder in ("samples", "sweeps")
        for p in (root / folder / "LIDAR_TOP").glob("*.pcd.bin")
    )


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--uri", required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    args = parser.parse_args()

    store = create_artifact_store(get_settings().artifact)
    xyz = decode_lidar_xyz(POINTCLOUD2_CDR_MEDIA_TYPE, await store.read_bytes(args.uri))

    matched = None
    for path in _source_files(args.source_root):
        points = np.fromfile(path, dtype=np.float32).reshape(-1, 5)
        if points.shape[0] != xyz.shape[0]:
            continue
        # nuScenes keeps non-finite points out of its tables; the decoder drops
        # them, so compare finite source points.
        finite = points[np.isfinite(points[:, :3]).all(axis=1), :3].astype(np.float64)
        if finite.shape == xyz.shape and np.array_equal(finite, xyz):
            matched = path
            break
    print(
        json.dumps(
            {
                "point_count": int(xyz.shape[0]),
                "matches_source": matched is not None,
                "source_file": matched.name if matched else None,
            }
        )
    )


asyncio.run(main())
