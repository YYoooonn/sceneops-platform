from __future__ import annotations

from sceneops_core.episodes.schemas import (
    EpisodeActionFrame,
    EpisodeManifest,
    EpisodeObservationFrame,
)

from .config import TemporalAlignmentConfig, TemporalSourceContext
from .enums import AlignedValueKind, AssociationPolicy, ChannelNamespace
from .errors import UnknownChannelError
from .policies import (
    ChannelSample,
    EffectiveChannelPolicy,
    apply_exact,
    apply_linear_interpolation,
    apply_nearest,
    apply_previous,
    canonicalize_samples,
    group_frames_by_channel,
    resolve_channel_policy,
)
from .schemas import AlignedEpisode, AlignedSignal, AlignedValue, LearningStep
from .semantics import ALIGNMENT_SEMANTICS_VERSION
from .timeline import generate_fixed_frequency_timeline

_ORIENTATION_CHANNEL = "state.orientation"
_NUMERIC_VECTOR_STATE_CHANNELS = frozenset(
    {"state.position", "state.velocity", "state.acceleration"}
)
_NUMERIC_SCALAR_STATE_CHANNELS = frozenset({"state.battery"})


def align_episode(
    manifest: EpisodeManifest,
    config: TemporalAlignmentConfig,
    source_context: TemporalSourceContext,
) -> AlignedEpisode:
    """Given an EpisodeManifest, an explicit TemporalAlignmentConfig, and
    explicit temporal-source provenance, deterministically produce an
    AlignedEpisode. Pure domain transformation — zero I/O, zero DB, zero
    ArtifactStore, zero worker/job dependency (SceneOps V2 Request 2.2).

    Deterministic: identical inputs always produce an identical output.
    Never mutates ``manifest``, its frame lists, ``config``, or
    ``source_context``.
    """
    start_us = manifest.start_timestamp_us
    end_us = manifest.end_timestamp_us
    timeline = generate_fixed_frequency_timeline(
        start_timestamp_us=start_us,
        end_timestamp_us=end_us,
        target_frequency_hz=config.target_frequency_hz,
    )
    # generate_fixed_frequency_timeline raises InvalidEpisodeBoundsError
    # when either bound is None — both are guaranteed non-None below.
    assert start_us is not None and end_us is not None

    duplicate_discarded_count = 0

    observation_samples: dict[str, list[ChannelSample]] = {}
    for channel, frames in group_frames_by_channel(manifest.observation_frames).items():
        samples = [
            ChannelSample(timestamp_us=f.timestamp_us, payload=_observation_value(f))
            for f in frames
        ]
        canonical, discarded = canonicalize_samples(samples)
        duplicate_discarded_count += discarded
        observation_samples[channel] = canonical

    action_samples: dict[str, list[ChannelSample]] = {}
    for channel, frames in group_frames_by_channel(manifest.action_frames).items():
        samples = [
            ChannelSample(timestamp_us=f.timestamp_us, payload=_action_value(f))
            for f in frames
        ]
        canonical, discarded = canonicalize_samples(samples)
        duplicate_discarded_count += discarded
        action_samples[channel] = canonical

    observation_policies = {
        channel: resolve_channel_policy(
            channel=channel,
            namespace=ChannelNamespace.OBSERVATION,
            config=config,
            value_kind=samples[0].payload.kind if samples else None,
        )
        for channel, samples in observation_samples.items()
    }
    action_policies = {
        channel: resolve_channel_policy(
            channel=channel,
            namespace=ChannelNamespace.ACTION,
            config=config,
            value_kind=samples[0].payload.kind if samples else None,
        )
        for channel, samples in action_samples.items()
    }

    steps: list[LearningStep] = []
    for t in timeline.timestamps_us:
        observations = {
            channel: _apply_effective_policy(
                channel=channel,
                samples=samples,
                t=t,
                effective=observation_policies[channel],
            )
            for channel, samples in observation_samples.items()
        }
        actions = {
            channel: _apply_effective_policy(
                channel=channel,
                samples=samples,
                t=t,
                effective=action_policies[channel],
            )
            for channel, samples in action_samples.items()
        }
        steps.append(
            LearningStep(timestamp_us=t, observations=observations, actions=actions)
        )

    return AlignedEpisode(
        episode_id=manifest.episode_id,
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
        task=manifest.task,
        outcome=manifest.outcome,
        metadata=dict(manifest.metadata),
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


def _observation_value(frame: EpisodeObservationFrame) -> AlignedValue:
    if frame.modality is not None:
        # Structural discriminator: EpisodeBuilder sets `modality` only for
        # camera/LiDAR sensor frames, never for state-derived samples
        # (episode_builder.py:131-139 vs. 145-156) — channel names for these
        # are data-dependent (topic-derived), so this cannot be a name check.
        return AlignedValue(
            kind=AlignedValueKind.REFERENCE,
            reference_channel=frame.channel,
            reference_modality=frame.modality,
            reference_uri=frame.uri,
            reference_metadata=dict(frame.metadata),
        )
    if frame.channel == _ORIENTATION_CHANNEL:
        return AlignedValue(
            kind=AlignedValueKind.ORIENTATION, vector=list(frame.values or [])
        )
    if frame.channel in _NUMERIC_VECTOR_STATE_CHANNELS:
        return AlignedValue(
            kind=AlignedValueKind.NUMERIC_VECTOR, vector=list(frame.values or [])
        )
    if frame.channel in _NUMERIC_SCALAR_STATE_CHANNELS:
        values = frame.values or []
        return AlignedValue(
            kind=AlignedValueKind.NUMERIC_SCALAR, scalar=values[0] if values else None
        )
    raise UnknownChannelError(
        f"cannot classify observation channel {frame.channel!r} into a known "
        "AlignedValue kind (no modality, and not a known state.* channel)"
    )


def _action_value(frame: EpisodeActionFrame) -> AlignedValue:
    return AlignedValue(kind=AlignedValueKind.NUMERIC_SCALAR, scalar=frame.value)
