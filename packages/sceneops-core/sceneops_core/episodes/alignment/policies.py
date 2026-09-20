from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence, TypeVar

from .config import ChannelPolicyConfig, TemporalAlignmentConfig
from .enums import (
    AlignedSignalStatus,
    AlignedValueKind,
    AssociationPolicy,
    ChannelNamespace,
)
from .errors import (
    InterpolationShapeError,
    InvalidAlignmentConfigError,
    UnknownChannelError,
)
from .schemas import AlignedSignal, AlignedValue

# Known observation channels emitted by EpisodeBuilder (see
# _OBSERVATION_STATE_FIELDS, episode_builder.py:21-26). Any observation
# channel outside this set is a reference/binary channel iff its samples
# carry a SensorModality — never assumed from the channel name alone
# (SceneOps V2 Request 2.1B §9, Request 2.2 §20).
_ORIENTATION_CHANNEL = "state.orientation"
_NUMERIC_VECTOR_STATE_CHANNELS = frozenset(
    {"state.position", "state.velocity", "state.acceleration"}
)
_NUMERIC_SCALAR_STATE_CHANNELS = frozenset({"state.battery"})

_INTERPOLATION_ELIGIBLE_KINDS = frozenset(
    {AlignedValueKind.NUMERIC_SCALAR, AlignedValueKind.NUMERIC_VECTOR}
)


@dataclass(frozen=True)
class ChannelSample:
    """One channel's value at one source timestamp, already converted to
    the typed AlignedValue shape — internal plumbing only, never
    serialized. Association policies operate uniformly on this regardless
    of whether the source was an EpisodeObservationFrame or
    EpisodeActionFrame."""

    timestamp_us: int
    payload: AlignedValue


def canonicalize_samples(
    samples: Sequence[ChannelSample],
) -> tuple[list[ChannelSample], int]:
    """Stable-sort by timestamp_us; collapse exact-timestamp duplicates
    within one channel, keeping the first occurrence in canonical
    (stable-sorted) order (SceneOps V2 Request 2.1B §13/§14, Request 2.2
    §22/§23). Never requires the caller to pre-sort.

    Returns (canonical_samples, duplicate_discarded_count).
    """
    ordered = sorted(samples, key=lambda s: s.timestamp_us)
    canonical: list[ChannelSample] = []
    seen: set[int] = set()
    discarded = 0
    for sample in ordered:
        if sample.timestamp_us in seen:
            discarded += 1
            continue
        seen.add(sample.timestamp_us)
        canonical.append(sample)
    return canonical, discarded


class _ChannelBearingFrame(Protocol):
    channel: str


_FrameT = TypeVar("_FrameT", bound=_ChannelBearingFrame)


def group_frames_by_channel(frames: Sequence[_FrameT]) -> dict[str, list[_FrameT]]:
    """Groups raw EpisodeObservationFrame/EpisodeActionFrame entries by
    ``channel`` — input ordering is preserved per group; canonicalization
    (stable-sort + dedup) happens separately, per group, once each frame is
    converted to a ChannelSample (SceneOps V2 Request 2.2 §10/§22)."""
    groups: dict[str, list[_FrameT]] = {}
    for frame in frames:
        groups.setdefault(frame.channel, []).append(frame)
    return groups


@dataclass(frozen=True)
class EffectiveChannelPolicy:
    channel: str
    policy: AssociationPolicy
    tolerance_us: int | None
    max_gap_us: int | None


def _default_policy(
    *,
    channel: str,
    namespace: ChannelNamespace,
    value_kind: AlignedValueKind | None,
) -> AssociationPolicy:
    if namespace == ChannelNamespace.ACTION:
        # All current action channels (steering/throttle/brake) are control
        # commands in effect until superseded (SceneOps V2 Request 2.1B §9).
        return AssociationPolicy.PREVIOUS

    if channel in _NUMERIC_SCALAR_STATE_CHANNELS:
        return AssociationPolicy.PREVIOUS
    if channel in _NUMERIC_VECTOR_STATE_CHANNELS or channel == _ORIENTATION_CHANNEL:
        return AssociationPolicy.NEAREST
    if value_kind == AlignedValueKind.REFERENCE:
        # Camera/LiDAR channel names are data-dependent (topic-derived), so
        # this is identified structurally via value_kind, not a name
        # allowlist — the only other category besides the known state.*
        # channels that current producers emit.
        return AssociationPolicy.NEAREST

    raise UnknownChannelError(
        f"no known default association policy for observation channel "
        f"{channel!r} (value_kind={value_kind!r}) — provide an explicit "
        "channel_policies override"
    )


