"""``align_episode``: canonical Episode -> AlignedEpisode (ADR-007 §13.10,
§31.8). Derived (L3) and pure: no I/O, DB or ArtifactStore.

The canonical Episode keeps observation, state and action streams
asynchronous, each on its own declared clock. Every temporal decision is
made here, never in canonicalization:

    clock          one alignment clock (``TemporalSourceContext.source_clock``);
                   a stream on any other clock is rejected, never converted
    channels       observation and state fields -> observation channels, action
                   fields -> action channels, named ``<topic>#<field>``; an
                   observation payload is a reference channel named ``<topic>``.
                   Numeric fields align as scalars, numeric lists as vectors;
                   boolean and string fields and event streams are not aligned
    quantization   canonical ns -> integer us by floor division
    timeline       fixed frequency over the Episode window when aligning on the
                   window's clock ([start, end) -> [start_us, (end - 1)_us]),
                   otherwise over the aligned samples' extent
    association    per-channel policies (exact / nearest / previous /
                   linear_interpolation)

Deterministic: identical inputs always produce an identical output.
"""

from __future__ import annotations

from sceneops_core.episodes.recording_build import EpisodeStreamRole
from sceneops_core.episodes.schemas import (
    EpisodeManifest,
    EpisodeOccurrence,
    EpisodeStream,
)

from .config import TemporalAlignmentConfig, TemporalSourceContext
from .enums import AlignedValueKind, AssociationPolicy, ChannelNamespace
from .errors import ClockMismatchError, InvalidEpisodeBoundsError
from .policies import (
    ChannelSample,
    EffectiveChannelPolicy,
    apply_exact,
    apply_linear_interpolation,
    apply_nearest,
    apply_previous,
    canonicalize_samples,
    resolve_channel_policy,
)
from .schemas import AlignedEpisode, AlignedSignal, AlignedValue, LearningStep
from .semantics import ALIGNMENT_SEMANTICS_VERSION
from .timeline import generate_fixed_frequency_timeline

_NAMESPACE_BY_ROLE = {
    EpisodeStreamRole.OBSERVATION: ChannelNamespace.OBSERVATION,
    EpisodeStreamRole.STATE: ChannelNamespace.OBSERVATION,
    EpisodeStreamRole.ACTION: ChannelNamespace.ACTION,
}


def field_channel(topic: str, field_name: str) -> str:
    return f"{topic}#{field_name}"


def _field_value(value: object) -> AlignedValue | None:
    if isinstance(value, bool) or isinstance(value, str):
        return None
    if isinstance(value, (int, float)):
        return AlignedValue(kind=AlignedValueKind.NUMERIC_SCALAR, scalar=float(value))
    if isinstance(value, list):
        return AlignedValue(
            kind=AlignedValueKind.NUMERIC_VECTOR, vector=[float(v) for v in value]
        )
    return None


def _payload_value(
    stream: EpisodeStream, occurrence: EpisodeOccurrence
) -> AlignedValue:
    payload = occurrence.payload
    assert payload is not None
    return AlignedValue(
        kind=AlignedValueKind.REFERENCE,
        reference_channel=stream.topic,
        reference_metadata={
            "artifact_id": payload.artifact_id,
            "checksum": payload.checksum,
            "size_bytes": payload.size_bytes,
            "media_type": payload.media_type,
        },
    )


def alignment_samples(
    manifest: EpisodeManifest, clock: str
) -> dict[ChannelNamespace, dict[str, list[ChannelSample]]]:
    """The canonical streams as alignment channels on ``clock`` (unsorted,
    duplicates kept; ``canonicalize_samples`` orders them)."""
    channels: dict[ChannelNamespace, dict[str, list[ChannelSample]]] = {
        ChannelNamespace.OBSERVATION: {},
        ChannelNamespace.ACTION: {},
    }
    for role, namespace in _NAMESPACE_BY_ROLE.items():
        for stream in manifest.streams:
            if stream.role != role:
                continue
            if stream.source_clock != clock:
                raise ClockMismatchError(
                    f"stream {stream.topic!r} is on clock {stream.source_clock!r}; "
                    f"alignment on {clock!r} cannot place it"
                )
            target = channels[namespace]
            if stream.has_payload:
                target.setdefault(stream.topic, [])
            for field in stream.fields:
                target.setdefault(field_channel(stream.topic, field.name), [])
        for occurrence in manifest.occurrences_of(role):
            stream = manifest.stream(occurrence.topic)
            timestamp_us = occurrence.timestamp_ns // 1_000
            target = channels[namespace]
            if occurrence.payload is not None:
                target[stream.topic].append(
                    ChannelSample(timestamp_us, _payload_value(stream, occurrence))
                )
            for name, raw in occurrence.values.items():
                value = _field_value(raw)
                if value is None:
                    target.pop(field_channel(stream.topic, name), None)
                    continue
                key = field_channel(stream.topic, name)
                if key in target:
                    target[key].append(ChannelSample(timestamp_us, value))
    return channels


