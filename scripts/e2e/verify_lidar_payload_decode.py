"""Lidar payload fidelity (ADR-007 §33.5): a canonical lidar payload in the
ArtifactStore is byte-for-byte one message of the locked reference recording it
was built from, and the worker's decoder reads from it exactly the points an
independent decoder reads from that recorded message.

Runs INSIDE the recording-publisher container: the worker image (decoder,
recording reader), the publisher's ArtifactStore settings and the reference
cache mounted read-only. It reads no source dataset. Prints one JSON line.

    python verify_lidar_payload_decode.py --uri <payload uri> \
        --recording /reference/<corpus>/recordings/<fixture>-<key>.mcap
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

import numpy as np
from mcap import records
from mcap_ros2.decoder import DecoderFactory
from sceneops_storage import create_artifact_store

from sceneops_integrations.recording import iter_recording_messages
from sceneops_integrations.recording.cli import RecordingPublisherSettings
from sceneops_worker.inference.detection.pointcloud2 import (
    POINTCLOUD2_CDR_MEDIA_TYPE,
    decode_lidar_xyz,
)

FLOAT32 = 7


def recorded_xyz(message: Any) -> np.ndarray:
    """Finite xyz of a recorded PointCloud2, read with the official MCAP ROS 2
    decoder, not the worker's."""
    schema = records.Schema(
        id=1,
        name=message.schema_name,
        encoding=message.schema_encoding,
        data=message.schema_data,
    )
    decoded = DecoderFactory().decoder_for(message.message_encoding, schema)(
        message.data
    )
    fields = {f.name: f for f in decoded.fields}
    assert not decoded.is_bigendian, "big-endian clouds are not part of the corpus"
    assert all(fields[a].datatype == FLOAT32 for a in "xyz"), "x, y, z are float32"
    dtype = np.dtype(
        {
            "names": ["x", "y", "z"],
            "formats": ["<f4"] * 3,
            "offsets": [fields[a].offset for a in "xyz"],
            "itemsize": decoded.point_step,
        }
    )
    points = np.frombuffer(
        bytes(decoded.data), dtype=dtype, count=decoded.width * decoded.height
    )
    xyz = np.stack([points["x"], points["y"], points["z"]], axis=1)
    return xyz[np.isfinite(xyz).all(axis=1)].astype(np.float64)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--uri", required=True)
    parser.add_argument("--recording", type=Path, required=True)
    parser.add_argument("--topic", default="/lidar/top/points")
    args = parser.parse_args()

    store = create_artifact_store(RecordingPublisherSettings().artifact)
    payload = await store.read_bytes(args.uri)
    xyz = decode_lidar_xyz(POINTCLOUD2_CDR_MEDIA_TYPE, payload)

    matches = [
        m
        for m in iter_recording_messages(args.recording, topics=[args.topic])
        if m.data == payload
    ]
    points_equal = bool(matches) and np.array_equal(recorded_xyz(matches[0]), xyz)
    print(
        json.dumps(
            {
                "point_count": int(xyz.shape[0]),
                "recorded_messages_equal_to_payload": len(matches),
                "matches_recording": len(matches) == 1 and points_equal,
                "recording_channel_index": matches[0].channel_index
                if matches
                else None,
                "recording_log_time_ns": matches[0].log_time_ns if matches else None,
            }
        )
    )


asyncio.run(main())
