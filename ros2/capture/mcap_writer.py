"""MCAP writer backing the durable capture consumer.

Writes one L1 raw recording (ADR-007 §29.5) with the official ``mcap``
writer in the ROS 2 profile: ``message_encoding = cdr``, ``schema_encoding =
ros2msg``, one channel per topic. ``rosbag2_py`` is not used because its
MCAP storage plugin cannot record an MCAP ``sequence`` or an independent
``publish_time`` per message, and the recording must preserve both.

Payload bytes are written exactly as the bridge forwarded them: no
deserialize, no reserialize (Kafka record value == ``TelemetryEnvelope.payload``
== ``Message.data``). Schema text comes from the installed ROS 2 interfaces
(``message_definition.py``).

Timing -- one fact per field, never one substituted for another:

    log_time      capture RECEIVE time: the wall-clock instant capture took
                  the record from Kafka (Unix epoch ns). The recorder's
                  clock; never a source timestamp. Clamped so it never
                  decreases in write order (a clock step must not break the
                  recording's receive order).
    publish_time  ``TelemetryEnvelope.ingest_timestamp_ns``: the TRANSPORT
                  ingest time, when the bridge accepted the message. It is
                  neither an observation time nor a robot-side publication
                  time (a raw ROS 2 subscription exposes no publisher
                  timestamp, and a DDS source timestamp would be the
                  publisher's own wall clock -- replay wall-clock for a
                  replay). It is the one upstream-of-capture time SceneOps's
                  transport observes, preserved here because a transport-level
                  timing fact must survive into the recording (§29.5 R4).
    source time   stays in the payload (Header.stamp, a JSON field),
                  untouched -- including a zero stamp. ``TelemetryEnvelope.
                  source_timestamp_ns`` is the bridge's verbatim copy of it
                  and is not written separately.

    sequence      ``TelemetryEnvelope.sequence_number + 1``: the bridge's
                  transport counter. MCAP reserves 0 for "no sequence", and
                  the counter starts at 0, so the stored value is one
                  higher. It is increasing within every channel, with gaps
                  where other channels' messages sit between.

Messages are written in the order consumed from the run's Kafka partition.
That order is acquisition evidence only; canonical temporal order is never
inferred from it.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from mcap.writer import CompressionType, Writer

from sceneops_core.streaming import DEFAULT_REGISTRY, ChannelRegistry, TelemetryEnvelope

from message_definition import message_definition

MCAP_PROFILE = "ros2"
SCHEMA_ENCODING = "ros2msg"
MESSAGE_ENCODING = "cdr"
LIBRARY = "sceneops-capture"


class UnsupportedChannelError(ValueError):
    """An envelope named a channel/message_type combination outside the
    channel registry."""


@dataclass
class WriterStats:
    message_count: int = 0
    per_channel_counts: dict[str, int] = field(default_factory=dict)


class McapCaptureWriter:
    """Writes ``TelemetryEnvelope`` records into one MCAP file inside a bag
    directory. Not responsible for temp/final path lifecycle (see
    ``finalize.py``) or for Kafka consumption (see ``capture_consumer.py``)
    -- this class only knows how to turn envelopes into MCAP messages."""

    def __init__(
        self, *, bag_uri: str, registry: ChannelRegistry = DEFAULT_REGISTRY
    ) -> None:
        self._bag_uri = bag_uri
        self._registry = registry
        bag_dir = Path(bag_uri)
        bag_dir.mkdir(parents=True, exist_ok=False)
        self._stream = open(self.mcap_file_path(), "xb")
        self._writer = Writer(self._stream, compression=CompressionType.ZSTD)
        self._writer.start(profile=MCAP_PROFILE, library=LIBRARY)
        self._schema_ids: dict[str, int] = {}
        self._channel_ids: dict[str, int] = {}
        self._last_log_time_ns: int | None = None
        self._closed = False
        self.stats = WriterStats()

    def write_envelope(
        self, envelope: TelemetryEnvelope, *, receive_time_ns: int | None = None
    ) -> None:
        if not self._registry.is_supported(
            channel=envelope.channel, message_type=envelope.message_type
        ):
            raise UnsupportedChannelError(
                f"unsupported channel/message_type: {envelope.channel!r} / "
                f"{envelope.message_type!r} (supported: "
                f"{self._registry.message_types()})"
            )

        channel_id = self._channel_ids.get(envelope.channel)
        if channel_id is None:
            schema_id = self._schema_ids.get(envelope.message_type)
            if schema_id is None:
                schema_id = self._writer.register_schema(
                    name=envelope.message_type,
                    encoding=SCHEMA_ENCODING,
                    data=message_definition(envelope.message_type).encode(),
                )
                self._schema_ids[envelope.message_type] = schema_id
            channel_id = self._writer.register_channel(
                topic=envelope.channel,
                message_encoding=MESSAGE_ENCODING,
                schema_id=schema_id,
            )
            self._channel_ids[envelope.channel] = channel_id

        log_time = time.time_ns() if receive_time_ns is None else receive_time_ns
        if self._last_log_time_ns is not None:
            log_time = max(log_time, self._last_log_time_ns)
        self._last_log_time_ns = log_time

        self._writer.add_message(
            channel_id=channel_id,
            log_time=log_time,
            publish_time=envelope.ingest_timestamp_ns,
            data=envelope.payload,
            sequence=envelope.sequence_number + 1,
        )
        self.stats.message_count += 1
        self.stats.per_channel_counts[envelope.channel] = (
            self.stats.per_channel_counts.get(envelope.channel, 0) + 1
        )

    def close(self) -> None:
        """Finish the MCAP (footer, summary) and fsync the file and its
        directory entry -- durability up to this point does not depend on
        the OS ever flushing dirty pages on its own schedule. Idempotent."""
        if self._closed:
            return
        self._closed = True
        try:
            self._writer.finish()
            self._stream.flush()
            os.fsync(self._stream.fileno())
        finally:
            self._stream.close()
        dir_fd = os.open(self._bag_uri, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)

    def mcap_file_path(self) -> str:
        """The single .mcap file inside the bag directory,
        ``<bag_name>_0.mcap``."""
        bag_name = Path(self._bag_uri).name
        return str(Path(self._bag_uri) / f"{bag_name}_0.mcap")
