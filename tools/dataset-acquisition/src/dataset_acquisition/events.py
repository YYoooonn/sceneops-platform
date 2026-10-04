"""Acquisition events: the tool-internal unit between a dataset adapter and
a sink (ADR-007 §29.13).

An event is one message as a robot would have published it: a topic, a
self-describing message type, the payload serialized exactly once, and the
source time the adapter took from the dataset. Sinks never re-serialize the
payload, so a batch MCAP and a future live replay carry identical bytes.

Events carry acquisition facts only. Nothing here names a Scene, an
Episode, a DatasetVersion, a canonical modality or a sampling decision.
This type is local to the tool; it is not a SceneOps abstraction.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class MessageType:
    """A message type as embedded in a recording: ``name`` such as
    ``sensor_msgs/msg/CompressedImage``, the schema ``encoding`` and its full
    ``definition`` text (dependencies included)."""

    name: str
    encoding: str
    definition: str


@dataclass(frozen=True)
class AcquisitionEvent:
    topic: str
    message_type: MessageType
    message_encoding: str
    payload: bytes
    # The time the source places this message at, in ns. Sinks pace replay
    # and simulate receive time from it. The message's own observation
    # timestamp (e.g. Header.stamp) is inside ``payload`` and is never
    # rewritten.
    source_time_ns: int
    # Publisher-side message counter on this topic, when the adapter has one.
    # The batch MCAP sink writes it as the MCAP sequence. A ROS 2 topic
    # carries no such counter, so the replay sink drops it; the platform's
    # bridge assigns its own transport sequence on arrival.
    sequence: int | None = None


class DatasetAdapter(Protocol):
    """Turns one selected unit of an external dataset into events."""

    def origin(self) -> Mapping[str, str]:
        """Acquisition-origin facts for inspection only (§29.8)."""
        ...

    def channels(self) -> Mapping[str, MessageType]:
        """Every topic the unit will publish and its message type, known
        before any event is produced (a live sink needs its publishers
        before it publishes)."""
        ...

    def events(self) -> Iterator[AcquisitionEvent]:
        """Events in acquisition order: ``source_time_ns`` never decreases,
        and the order is deterministic for identical inputs."""
        ...


class AcquisitionError(RuntimeError):
    """The source cannot be converted into a conformant recording."""
