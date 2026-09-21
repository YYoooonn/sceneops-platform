"""Tests for individual association policies, duplicate canonicalization,
and channel-policy resolution (SceneOps V2 Request 2.2 §11-24, §38-41, §43).

Exercises policies.py directly against hand-built ChannelSample lists --
no EpisodeManifest/engine involved, so these prove each policy's semantics
in isolation from timeline generation (§10's separation requirement)."""

from __future__ import annotations

import pytest

from sceneops_core.episodes.alignment import (
    AlignedSignalStatus,
    AlignedValueKind,
    AssociationPolicy,
    ChannelNamespace,
    ChannelPolicyConfig,
    ChannelSample,
    InterpolationShapeError,
    InvalidAlignmentConfigError,
    TemporalAlignmentConfig,
    UnknownChannelError,
    canonicalize_samples,
    resolve_channel_policy,
)
from sceneops_core.episodes.alignment.policies import (
    apply_exact,
    apply_linear_interpolation,
    apply_nearest,
    apply_previous,
)
from sceneops_core.episodes.alignment.schemas import AlignedValue


def _scalar(value: float) -> AlignedValue:
    return AlignedValue(kind=AlignedValueKind.NUMERIC_SCALAR, scalar=value)


def _sample(t: int, value: float) -> ChannelSample:
    return ChannelSample(timestamp_us=t, payload=_scalar(value))


class TestExactPolicy:
    def test_exact_timestamp_exists(self) -> None:
        samples = [_sample(0, 1.0), _sample(1_000, 2.0)]
        signal = apply_exact(channel="c", samples=samples, t=1_000)
        assert signal.status == AlignedSignalStatus.RESOLVED
        assert signal.value.scalar == 2.0
        assert signal.time_delta_us == 0

    def test_exact_timestamp_absent(self) -> None:
        samples = [_sample(0, 1.0), _sample(1_000, 2.0)]
        signal = apply_exact(channel="c", samples=samples, t=500)
        assert signal.status == AlignedSignalStatus.MISSING
        assert signal.value is None

    def test_exact_ignores_tolerance_widening(self) -> None:
        # apply_exact takes no tolerance parameter at all -- a near-miss at
        # t=999 must never resolve, however close.
        samples = [_sample(1_000, 2.0)]
        signal = apply_exact(channel="c", samples=samples, t=999)
        assert signal.status == AlignedSignalStatus.MISSING


class TestNearestPolicy:
    def test_previous_sample_closer(self) -> None:
        samples = [_sample(0, 1.0), _sample(1_000, 2.0)]
        signal = apply_nearest(channel="c", samples=samples, t=200, tolerance_us=1_000)
        assert signal.status == AlignedSignalStatus.RESOLVED
        assert signal.source_timestamp_us == 0
        assert signal.time_delta_us == 200

    def test_next_sample_closer(self) -> None:
        samples = [_sample(0, 1.0), _sample(1_000, 2.0)]
        signal = apply_nearest(channel="c", samples=samples, t=800, tolerance_us=1_000)
        assert signal.status == AlignedSignalStatus.RESOLVED
        assert signal.source_timestamp_us == 1_000
        assert signal.time_delta_us == -200

    def test_equidistant_samples_earlier_wins(self) -> None:
        samples = [_sample(0, 1.0), _sample(1_000, 2.0)]
        signal = apply_nearest(channel="c", samples=samples, t=500, tolerance_us=1_000)
        assert signal.status == AlignedSignalStatus.RESOLVED
        assert signal.source_timestamp_us == 0  # earlier of the two ties

    def test_delta_equal_tolerance_is_resolved(self) -> None:
        samples = [_sample(0, 1.0)]
        signal = apply_nearest(
            channel="c", samples=samples, t=1_000, tolerance_us=1_000
        )
        assert signal.status == AlignedSignalStatus.RESOLVED

    def test_delta_one_more_than_tolerance_is_missing(self) -> None:
        samples = [_sample(0, 1.0)]
        signal = apply_nearest(
            channel="c", samples=samples, t=1_001, tolerance_us=1_000
        )
        assert signal.status == AlignedSignalStatus.MISSING

    def test_missing_tolerance_raises(self) -> None:
        samples = [_sample(0, 1.0)]
        with pytest.raises(InvalidAlignmentConfigError):
            apply_nearest(channel="c", samples=samples, t=0, tolerance_us=None)


