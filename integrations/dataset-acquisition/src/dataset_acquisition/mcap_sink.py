"""Batch sink: acquisition events -> one finalized, L1-conformant MCAP
(ADR-007 §29.5).

The sink plays the recorder. It has no live reception, so it writes a
deterministic *simulated receive time*:

    log_time      = the event's source time (``AcquisitionEvent.source_time_ns``)
    publish_time  = log_time
    sequence      = the event's publisher-side counter (0 when it has none)

It never reads the wall clock, so equal events give equal recordings. The
payload is written exactly as the adapter serialized it; source observation
timestamps inside it are never touched.

Output is write-once: the MCAP is written to ``<output>.partial``, fsynced
and atomically renamed, so a crash never leaves a file at ``output`` that
looks finalized. An existing ``output`` is never overwritten.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from mcap.writer import CompressionType, Writer

from . import __version__
from .events import AcquisitionError, AcquisitionEvent

MCAP_PROFILE = "ros2"
ACQUISITION_ORIGIN_METADATA = "sceneops.acquisition_origin"
LIBRARY = f"sceneops-dataset-acquisition/{__version__}"


def simulated_receive_time_ns(event: AcquisitionEvent) -> int:
    """The batch recorder's receive time: the source time itself, with zero
    simulated transport latency."""
    return event.source_time_ns


@dataclass
class RecordingSummary:
    path: Path
    sha256: str
    size_bytes: int
    message_count: int = 0
    first_log_time_ns: int | None = None
    last_log_time_ns: int | None = None
    topic_counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "message_count": self.message_count,
            "first_log_time_ns": self.first_log_time_ns,
            "last_log_time_ns": self.last_log_time_ns,
            "topic_counts": dict(sorted(self.topic_counts.items())),
        }


def _fsync_path(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_mcap(
    events: Iterable[AcquisitionEvent],
    output: Path,
    *,
    origin: Mapping[str, str] | None = None,
) -> RecordingSummary:
    """Write ``events`` (in order) to a finalized MCAP at ``output``.

    Raises :class:`AcquisitionError` if ``output`` exists, if the stream is
    empty, if receive time would go backwards, or if one topic or type name
    is used with two different definitions.
    """
    output = Path(output)
    partial = output.with_name(output.name + ".partial")
    if output.exists():
        raise AcquisitionError(f"refusing to overwrite existing recording {output}")
    output.parent.mkdir(parents=True, exist_ok=True)

    try:
        # Exclusive create: a .partial left by a concurrent or interrupted
        # run is reported, never adopted or deleted.
        partial_stream = partial.open("xb")
    except FileExistsError as exc:
        raise AcquisitionError(
            f"{partial} exists (another or an interrupted run); remove it to retry"
        ) from exc

    counts: dict[str, int] = {}
    first: int | None = None
    last: int | None = None
    try:
        with partial_stream as stream:
            writer = Writer(stream, compression=CompressionType.ZSTD)
            writer.start(profile=MCAP_PROFILE, library=LIBRARY)
            schemas: dict[str, tuple[str, int]] = {}
            channels: dict[str, tuple[str, str, int]] = {}
            for event in events:
                schema = schemas.get(event.message_type.name)
                if schema is None:
                    schema_id = writer.register_schema(
                        name=event.message_type.name,
                        encoding=event.message_type.encoding,
                        data=event.message_type.definition.encode(),
                    )
                    schema = (event.message_type.definition, schema_id)
                    schemas[event.message_type.name] = schema
                elif schema[0] != event.message_type.definition:
                    raise AcquisitionError(
                        f"type {event.message_type.name!r} used with two definitions"
                    )

                channel = channels.get(event.topic)
                if channel is None:
                    channel_id = writer.register_channel(
                        topic=event.topic,
                        message_encoding=event.message_encoding,
                        schema_id=schema[1],
                    )
                    channel = (
                        event.message_type.name,
                        event.message_encoding,
                        channel_id,
                    )
                    channels[event.topic] = channel
                elif channel[:2] != (event.message_type.name, event.message_encoding):
                    raise AcquisitionError(
                        f"topic {event.topic!r} used with two message types "
                        f"({channel[0]}, {event.message_type.name})"
                    )

                log_time = simulated_receive_time_ns(event)
                if last is not None and log_time < last:
                    raise AcquisitionError(
                        f"events out of acquisition order on {event.topic!r}: "
                        f"{log_time} < {last}"
                    )
                writer.add_message(
                    channel_id=channel[2],
                    log_time=log_time,
                    publish_time=log_time,
                    data=event.payload,
                    sequence=event.sequence or 0,
                )
                first = log_time if first is None else first
                last = log_time
                counts[event.topic] = counts.get(event.topic, 0) + 1

            if not counts:
                raise AcquisitionError("the selected source produced no events")
            if origin:
                writer.add_metadata(
                    ACQUISITION_ORIGIN_METADATA, dict(sorted(origin.items()))
                )
            writer.finish()
            stream.flush()
            os.fsync(stream.fileno())
        os.rename(partial, output)
        _fsync_path(output.parent)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise

    digest = hashlib.sha256()
    with output.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return RecordingSummary(
        path=output,
        sha256=f"sha256:{digest.hexdigest()}",
        size_bytes=output.stat().st_size,
        message_count=sum(counts.values()),
        first_log_time_ns=first,
        last_log_time_ns=last,
        topic_counts=counts,
    )
