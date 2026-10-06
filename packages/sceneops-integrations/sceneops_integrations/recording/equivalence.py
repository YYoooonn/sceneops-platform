"""Semantic acquisition equivalence of two L1 recordings (ADR-007 §29.12).

One logical source can be acquired in batch or by stream replay. The two
recordings legitimately differ in bytes, chunking, receive times, write
order across channels and acquisition provenance. They are *semantically
equivalent* when, over the channels both include (and, here, over every
channel -- a channel in only one is reported):

    each channel has the same (schema name, schema encoding, message encoding)
    each channel holds the same messages as a multiset, compared by
        sha256(payload) -- source observation timestamps live inside the
        payload, so the checksum covers them. Duplicates are counted, never
        collapsed.
    where both recordings carry sequence information for a channel, the
        channel's messages in sequence order are the same ordered list

Ignored: ``log_time``, ``publish_time`` (a transport or replay time),
cross-channel write order, MCAP sequence *values* (only per-channel order),
schema definition text, metadata and attachments (acquisition origin).

This is the executable form of the §29.12 equivalence relation; canonical
equivalence (I-35) is checked on the built manifests.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .reader import iter_recording_messages


@dataclass(frozen=True)
class ChannelContent:
    schema_name: str
    schema_encoding: str
    message_encoding: str
    message_count: int
    # Counter of payload checksums (the multiset).
    payloads: Counter[str]
    # Payload checksums in sequence order; None unless every message of the
    # channel carries a sequence.
    sequenced_order: tuple[str, ...] | None


@dataclass(frozen=True)
class RecordingContent:
    channels: dict[str, ChannelContent]


@dataclass(frozen=True)
class EquivalenceReport:
    differences: tuple[str, ...] = field(default_factory=tuple)
    channel_count: int = 0
    message_count: int = 0

    @property
    def equivalent(self) -> bool:
        return not self.differences

    def to_dict(self) -> dict[str, object]:
        return {
            "equivalent": self.equivalent,
            "channel_count": self.channel_count,
            "message_count": self.message_count,
            "differences": list(self.differences),
        }


def semantic_recording_content(path: Path) -> RecordingContent:
    """The comparison projection of the recording at ``path``."""
    shape: dict[str, tuple[str, str, str]] = {}
    payloads: dict[str, Counter[str]] = {}
    ordered: dict[str, list[tuple[int | None, int, str]]] = {}
    for message in iter_recording_messages(path):
        topic = message.topic
        shape.setdefault(
            topic,
            (message.schema_name, message.schema_encoding, message.message_encoding),
        )
        digest = hashlib.sha256(message.data).hexdigest()
        payloads.setdefault(topic, Counter())[digest] += 1
        ordered.setdefault(topic, []).append(
            (message.sequence, message.acquisition_index, digest)
        )
    channels: dict[str, ChannelContent] = {}
    for topic, (schema, schema_encoding, message_encoding) in shape.items():
        entries = ordered[topic]
        sequenced = all(sequence is not None for sequence, _, _ in entries)
        channels[topic] = ChannelContent(
            schema_name=schema,
            schema_encoding=schema_encoding,
            message_encoding=message_encoding,
            message_count=len(entries),
            payloads=payloads[topic],
            sequenced_order=(
                tuple(d for _, _, d in sorted(entries, key=lambda e: (e[0], e[1])))
                if sequenced
                else None
            ),
        )
    return RecordingContent(channels=channels)


def compare_recording_contents(
    a: RecordingContent, b: RecordingContent
) -> EquivalenceReport:
    """Whether two comparison projections are semantically equivalent, with
    every difference named."""
    differences: list[str] = []
    for topic in sorted(set(a.channels) ^ set(b.channels)):
        side = "first" if topic in a.channels else "second"
        differences.append(f"channel {topic!r} only in the {side} recording")
    for topic in sorted(set(a.channels) & set(b.channels)):
        x, y = a.channels[topic], b.channels[topic]
        if (x.schema_name, x.schema_encoding, x.message_encoding) != (
            y.schema_name,
            y.schema_encoding,
            y.message_encoding,
        ):
            differences.append(
                f"{topic!r}: type/encoding differ: "
                f"{(x.schema_name, x.schema_encoding, x.message_encoding)} vs "
                f"{(y.schema_name, y.schema_encoding, y.message_encoding)}"
            )
        if x.payloads != y.payloads:
            missing = sum((x.payloads - y.payloads).values())
            extra = sum((y.payloads - x.payloads).values())
            differences.append(
                f"{topic!r}: message multisets differ "
                f"({x.message_count} vs {y.message_count}; {missing} only in "
                f"first, {extra} only in second)"
            )
        elif (
            x.sequenced_order is not None
            and y.sequenced_order is not None
            and x.sequenced_order != y.sequenced_order
        ):
            differences.append(f"{topic!r}: per-channel sequence order differs")
    return EquivalenceReport(
        differences=tuple(differences),
        channel_count=len(set(a.channels) | set(b.channels)),
        message_count=sum(c.message_count for c in a.channels.values()),
    )


def compare_recordings(first: Path, second: Path) -> EquivalenceReport:
    """Whether the recordings at ``first`` and ``second`` are semantically
    equivalent, with every difference named."""
    return compare_recording_contents(
        semantic_recording_content(first), semantic_recording_content(second)
    )


__all__ = [
    "ChannelContent",
    "EquivalenceReport",
    "RecordingContent",
    "compare_recording_contents",
    "compare_recordings",
    "semantic_recording_content",
]