class TestPreviousPolicy:
    def test_before_first_sample_is_missing(self) -> None:
        samples = [_sample(1_000, 1.0)]
        signal = apply_previous(channel="c", samples=samples, t=500, tolerance_us=None)
        assert signal.status == AlignedSignalStatus.MISSING

    def test_normal_hold_last(self) -> None:
        samples = [_sample(0, 1.0), _sample(1_000, 2.0)]
        signal = apply_previous(
            channel="c", samples=samples, t=1_500, tolerance_us=None
        )
        assert signal.status == AlignedSignalStatus.RESOLVED
        assert signal.source_timestamp_us == 1_000
        assert signal.time_delta_us == 500

    def test_age_equal_tolerance_is_resolved(self) -> None:
        samples = [_sample(0, 1.0)]
        signal = apply_previous(
            channel="c", samples=samples, t=1_000, tolerance_us=1_000
        )
        assert signal.status == AlignedSignalStatus.RESOLVED

    def test_age_one_more_than_tolerance_is_missing(self) -> None:
        samples = [_sample(0, 1.0)]
        signal = apply_previous(
            channel="c", samples=samples, t=1_001, tolerance_us=1_000
        )
        assert signal.status == AlignedSignalStatus.MISSING

    def test_tolerance_none_is_unlimited_hold_last(self) -> None:
        samples = [_sample(0, 1.0)]
        signal = apply_previous(
            channel="c", samples=samples, t=10_000_000, tolerance_us=None
        )
        assert signal.status == AlignedSignalStatus.RESOLVED

    def test_never_selects_a_future_sample(self) -> None:
        samples = [_sample(0, 1.0), _sample(2_000, 2.0)]
        signal = apply_previous(
            channel="c", samples=samples, t=1_000, tolerance_us=None
        )
        assert signal.source_timestamp_us == 0
        assert signal.time_delta_us >= 0


class TestLinearInterpolationPolicy:
    def test_scalar_interpolation(self) -> None:
        samples = [_sample(0, 0.0), _sample(1_000, 10.0)]
        signal = apply_linear_interpolation(
            channel="c", samples=samples, t=250, max_gap_us=2_000
        )
        assert signal.status == AlignedSignalStatus.INTERPOLATED
        assert signal.value.scalar == pytest.approx(2.5)
        assert signal.interpolation_ratio == pytest.approx(0.25)
        assert signal.source_before_timestamp_us == 0
        assert signal.source_after_timestamp_us == 1_000

    def test_vector_interpolation(self) -> None:
        before = ChannelSample(
            timestamp_us=0,
            payload=AlignedValue(
                kind=AlignedValueKind.NUMERIC_VECTOR, vector=[0.0, 0.0]
            ),
        )
        after = ChannelSample(
            timestamp_us=1_000,
            payload=AlignedValue(
                kind=AlignedValueKind.NUMERIC_VECTOR, vector=[10.0, 20.0]
            ),
        )
        signal = apply_linear_interpolation(
            channel="c", samples=[before, after], t=500, max_gap_us=2_000
        )
        assert signal.status == AlignedSignalStatus.INTERPOLATED
        assert signal.value.vector == pytest.approx([5.0, 10.0])

    def test_exact_source_timestamp_is_resolved_not_interpolated(self) -> None:
        samples = [_sample(0, 0.0), _sample(1_000, 10.0)]
        signal = apply_linear_interpolation(
            channel="c", samples=samples, t=0, max_gap_us=2_000
        )
        assert signal.status == AlignedSignalStatus.RESOLVED

    def test_bracket_gap_equal_to_max_gap_is_interpolated(self) -> None:
        samples = [_sample(0, 0.0), _sample(1_000, 10.0)]
        signal = apply_linear_interpolation(
            channel="c", samples=samples, t=500, max_gap_us=1_000
        )
        assert signal.status == AlignedSignalStatus.INTERPOLATED

    def test_bracket_gap_over_max_gap_is_missing(self) -> None:
        samples = [_sample(0, 0.0), _sample(1_000, 10.0)]
        signal = apply_linear_interpolation(
            channel="c", samples=samples, t=500, max_gap_us=999
        )
        assert signal.status == AlignedSignalStatus.MISSING

    def test_no_left_sample_is_missing(self) -> None:
        samples = [_sample(1_000, 10.0)]
        signal = apply_linear_interpolation(
            channel="c", samples=samples, t=500, max_gap_us=2_000
        )
        assert signal.status == AlignedSignalStatus.MISSING

    def test_no_right_sample_is_missing(self) -> None:
        samples = [_sample(0, 0.0)]
        signal = apply_linear_interpolation(
            channel="c", samples=samples, t=500, max_gap_us=2_000
        )
        assert signal.status == AlignedSignalStatus.MISSING

    def test_max_gap_none_raises(self) -> None:
        samples = [_sample(0, 0.0), _sample(1_000, 10.0)]
        with pytest.raises(InvalidAlignmentConfigError):
            apply_linear_interpolation(
                channel="c", samples=samples, t=500, max_gap_us=None
            )

    def test_vector_dimension_mismatch_raises_explicit_error(self) -> None:
        before = ChannelSample(
            timestamp_us=0,
            payload=AlignedValue(
                kind=AlignedValueKind.NUMERIC_VECTOR, vector=[0.0, 0.0]
            ),
        )
        after = ChannelSample(
            timestamp_us=1_000,
            payload=AlignedValue(
                kind=AlignedValueKind.NUMERIC_VECTOR, vector=[1.0, 2.0, 3.0]
            ),
        )
        with pytest.raises(InterpolationShapeError):
            apply_linear_interpolation(
                channel="c", samples=[before, after], t=500, max_gap_us=2_000
            )

    def test_never_falls_back_to_nearest_when_gap_exceeded(self) -> None:
        # A near sample exists just outside max_gap -- must stay missing,
        # not silently resolve via a nearest-style fallback.
        samples = [_sample(0, 0.0), _sample(100, 1.0), _sample(1_000, 10.0)]
        signal = apply_linear_interpolation(
            channel="c", samples=samples, t=550, max_gap_us=100
        )
        assert signal.status == AlignedSignalStatus.MISSING
        assert signal.value is None