def resolve_channel_policy(
    *,
    channel: str,
    namespace: ChannelNamespace,
    config: TemporalAlignmentConfig,
    value_kind: AlignedValueKind | None,
) -> EffectiveChannelPolicy:
    """Explicit override -> known semantic default -> unknown-channel error
    (SceneOps V2 Request 2.2 §20). Resolves tolerance_us/max_gap_us the same
    way: per-channel override, else global config default. This is the
    authoritative validation point for "does this policy have what it
    needs" — TemporalAlignmentConfig's own model_validator only catches the
    subset of this that's visible without a manifest (explicit overrides)."""
    override: ChannelPolicyConfig | None = config.channel_policies.get(channel)
    policy = (override.policy if override and override.policy else None) or (
        _default_policy(channel=channel, namespace=namespace, value_kind=value_kind)
    )

    if policy == AssociationPolicy.LINEAR_INTERPOLATION and (
        value_kind is not None and value_kind not in _INTERPOLATION_ELIGIBLE_KINDS
    ):
        raise InvalidAlignmentConfigError(
            f"channel {channel!r} cannot use linear_interpolation — "
            f"{value_kind!r} values are not interpolable in v1"
        )

    tolerance_us = (
        override.tolerance_us
        if override and override.tolerance_us is not None
        else None
    )
    if tolerance_us is None:
        tolerance_us = config.tolerance_us

    max_gap_us = (
        override.max_gap_us if override and override.max_gap_us is not None else None
    )
    if max_gap_us is None:
        max_gap_us = config.max_gap_us

    if policy == AssociationPolicy.NEAREST and tolerance_us is None:
        raise InvalidAlignmentConfigError(
            f"channel {channel!r} resolves to 'nearest' but no tolerance_us "
            "is configured (override or global)"
        )
    if policy == AssociationPolicy.LINEAR_INTERPOLATION and max_gap_us is None:
        raise InvalidAlignmentConfigError(
            f"channel {channel!r} resolves to 'linear_interpolation' but no "
            "max_gap_us is configured (override or global)"
        )

    return EffectiveChannelPolicy(
        channel=channel, policy=policy, tolerance_us=tolerance_us, max_gap_us=max_gap_us
    )


# ── Association policies ────────────────────────────────────────────────────
#
# Each function returns a fully-formed AlignedSignal for one channel at one
# aligned timestamp `t`. `samples` is always the channel's canonicalized
# (stable-sorted, deduplicated) sample list — callers never need to sort.


def apply_exact(
    *, channel: str, samples: Sequence[ChannelSample], t: int
) -> AlignedSignal:
    for s in samples:
        if s.timestamp_us == t:
            return AlignedSignal(
                channel=channel,
                policy=AssociationPolicy.EXACT,
                status=AlignedSignalStatus.RESOLVED,
                value=s.payload,
                source_timestamp_us=t,
                time_delta_us=0,
            )
    return AlignedSignal(
        channel=channel,
        policy=AssociationPolicy.EXACT,
        status=AlignedSignalStatus.MISSING,
    )


def apply_nearest(
    *, channel: str, samples: Sequence[ChannelSample], t: int, tolerance_us: int | None
) -> AlignedSignal:
    if tolerance_us is None:
        raise InvalidAlignmentConfigError(
            f"channel {channel!r}: nearest requires a finite tolerance_us"
        )

    best: ChannelSample | None = None
    best_abs_delta: int | None = None
    for s in samples:
        delta = t - s.timestamp_us  # aligned - source: see AlignedSignal docstring
        abs_delta = abs(delta)
        if abs_delta > tolerance_us:
            continue
        if best_abs_delta is None or abs_delta < best_abs_delta:
            best, best_abs_delta = s, abs_delta
        elif abs_delta == best_abs_delta and best is not None:
            # Frozen tie rule: earlier timestamp wins (SceneOps V2 Request
            # 2.2 §13). `samples` is ascending, so the earlier candidate is
            # always encountered first and already holds the title — this
            # branch is a defensive, explicit restatement of that guarantee.
            if s.timestamp_us < best.timestamp_us:
                best = s

    if best is None:
        return AlignedSignal(
            channel=channel,
            policy=AssociationPolicy.NEAREST,
            status=AlignedSignalStatus.MISSING,
        )

    delta = t - best.timestamp_us
    return AlignedSignal(
        channel=channel,
        policy=AssociationPolicy.NEAREST,
        status=AlignedSignalStatus.RESOLVED,
        value=best.payload,
        source_timestamp_us=best.timestamp_us,
        time_delta_us=delta,
    )


