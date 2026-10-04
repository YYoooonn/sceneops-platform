"""Pre-finalize validation of a just-written MCAP bag.

Reads the just-closed MCAP back with the same reader stack apps/worker's
``RosbagAdapter`` uses (the ``mcap`` package) -- never trust the writer's
own in-memory counters as proof the bytes landed correctly on disk. This
is the "validate" step in the frozen commit-boundary ordering: consume ->
write temp MCAP -> close -> fsync/validate -> atomically finalize -> fsync
parent dir -> commit Kafka offsets. A failure here must leave the bag in
its ``.partial`` location, unfinalized and uncommitted.
"""

from __future__ import annotations

from dataclasses import dataclass

from mcap.reader import make_reader


class McapValidationError(ValueError):
    """The just-written MCAP failed validation and must NOT be finalized."""


@dataclass
class ValidationResult:
    message_count: int
    first_log_time: int
    last_log_time: int


def recorded_stream(path: str) -> list[tuple]:
    """The recording's logical message stream in write order, without the
    recorder's own receive time: per message the topic, schema (name,
    encoding, text), message encoding, ``publish_time``, ``sequence`` and
    payload. Independent of chunking, compression and channel ids."""
    with open(path, "rb") as f:
        return [
            (
                channel.topic,
                schema.name,
                schema.encoding,
                schema.data,
                channel.message_encoding,
                message.publish_time,
                message.sequence,
                message.data,
            )
            for schema, channel, message in make_reader(f).iter_messages()
        ]


def same_recorded_content(first_path: str, second_path: str) -> bool:
    """Whether two recordings hold the same messages, ignoring only
    ``log_time``. A re-capture of the same Kafka records differs from the
    first attempt in receive time and in nothing else."""
    return recorded_stream(first_path) == recorded_stream(second_path)


def validate_mcap_file(path: str, *, expected_message_count: int) -> ValidationResult:
    message_count = 0
    first_log_time: int | None = None
    last_log_time: int | None = None

    with open(path, "rb") as f:
        try:
            reader = make_reader(f)
            for _schema, _channel, message in reader.iter_messages():
                message_count += 1
                if first_log_time is None:
                    first_log_time = message.log_time
                last_log_time = message.log_time
        except McapValidationError:
            raise
        except Exception as exc:
            # mcap raises varied exception types on truncated/corrupt input;
            # any of them means this file must not be finalized.
            raise McapValidationError(
                f"MCAP file is unreadable/corrupt: {path}: {exc}"
            ) from exc

    if message_count != expected_message_count:
        raise McapValidationError(
            f"MCAP message count mismatch after write: wrote "
            f"{expected_message_count}, read back {message_count} from {path}"
        )
    if message_count == 0:
        raise McapValidationError(
            f"MCAP file has zero messages, refusing to finalize: {path}"
        )

    assert first_log_time is not None and last_log_time is not None
    return ValidationResult(
        message_count=message_count,
        first_log_time=first_log_time,
        last_log_time=last_log_time,
    )