class TestDuplicateCanonicalization:
    def test_non_duplicate_permutation_yields_identical_result(self) -> None:
        a = [_sample(1_000, 2.0), _sample(0, 1.0), _sample(2_000, 3.0)]
        b = [_sample(2_000, 3.0), _sample(0, 1.0), _sample(1_000, 2.0)]
        canon_a, discarded_a = canonicalize_samples(a)
        canon_b, discarded_b = canonicalize_samples(b)
        assert discarded_a == discarded_b == 0
        assert [(s.timestamp_us, s.payload.scalar) for s in canon_a] == [
            (s.timestamp_us, s.payload.scalar) for s in canon_b
        ]

    def test_duplicate_first_occurrence_wins_a_then_b(self) -> None:
        samples = [_sample(0, 111.0), _sample(0, 222.0)]  # A (111) then B (222)
        canon, discarded = canonicalize_samples(samples)
        assert discarded == 1
        assert len(canon) == 1
        assert canon[0].payload.scalar == 111.0  # A wins

    def test_duplicate_first_occurrence_wins_b_then_a(self) -> None:
        samples = [_sample(0, 222.0), _sample(0, 111.0)]  # B (222) then A (111)
        canon, discarded = canonicalize_samples(samples)
        assert discarded == 1
        assert canon[0].payload.scalar == 222.0  # B wins (it came first)

    def test_duplicate_discarded_count_is_correct_across_multiple_collisions(
        self,
    ) -> None:
        samples = [
            _sample(0, 1.0),
            _sample(0, 2.0),
            _sample(0, 3.0),
            _sample(1_000, 4.0),
        ]
        canon, discarded = canonicalize_samples(samples)
        assert discarded == 2
        assert len(canon) == 2

    def test_different_channels_sharing_a_timestamp_is_not_a_duplicate(self) -> None:
        # canonicalize_samples operates per already-grouped channel, so this
        # is really a statement about group_frames_by_channel's contract:
        # co-timed different channels never enter the same canonicalize_samples
        # call in the first place.
        samples_channel_a = [_sample(0, 1.0)]
        samples_channel_b = [_sample(0, 2.0)]
        canon_a, discarded_a = canonicalize_samples(samples_channel_a)
        canon_b, discarded_b = canonicalize_samples(samples_channel_b)
        assert discarded_a == 0 and discarded_b == 0
        assert len(canon_a) == 1 and len(canon_b) == 1

    def test_out_of_order_non_duplicate_input_is_stable_sorted(self) -> None:
        samples = [_sample(500, 2.0), _sample(0, 1.0), _sample(1_000, 3.0)]
        canon, _ = canonicalize_samples(samples)
        assert [s.timestamp_us for s in canon] == [0, 500, 1_000]