def apply_previous(
    *, channel: str, samples: Sequence[ChannelSample], t: int, tolerance_us: int | None
) -> AlignedSignal:
    candidate: ChannelSample | None = None
    for s in samples:  # ascending order
        if s.timestamp_us > t:
            break
        candidate = s  # keep advancing to the latest sample <= t

    if candidate is None:
        return AlignedSignal(
            channel=channel,
            policy=AssociationPolicy.PREVIOUS,
            status=AlignedSignalStatus.MISSING,
        )

    age = t - candidate.timestamp_us  # never negative by construction
    if tolerance_us is not None and age > tolerance_us:
        return AlignedSignal(
            channel=channel,
            policy=AssociationPolicy.PREVIOUS,
            status=AlignedSignalStatus.MISSING,
        )

    return AlignedSignal(
        channel=channel,
        policy=AssociationPolicy.PREVIOUS,
        status=AlignedSignalStatus.RESOLVED,
        value=candidate.payload,
        source_timestamp_us=candidate.timestamp_us,
        time_delta_us=age,
    )


def apply_linear_interpolation(
    *, channel: str, samples: Sequence[ChannelSample], t: int, max_gap_us: int | None
) -> AlignedSignal:
    if max_gap_us is None:
        raise InvalidAlignmentConfigError(
            f"channel {channel!r}: linear_interpolation requires a finite max_gap_us"
        )

    for s in samples:
        if s.timestamp_us == t:
            return AlignedSignal(
                channel=channel,
                policy=AssociationPolicy.LINEAR_INTERPOLATION,
                status=AlignedSignalStatus.RESOLVED,
                value=s.payload,
                source_timestamp_us=t,
                time_delta_us=0,
            )

    before: ChannelSample | None = None
    after: ChannelSample | None = None
    for s in samples:  # ascending order
        if s.timestamp_us < t:
            before = s
        elif s.timestamp_us > t and after is None:
            after = s
            break

    missing = AlignedSignal(
        channel=channel,
        policy=AssociationPolicy.LINEAR_INTERPOLATION,
        status=AlignedSignalStatus.MISSING,
    )
    if before is None or after is None:
        return missing

    gap = after.timestamp_us - before.timestamp_us
    if gap > max_gap_us:
        return missing

    ratio = (t - before.timestamp_us) / gap  # 0 < ratio < 1 (strict bracket)
    interpolated_value = _interpolate_value(before.payload, after.payload, ratio)

    return AlignedSignal(
        channel=channel,
        policy=AssociationPolicy.LINEAR_INTERPOLATION,
        status=AlignedSignalStatus.INTERPOLATED,
        value=interpolated_value,
        source_before_timestamp_us=before.timestamp_us,
        source_after_timestamp_us=after.timestamp_us,
        interpolation_ratio=ratio,
    )


def _interpolate_value(
    before: AlignedValue, after: AlignedValue, ratio: float
) -> AlignedValue:
    if before.kind != after.kind:
        raise InterpolationShapeError(
            f"cannot interpolate mismatched value kinds {before.kind!r} and "
            f"{after.kind!r}"
        )
    if before.kind == AlignedValueKind.NUMERIC_SCALAR:
        b, a = before.scalar, after.scalar
        if b is None or a is None:
            raise InterpolationShapeError(
                "cannot interpolate a numeric_scalar value with no scalar payload"
            )
        return AlignedValue(
            kind=AlignedValueKind.NUMERIC_SCALAR, scalar=b + (a - b) * ratio
        )
    if before.kind == AlignedValueKind.NUMERIC_VECTOR:
        bv, av = before.vector or [], after.vector or []
        if len(bv) != len(av):
            raise InterpolationShapeError(
                f"vector dimension mismatch: before has {len(bv)}, after has {len(av)}"
            )
        return AlignedValue(
            kind=AlignedValueKind.NUMERIC_VECTOR,
            vector=[b + (a - b) * ratio for b, a in zip(bv, av)],
        )
    raise InterpolationShapeError(f"value kind {before.kind!r} is not interpolable")
