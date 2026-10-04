"""The one internal reader of an L1 recording's messages (ADR-007 §29.6).

Canonicalization reads a resolved recording only through this module. It
turns an MCAP into a stream of messages, each with its preserved facts:

    topic · schema (name, encoding) · message encoding · payload bytes
    log_time      recorder receive time (clock "mcap_log_time")
    publish_time  upstream publication time (clock "mcap_publish_time")
    sequence      MCAP Message.sequence where the writer set one, else None
    acquisition_index   position in file (write) order, whole recording
    channel_index       occurrence index among the messages of the same topic,
                        in that topic's acquisition order; independent of how
                        other topics' messages are interleaved

Source-semantic timestamps stay inside the payload; :class:`Ros2Decoder`
decodes a message with the schema embedded in the recording and
:func:`header_stamps` returns the stamps a decoded message carries. Nothing
here maps topics to meanings, picks a canonical time or knows a source
format: those are the builders' decisions. Messages are read with the MCAP
stream reader, in file order, independent of chunk indexes.

``mcap`` / ``mcap_ros2`` are imported lazily, like :mod:`.facts`.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

ROS2_MESSAGE_ENCODING: Final = "cdr"
ROS2_SCHEMA_ENCODING: Final = "ros2msg"
TF_SCHEMA: Final = "tf2_msgs/msg/TFMessage"


class RecordingReadError(RuntimeError):
    """The recording cannot be read or decoded as declared."""


@dataclass(frozen=True)
class RecordingMessage:
    topic: str
    schema_name: str
    schema_encoding: str
    schema_data: bytes
    message_encoding: str
    data: bytes
    log_time_ns: int
    publish_time_ns: int
    sequence: int | None
    acquisition_index: int
    channel_index: int


def stamp_ns(stamp: Any) -> int:
    """``builtin_interfaces/msg/Time`` as integer nanoseconds."""
    return int(stamp.sec) * 1_000_000_000 + int(stamp.nanosec)


def header_stamps(decoded: Any, schema_name: str) -> list[Any]:
    """The ``std_msgs/msg/Header`` values a decoded message carries: one
    per transform of a ``TFMessage``, the message's own header otherwise,
    none if it has no header."""
    if schema_name == TF_SCHEMA:
        return [t.header for t in decoded.transforms]
    header = getattr(decoded, "header", None)
    return [header] if header is not None and hasattr(header, "stamp") else []


def iter_recording_messages(
    path: Path, *, topics: Iterable[str] | None = None
) -> Iterator[RecordingMessage]:
    """Every message of the MCAP at ``path`` in file order, optionally only
    those on ``topics``. ``acquisition_index`` / ``channel_index`` count
    every message, selected or not, so they are properties of the recording
    and never of the selection."""
    from mcap import records
    from mcap.stream_reader import StreamReader

    wanted = None if topics is None else frozenset(topics)
    schemas: dict[int, Any] = {}
    channels: dict[int, Any] = {}
    channel_counts: dict[str, int] = {}
    index = 0
    try:
        with path.open("rb") as stream:
            for record in StreamReader(stream, validate_crcs=True).records:
                if isinstance(record, records.Schema):
                    schemas[record.id] = record
                elif isinstance(record, records.Channel):
                    channels[record.id] = record
                elif isinstance(record, records.Message):
                    channel = channels.get(record.channel_id)
                    if channel is None:
                        raise RecordingReadError(
                            f"message {index} references undefined channel "
                            f"{record.channel_id}"
                        )
                    channel_index = channel_counts.get(channel.topic, 0)
                    channel_counts[channel.topic] = channel_index + 1
                    acquisition_index = index
                    index += 1
                    if wanted is not None and channel.topic not in wanted:
                        continue
                    schema = schemas.get(channel.schema_id)
                    yield RecordingMessage(
                        topic=channel.topic,
                        schema_name=schema.name if schema is not None else "",
                        schema_encoding=schema.encoding if schema is not None else "",
                        schema_data=bytes(schema.data) if schema is not None else b"",
                        message_encoding=channel.message_encoding,
                        data=bytes(record.data),
                        log_time_ns=int(record.log_time),
                        publish_time_ns=int(record.publish_time),
                        sequence=int(record.sequence) or None,
                        acquisition_index=acquisition_index,
                        channel_index=channel_index,
                    )
    except RecordingReadError:
        raise
    except Exception as exc:  # noqa: BLE001 - any read failure is fatal here
        raise RecordingReadError(f"cannot read recording {path}: {exc}") from exc


class Ros2Decoder:
    """Decodes ROS 2 (``cdr`` / ``ros2msg``) messages with the schema the
    recording embeds. Any other encoding is unsupported and fails loudly;
    it is never skipped (§29.5 encoding profile)."""

    def __init__(self) -> None:
        self._factory: Any = None
        self._decoders: dict[tuple[str, bytes], Any] = {}

    def decode(self, message: RecordingMessage) -> Any:
        if (
            message.message_encoding != ROS2_MESSAGE_ENCODING
            or message.schema_encoding != ROS2_SCHEMA_ENCODING
        ):
            raise RecordingReadError(
                f"{message.topic!r} uses message_encoding="
                f"{message.message_encoding!r} schema_encoding="
                f"{message.schema_encoding!r}; only "
                f"{ROS2_MESSAGE_ENCODING!r}/{ROS2_SCHEMA_ENCODING!r} is supported"
            )
        key = (message.schema_name, message.schema_data)
        decoder = self._decoders.get(key)
        if decoder is None:
            decoder = self._decoder_for(message)
            self._decoders[key] = decoder
        try:
            return decoder(message.data)
        except Exception as exc:  # noqa: BLE001 - reported with context
            raise RecordingReadError(
                f"message {message.acquisition_index} on {message.topic!r} does not "
                f"decode as {message.schema_name!r}: {exc}"
            ) from exc

    def _decoder_for(self, message: RecordingMessage) -> Any:
        from mcap.records import Schema

        if self._factory is None:
            from mcap_ros2.decoder import DecoderFactory

            self._factory = DecoderFactory()
        # mcap_ros2 caches decoders by schema id, so every distinct schema
        # needs its own id.
        schema = Schema(
            id=len(self._decoders) + 1,
            name=message.schema_name,
            encoding=message.schema_encoding,
            data=message.schema_data,
        )
        try:
            decoder = self._factory.decoder_for(message.message_encoding, schema)
        except Exception as exc:  # noqa: BLE001 - reported with context
            raise RecordingReadError(
                f"schema {message.schema_name!r} of {message.topic!r} does not "
                f"parse: {exc}"
            ) from exc
        if decoder is None:
            raise RecordingReadError(
                f"no decoder for schema {message.schema_name!r} of {message.topic!r}"
            )
        return decoder


__all__ = [
    "ROS2_MESSAGE_ENCODING",
    "ROS2_SCHEMA_ENCODING",
    "TF_SCHEMA",
    "RecordingMessage",
    "RecordingReadError",
    "Ros2Decoder",
    "header_stamps",
    "iter_recording_messages",
    "stamp_ns",
]
