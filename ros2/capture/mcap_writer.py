"""MCAP writer backing the durable capture consumer.

Uses ``rosbag2_py.SequentialWriter`` (the exact writer ``ros2 bag record``
itself uses, via the same ``mcap`` storage plugin) rather than a
hand-rolled MCAP encoder -- this is what gives the produced file its ROS2/
rosbag2 ecosystem compatibility (standard profile, ``ros2msg`` schema
encoding resolved from the installed ROS2 interface definitions, ``cdr``
message encoding) for free, with zero hand-crafted schema text.

Payload bytes are passed straight through to ``SequentialWriter.write()``
without deserializing or reserializing -- verified empirically that this
writer stores the exact bytes given it (Kafka record value ==
``TelemetryEnvelope.payload`` == the resulting ``Message.data``).

Time mapping (frozen; see docs/architecture/streaming-transport.md's MCAP
capture section for the full audit this is based on):

    MCAP log_time      = TelemetryEnvelope.source_timestamp_ns
    MCAP publish_time   = TelemetryEnvelope.ingest_timestamp_ns

This is the inverse of the naive publish_time/log_time assignment,
chosen because the existing ``RosbagAdapter`` (apps/worker) reads ONLY
``message.log_time`` for every timestamp it derives (RobotState,
Mission, frame, and raw-log time-range) and never reads
``publish_time`` at all -- putting the envelope's real
source-observation/event time in the one field actually read preserves
its meaning downstream; putting the less-consumed transport-ingest time
there instead would silently discard it behind a field nothing reads.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import rosbag2_py

from sceneops_core.streaming import TelemetryEnvelope

from schema_registry import SUPPORTED_CHANNELS


class UnsupportedChannelError(ValueError):
    """An envelope named a channel/message_type combination outside the
    explicit v1 supported set (schema_registry.SUPPORTED_CHANNELS)."""


@dataclass
class WriterStats:
    message_count: int = 0
    per_channel_counts: dict[str, int] = field(default_factory=dict)


class McapCaptureWriter:
    """Writes ``TelemetryEnvelope`` records into one rosbag2/MCAP bag
    directory. Not responsible for temp/final path lifecycle (see
    ``finalize.py``) or for Kafka consumption (see ``capture_consumer.py``)
    -- this class only knows how to turn envelopes into MCAP messages."""

    def __init__(self, *, bag_uri: str) -> None:
        self._bag_uri = bag_uri
        self._writer = rosbag2_py.SequentialWriter()
        storage_options = rosbag2_py.StorageOptions(uri=bag_uri, storage_id="mcap")
        converter_options = rosbag2_py.ConverterOptions(
            input_serialization_format="cdr", output_serialization_format="cdr"
        )
        self._writer.open(storage_options, converter_options)
        self._created_topics: set[str] = set()
        self.stats = WriterStats()

    def write_envelope(self, envelope: TelemetryEnvelope) -> None:
        expected_type = SUPPORTED_CHANNELS.get(envelope.channel)
        if expected_type is None or expected_type != envelope.message_type:
            raise UnsupportedChannelError(
                f"unsupported channel/message_type: {envelope.channel!r} / "
                f"{envelope.message_type!r} (supported: {SUPPORTED_CHANNELS})"
            )

        if envelope.channel not in self._created_topics:
            self._writer.create_topic(
                rosbag2_py.TopicMetadata(
                    id=len(self._created_topics),
                    name=envelope.channel,
                    type=envelope.message_type,
                    serialization_format="cdr",
                )
            )
            self._created_topics.add(envelope.channel)

        # log_time/publish_time both come from already-carried envelope
        # fields -- never a new capture-receive-time clock (see module
        # docstring for the frozen mapping and its rationale).
        self._writer.write(
            envelope.channel,
            envelope.payload,
            envelope.source_timestamp_ns,
            envelope.ingest_timestamp_ns,
        )
        self.stats.message_count += 1
        self.stats.per_channel_counts[envelope.channel] = (
            self.stats.per_channel_counts.get(envelope.channel, 0) + 1
        )

    def close(self) -> None:
        """Close the writer and fsync every file the bag directory now
        contains, plus the directory entry itself -- durability up to
        this point does not depend on the OS ever flushing dirty pages
        on its own schedule."""
        self._writer.close()
        bag_dir = Path(self._bag_uri)
        for entry in bag_dir.iterdir():
            if entry.is_file():
                fd = os.open(entry, os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
        dir_fd = os.open(bag_dir, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)

    def mcap_file_path(self) -> str:
        """The single .mcap file rosbag2 writes inside the bag directory
        -- rosbag2's own naming convention, ``<bag_name>_0.mcap``."""
        bag_name = Path(self._bag_uri).name
        return str(Path(self._bag_uri) / f"{bag_name}_0.mcap")
