"""Deterministic canonical EpisodeManifest builders for tests.

Test-support code only: production code must never import this module. It
lets core, DB, worker and API test suites build canonical Episodes from one
definition.

Describe an Episode as a list of occurrences (``observation`` / ``state`` /
``action`` / ``event``); :func:`episode_manifest` derives the streams (role,
field names, payload presence), ranks each stream's occurrences into
occurrence ids, chooses the window ``[earliest, latest + 1)`` unless given,
and signs the lineage with a valid producer fingerprint. Every stream uses
``clock`` unless an occurrence names its own.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from sceneops_core.artifacts.schemas.payload import PayloadRef
from sceneops_core.common.ids import robot_run_recording_artifact_id
from sceneops_core.provenance import (
    ProducerInfo,
    RecordingSegmentSource,
)

from .recording_build import EpisodeStreamRole
from .schemas import (
    EpisodeField,
    EpisodeLineage,
    EpisodeManifest,
    EpisodeOccurrence,
    EpisodeStream,
)

DEFAULT_PRODUCER_ID = "sceneops.test_episode_producer"
DEFAULT_CLOCK = "sensor.header_stamp"
DEFAULT_SCHEMA = "test_msgs/msg/Sample"


@dataclass(frozen=True)
class Occ:
    role: EpisodeStreamRole
    topic: str
    timestamp_ns: int
    values: dict[str, Any] = field(default_factory=dict)
    payload: bool = False
    clock: str | None = None


def observation(
    topic: str, t: int, *, payload: bool = True, clock=None, **values
) -> Occ:
    return Occ(EpisodeStreamRole.OBSERVATION, topic, t, values, payload, clock)


def state(topic: str, t: int, *, clock=None, **values) -> Occ:
    return Occ(EpisodeStreamRole.STATE, topic, t, values, False, clock)


def action(topic: str, t: int, *, clock=None, **values) -> Occ:
    return Occ(EpisodeStreamRole.ACTION, topic, t, values, False, clock)


def event(topic: str, t: int, *, clock=None, **values) -> Occ:
    return Occ(EpisodeStreamRole.EVENT, topic, t, values, False, clock)


def payload_ref(topic: str, rank: int) -> PayloadRef:
    data = f"payload:{topic}:{rank}".encode()
    return PayloadRef(
        artifact_id=f"payload-{hashlib.sha256(data).hexdigest()[:32]}",
        checksum="sha256:" + hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        media_type="image/jpeg",
    )


def _slug(topic: str) -> str:
    return topic.strip("/").replace("/", ".") or "topic"


def episode_manifest(
    occurrences: Sequence[Occ],
    *,
    robot_run_id: str = "run-001",
    unit_key: str = "recording",
    clock: str = DEFAULT_CLOCK,
    window: tuple[int, int] | None = None,
    window_clock: str | None = None,
    recording_checksum: str = "sha256:" + "1" * 64,
    build_config: dict[str, Any] | None = None,
    extra_streams: Sequence[EpisodeStream] = (),
) -> EpisodeManifest:
    streams: dict[str, EpisodeStream] = {s.topic: s for s in extra_streams}
    for occ in occurrences:
        if occ.topic in streams:
            continue
        streams[occ.topic] = EpisodeStream(
            topic=occ.topic,
            role=occ.role,
            schema_name=DEFAULT_SCHEMA,
            source_clock=occ.clock or clock,
            fields=[EpisodeField(name=n, path=n) for n in sorted(occ.values)],
            has_payload=occ.payload,
        )

    by_topic: dict[str, list[tuple[int, int, Occ]]] = {}
    for order, occ in enumerate(occurrences):
        by_topic.setdefault(occ.topic, []).append((occ.timestamp_ns, order, occ))
    lists: dict[EpisodeStreamRole, list[EpisodeOccurrence]] = {
        r: [] for r in EpisodeStreamRole
    }
    for topic, items in by_topic.items():
        for rank, (_, _, occ) in enumerate(sorted(items, key=lambda i: (i[0], i[1]))):
            lists[occ.role].append(
                EpisodeOccurrence(
                    occurrence_id=f"{_slug(topic)}-{rank:08d}",
                    topic=topic,
                    timestamp_ns=occ.timestamp_ns,
                    values=dict(occ.values),
                    payload=payload_ref(topic, rank) if occ.payload else None,
                )
            )

    window_clock = window_clock or clock
    if window is None:
        stamps = [
            o.timestamp_ns for o in occurrences if (o.clock or clock) == window_clock
        ] or [0]
        window = (min(stamps), max(stamps) + 1)
    source = RecordingSegmentSource(
        robot_run_id=robot_run_id,
        recording_artifact_id=robot_run_recording_artifact_id(robot_run_id),
        recording_checksum=recording_checksum,
        source_clock=window_clock,
        start_timestamp_ns=window[0],
        end_timestamp_ns=window[1],
        unit_key=unit_key,
    )
    producer = ProducerInfo.create(
        producer_id=DEFAULT_PRODUCER_ID,
        semantics_version=1,
        build_config=build_config or {"test": True},
        source=source.source_revision(),
    )

    def ordered(role: EpisodeStreamRole) -> list[EpisodeOccurrence]:
        return sorted(
            lists[role], key=lambda o: (o.topic, o.timestamp_ns, o.occurrence_id)
        )

    return EpisodeManifest(
        lineage=EpisodeLineage(source=source, producer=producer),
        streams=sorted(streams.values(), key=lambda s: s.topic),
        observations=ordered(EpisodeStreamRole.OBSERVATION),
        states=ordered(EpisodeStreamRole.STATE),
        actions=ordered(EpisodeStreamRole.ACTION),
        events=ordered(EpisodeStreamRole.EVENT),
    )


def semantic_episode_content(manifest: EpisodeManifest) -> dict[str, Any]:
    """Canonical semantic content of an Episode, without provenance-owned
    identity (robot_run_id, recording checksum, fingerprint, payload
    artifact ids): the projection two equivalent acquisitions must share
    when canonical time and segmentation come from source timestamps
    (§29.12, §30.9)."""
    data = manifest.model_dump(mode="json")
    source = data["lineage"]["source"]
    for occurrence in [
        *data["observations"],
        *data["states"],
        *data["actions"],
        *data["events"],
    ]:
        if occurrence["payload"] is not None:
            occurrence["payload"].pop("artifact_id")
    return {
        "build_config": data["lineage"]["producer"]["build_config"],
        "window": [
            source["source_clock"],
            source["start_timestamp_ns"],
            source["end_timestamp_ns"],
        ],
        "unit_key": source["unit_key"],
        **{
            k: data[k]
            for k in ("streams", "observations", "states", "actions", "events")
        },
    }


__all__ = [
    "DEFAULT_CLOCK",
    "Occ",
    "action",
    "episode_manifest",
    "event",
    "observation",
    "payload_ref",
    "semantic_episode_content",
    "state",
]
