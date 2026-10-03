"""Recording facts derived from finalized MCAP bytes.

One implementation shared by the Recording Publisher (deriving the facts it
writes into the RobotRunManifest) and by ``REGISTER_ROBOT_RUN`` (re-deriving
them from the published bytes and requiring equality with the manifest), so
publication and registration can never disagree about what a recording
contains.

The ``mcap`` import is lazy, matching this package's SDK-import convention:
importing ``sceneops_integrations.recording`` never requires it, only
deriving facts does.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import IO

from sceneops_core.robots.clock import MCAP_LOG_TIME_CLOCK
from sceneops_core.robots.manifest import ChannelFact

# v1 derives started_at/ended_at from MCAP message log_time only; any other
# clock would make those facts mean something the bytes cannot prove.
SUPPORTED_SOURCE_CLOCKS = frozenset({MCAP_LOG_TIME_CLOCK})

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class RecordingValidationError(ValueError):
    """The recording is not a readable, non-empty MCAP with well-defined
    channel facts."""


@dataclass(frozen=True)
class RecordingFacts:
    started_at: datetime
    ended_at: datetime
    channels: list[ChannelFact]
    message_count: int


def sha256_checksum(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _ns_to_datetime(timestamp_ns: int) -> datetime:
    # Floor division truncates sub-microsecond precision toward negative
    # infinity (ADR-007 §8.3).
    return _EPOCH + timedelta(microseconds=timestamp_ns // 1000)


def derive_mcap_facts(stream: IO[bytes], *, source_clock: str) -> RecordingFacts:
    """Scan every record of an MCAP stream (CRC-validated) and derive its
    publication facts.

    Channels are grouped by ``(topic, message_encoding, schema_name,
    schema_encoding)``; a topic that appears under two different channel
    definitions is rejected because v1 manifests key channels by topic.
    Raises :class:`RecordingValidationError` for unreadable/corrupt input,
    zero messages, or an unsupported source clock.
    """
    if source_clock not in SUPPORTED_SOURCE_CLOCKS:
        raise RecordingValidationError(
            f"unsupported source_clock {source_clock!r}; "
            f"supported: {sorted(SUPPORTED_SOURCE_CLOCKS)}"
        )

    from mcap.reader import NonSeekingReader

    counts: dict[tuple[str, str, str, str], int] = {}
    min_ns: int | None = None
    max_ns: int | None = None
    try:
        reader = NonSeekingReader(stream, validate_crcs=True)
        for schema, channel, message in reader.iter_messages(log_time_order=False):
            key = (
                channel.topic,
                channel.message_encoding,
                schema.name if schema is not None else "",
                schema.encoding if schema is not None else "",
            )
            counts[key] = counts.get(key, 0) + 1
            log_time = message.log_time
            min_ns = log_time if min_ns is None else min(min_ns, log_time)
            max_ns = log_time if max_ns is None else max(max_ns, log_time)
    except Exception as exc:
        raise RecordingValidationError(f"MCAP is unreadable or corrupt: {exc}") from exc

    if min_ns is None or max_ns is None:
        raise RecordingValidationError("MCAP contains zero messages")

    channels = [
        ChannelFact(
            topic=topic,
            message_encoding=message_encoding,
            schema_name=schema_name,
            schema_encoding=schema_encoding,
            message_count=count,
        )
        for (topic, message_encoding, schema_name, schema_encoding), count in sorted(
            counts.items()
        )
    ]
    topics = [channel.topic for channel in channels]
    if len(topics) != len(set(topics)):
        duplicated = sorted({t for t in topics if topics.count(t) > 1})
        raise RecordingValidationError(
            f"topics published under more than one channel definition are not "
            f"supported by RobotRunManifest v1: {duplicated}"
        )

    return RecordingFacts(
        started_at=_ns_to_datetime(min_ns),
        ended_at=_ns_to_datetime(max_ns),
        channels=channels,
        message_count=sum(counts.values()),
    )
