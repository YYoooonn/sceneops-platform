"""MCAP source: a finalized ROS 2 MCAP recording -> acquisition events
(ADR-007 §29.13).

The adapter turns an existing recording back into the event stream the sinks
consume, so a replay publishes the recording's own bytes. Everything is
derived from the MCAP itself:

* channels: topic, schema name / encoding / definition and message encoding,
  from the MCAP summary (channels that carry no message are not declared);
* payload: the message bytes, unchanged -- ``Header.stamp`` and every other
  field inside the payload are never touched;
* source time: the message's MCAP ``log_time``. For a batch recording the
  batch sink wrote ``log_time`` as the source time (simulated receive time
  with zero latency), so this recovers the timeline the events had. For a
  recording made by a live recorder it is the recorder's receive time.

Order is ``log_time`` ascending; messages with equal ``log_time`` keep their
position in the file (chunk offset, then record index), which for a batch
recording is the order the sink wrote them. This is what keeps same-instant
messages -- e.g. ``/tf_static`` and the first ``/tf`` -- in acquisition
order. It is the MCAP reader's documented ordering and is pinned by tests.

Reading streams one chunk at a time. An MCAP without a chunk index would make
the reader load the whole file, so it is rejected instead of materialized. The
reader's physical-order mode (``log_time_order=False``) is not used either: it
queues every chunk before yielding the first message.
The tool depends on no SceneOps package; this module knows nothing about
which recording it reads or where it came from.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from pathlib import Path

from mcap.exceptions import McapError
from mcap.reader import make_reader
from mcap.records import Channel, Schema

from .events import AcquisitionError, AcquisitionEvent, MessageType
from .mcap_sink import ACQUISITION_ORIGIN_METADATA
from .ros2 import CDR_MESSAGE_ENCODING


def _message_type(channel: Channel, schema: Schema | None) -> MessageType:
    if schema is None:
        raise AcquisitionError(f"{channel.topic!r}: channel has no schema")
    return MessageType(
        name=schema.name,
        encoding=schema.encoding,
        definition=schema.data.decode(),
    )


class McapAdapter:
    """Events of one finalized MCAP recording, in ``log_time`` order."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._channels: dict[str, MessageType] | None = None

    def _summary(self, reader):  # type: ignore[no-untyped-def]
        try:
            summary = reader.get_summary()
        except McapError as exc:
            raise AcquisitionError(
                f"{self._path}: not a finalized MCAP: {exc}"
            ) from exc
        if summary is None or summary.statistics is None:
            raise AcquisitionError(
                f"{self._path}: no MCAP summary statistics (not finalized?)"
            )
        if not summary.chunk_indexes:
            raise AcquisitionError(
                f"{self._path}: no chunk index; it cannot be streamed without "
                "loading the whole recording"
            )
        return summary

    def origin(self) -> Mapping[str, str]:
        """The acquisition-origin metadata the recording carries, if any."""
        with self._path.open("rb") as stream:
            for record in make_reader(stream).iter_metadata():
                if record.name == ACQUISITION_ORIGIN_METADATA:
                    return dict(record.metadata)
        return {}

    def channels(self) -> Mapping[str, MessageType]:
        if self._channels is None:
            with self._path.open("rb") as stream:
                summary = self._summary(make_reader(stream))
                counts = summary.statistics.channel_message_counts
                channels: dict[str, MessageType] = {}
                for channel_id, channel in sorted(summary.channels.items()):
                    if not counts.get(channel_id):
                        continue
                    if channel.message_encoding != CDR_MESSAGE_ENCODING:
                        raise AcquisitionError(
                            f"{channel.topic!r}: only {CDR_MESSAGE_ENCODING!r} "
                            f"payloads can be replayed, got {channel.message_encoding!r}"
                        )
                    if channel.topic in channels:
                        raise AcquisitionError(
                            f"{channel.topic!r} is declared by more than one channel"
                        )
                    schema = (
                        summary.schemas.get(channel.schema_id)
                        if channel.schema_id
                        else None
                    )
                    channels[channel.topic] = _message_type(channel, schema)
            self._channels = channels
        return self._channels

    def events(self) -> Iterator[AcquisitionEvent]:
        types = self.channels()
        with self._path.open("rb") as stream:
            for _, channel, message in make_reader(stream).iter_messages(
                log_time_order=True
            ):
                yield AcquisitionEvent(
                    topic=channel.topic,
                    message_type=types[channel.topic],
                    message_encoding=channel.message_encoding,
                    payload=message.data,
                    source_time_ns=message.log_time,
                    sequence=message.sequence,
                )