class TestChannelPolicyResolution:
    def test_action_channel_defaults_to_previous(self) -> None:
        config = TemporalAlignmentConfig(target_frequency_hz=10.0)
        effective = resolve_channel_policy(
            channel="steering",
            namespace=ChannelNamespace.ACTION,
            config=config,
            value_kind=AlignedValueKind.NUMERIC_SCALAR,
        )
        assert effective.policy == AssociationPolicy.PREVIOUS

    def test_state_vector_channel_defaults_to_nearest(self) -> None:
        config = TemporalAlignmentConfig(target_frequency_hz=10.0, tolerance_us=1_000)
        effective = resolve_channel_policy(
            channel="state.velocity",
            namespace=ChannelNamespace.OBSERVATION,
            config=config,
            value_kind=AlignedValueKind.NUMERIC_VECTOR,
        )
        assert effective.policy == AssociationPolicy.NEAREST
        assert effective.tolerance_us == 1_000

    def test_orientation_defaults_to_nearest_not_slerp(self) -> None:
        config = TemporalAlignmentConfig(target_frequency_hz=10.0, tolerance_us=1_000)
        effective = resolve_channel_policy(
            channel="state.orientation",
            namespace=ChannelNamespace.OBSERVATION,
            config=config,
            value_kind=AlignedValueKind.ORIENTATION,
        )
        assert effective.policy == AssociationPolicy.NEAREST

    def test_battery_defaults_to_previous(self) -> None:
        config = TemporalAlignmentConfig(target_frequency_hz=10.0)
        effective = resolve_channel_policy(
            channel="state.battery",
            namespace=ChannelNamespace.OBSERVATION,
            config=config,
            value_kind=AlignedValueKind.NUMERIC_SCALAR,
        )
        assert effective.policy == AssociationPolicy.PREVIOUS

    def test_reference_channel_defaults_to_nearest_via_value_kind_not_name(
        self,
    ) -> None:
        config = TemporalAlignmentConfig(target_frequency_hz=10.0, tolerance_us=100_000)
        effective = resolve_channel_policy(
            channel="CAM_FRONT",  # arbitrary, data-dependent channel name
            namespace=ChannelNamespace.OBSERVATION,
            config=config,
            value_kind=AlignedValueKind.REFERENCE,
        )
        assert effective.policy == AssociationPolicy.NEAREST

    def test_unclassifiable_observation_channel_raises_explicit_error(self) -> None:
        config = TemporalAlignmentConfig(target_frequency_hz=10.0)
        with pytest.raises(UnknownChannelError):
            resolve_channel_policy(
                channel="state.mystery_field",
                namespace=ChannelNamespace.OBSERVATION,
                config=config,
                value_kind=None,
            )

    def test_explicit_override_wins_over_default(self) -> None:
        config = TemporalAlignmentConfig(
            target_frequency_hz=10.0,
            tolerance_us=1_000,
            channel_policies={
                "steering": ChannelPolicyConfig(policy=AssociationPolicy.EXACT)
            },
        )
        effective = resolve_channel_policy(
            channel="steering",
            namespace=ChannelNamespace.ACTION,
            config=config,
            value_kind=AlignedValueKind.NUMERIC_SCALAR,
        )
        assert effective.policy == AssociationPolicy.EXACT

    def test_orientation_cannot_be_overridden_to_interpolation(self) -> None:
        config = TemporalAlignmentConfig(
            target_frequency_hz=10.0,
            max_gap_us=100_000,
            channel_policies={
                "state.orientation": ChannelPolicyConfig(
                    policy=AssociationPolicy.LINEAR_INTERPOLATION
                )
            },
        )
        with pytest.raises(InvalidAlignmentConfigError):
            resolve_channel_policy(
                channel="state.orientation",
                namespace=ChannelNamespace.OBSERVATION,
                config=config,
                value_kind=AlignedValueKind.ORIENTATION,
            )

    def test_reference_channel_cannot_be_overridden_to_interpolation(self) -> None:
        config = TemporalAlignmentConfig(
            target_frequency_hz=10.0,
            max_gap_us=100_000,
            channel_policies={
                "CAM_FRONT": ChannelPolicyConfig(
                    policy=AssociationPolicy.LINEAR_INTERPOLATION
                )
            },
        )
        with pytest.raises(InvalidAlignmentConfigError):
            resolve_channel_policy(
                channel="CAM_FRONT",
                namespace=ChannelNamespace.OBSERVATION,
                config=config,
                value_kind=AlignedValueKind.REFERENCE,
            )

    def test_channel_override_tolerance_wins_over_global(self) -> None:
        config = TemporalAlignmentConfig(
            target_frequency_hz=10.0,
            tolerance_us=1_000,
            channel_policies={
                "state.velocity": ChannelPolicyConfig(
                    policy=AssociationPolicy.NEAREST, tolerance_us=50
                )
            },
        )
        effective = resolve_channel_policy(
            channel="state.velocity",
            namespace=ChannelNamespace.OBSERVATION,
            config=config,
            value_kind=AlignedValueKind.NUMERIC_VECTOR,
        )
        assert effective.tolerance_us == 50