def align_episode(
    manifest: EpisodeManifest,
    config: TemporalAlignmentConfig,
    source_context: TemporalSourceContext,
    *,
    episode_id: str,
) -> AlignedEpisode:
    """``episode_id`` is the registered, DatasetVersion-scoped id of the
    Episode ``manifest`` describes. Never mutates any input."""
    raw = alignment_samples(manifest, source_context.source_clock)

    duplicate_discarded_count = 0
    samples: dict[ChannelNamespace, dict[str, list[ChannelSample]]] = {}
    for namespace, channels in raw.items():
        samples[namespace] = {}
        for channel, items in sorted(channels.items()):
            canonical, discarded = canonicalize_samples(items)
            duplicate_discarded_count += discarded
            if canonical:
                samples[namespace][channel] = canonical

    timestamps = [
        s.timestamp_us
        for channels in samples.values()
        for items in channels.values()
        for s in items
    ]
    if not timestamps:
        raise InvalidEpisodeBoundsError(
            "the Episode has no alignable occurrence on clock "
            f"{source_context.source_clock!r}"
        )
    window = manifest.declared_window()
    if window.source_clock == source_context.source_clock:
        start_us = window.start_timestamp_ns // 1_000
        end_us = (window.end_timestamp_ns - 1) // 1_000
    else:
        start_us, end_us = min(timestamps), max(timestamps)
    timeline = generate_fixed_frequency_timeline(
        start_timestamp_us=start_us,
        end_timestamp_us=end_us,
        target_frequency_hz=config.target_frequency_hz,
    )

    policies = {
        namespace: {
            channel: resolve_channel_policy(
                channel=channel,
                namespace=namespace,
                config=config,
                value_kind=items[0].payload.kind,
            )
            for channel, items in channels.items()
        }
        for namespace, channels in samples.items()
    }

    steps: list[LearningStep] = []
    for t in timeline.timestamps_us:
        resolved = {
            namespace: {
                channel: _apply_effective_policy(
                    channel=channel,
                    samples=items,
                    t=t,
                    effective=policies[namespace][channel],
                )
                for channel, items in channels.items()
            }
            for namespace, channels in samples.items()
        }
        steps.append(
            LearningStep(
                timestamp_us=t,
                observations=resolved[ChannelNamespace.OBSERVATION],
                actions=resolved[ChannelNamespace.ACTION],
            )
        )

    return AlignedEpisode(
        episode_id=episode_id,
        source_start_timestamp_us=start_us,
        source_end_timestamp_us=end_us,
        source_clock=source_context.source_clock,
        alignment_semantics_version=ALIGNMENT_SEMANTICS_VERSION,
        alignment_config=config,
        target_frequency_hz=config.target_frequency_hz,
        achieved_frequency_hz=timeline.achieved_frequency_hz,
        dt_us=timeline.dt_us,
        step_count=timeline.step_count,
        duplicate_discarded_count=duplicate_discarded_count,
        steps=steps,
    )


def _apply_effective_policy(
    *,
    channel: str,
    samples: list[ChannelSample],
    t: int,
    effective: EffectiveChannelPolicy,
) -> AlignedSignal:
    if effective.policy == AssociationPolicy.EXACT:
        return apply_exact(channel=channel, samples=samples, t=t)
    if effective.policy == AssociationPolicy.NEAREST:
        return apply_nearest(
            channel=channel, samples=samples, t=t, tolerance_us=effective.tolerance_us
        )
    if effective.policy == AssociationPolicy.PREVIOUS:
        return apply_previous(
            channel=channel, samples=samples, t=t, tolerance_us=effective.tolerance_us
        )
    if effective.policy == AssociationPolicy.LINEAR_INTERPOLATION:
        return apply_linear_interpolation(
            channel=channel, samples=samples, t=t, max_gap_us=effective.max_gap_us
        )
    raise AssertionError(
        f"unhandled association policy: {effective.policy!r}"
    )  # pragma: no cover
