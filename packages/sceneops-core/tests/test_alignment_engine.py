"""align_episode(): canonical asynchronous Episode streams -> AlignedEpisode
(derived, L3; ADR-007 §31.8). Pure, in-memory: proves the engine's wiring,
determinism and immutability (test_alignment_policies.py covers each policy
in isolation)."""

from __future__ import annotations

import copy

import pytest

from sceneops_core.episodes.alignment import (
    ALIGNMENT_SEMANTICS_VERSION,
    AlignedSignalStatus,
    AlignedValueKind,
    AssociationPolicy,
    ChannelPolicyConfig,
    ClockMismatchError,
    InvalidEpisodeBoundsError,
    TemporalAlignmentConfig,
    TemporalSourceContext,
    align_episode,
    field_channel,
)
from sceneops_core.episodes.testing import (
    DEFAULT_CLOCK,
    action,
    episode_manifest,
    event,
    observation,
    state,
)

_CTX = TemporalSourceContext(source_clock=DEFAULT_CLOCK)
_CONFIG = TemporalAlignmentConfig(target_frequency_hz=1.0, tolerance_us=500_000)
US = 1_000  # ns per us
S = 1_000_000_000  # ns per s
POS = field_channel("/odom", "x")
STEER = field_channel("/control", "steering")


def _align(occurrences, config=_CONFIG, ctx=_CTX, **kwargs):
    return align_episode(
        episode_manifest(occurrences, **kwargs), config, ctx, episode_id="ep-1"
    )


def test_synchronized_streams_resolve_at_every_step() -> None:
    aligned = _align(
        [
            state("/odom", 0, x=0.0),
            state("/odom", S, x=1.0),
            action("/control", 0, steering=0.0),
            action("/control", S, steering=0.1),
        ]
    )
    assert aligned.step_count == 2
    assert aligned.episode_id == "ep-1"
    for step in aligned.steps:
        assert step.observations[POS].status == AlignedSignalStatus.RESOLVED
        assert step.actions[STEER].status == AlignedSignalStatus.RESOLVED


def test_actions_faster_than_observations_hold_last_by_default() -> None:
    aligned = _align(
        [state("/odom", 0, x=0.0)]
        + [action("/control", i * S // 2, steering=i / 10) for i in range(5)],
        config=TemporalAlignmentConfig(target_frequency_hz=2.0, tolerance_us=1_000_000),
    )
    assert [s.actions[STEER].value.scalar for s in aligned.steps] == [
        0.0,
        0.1,
        0.2,
        0.3,
        0.4,
    ]
    assert aligned.steps[0].actions[STEER].policy == AssociationPolicy.PREVIOUS


def test_state_and_observation_channels_default_to_nearest() -> None:
    aligned = _align([state("/odom", 0, x=0.0), observation("/cam", 0)])
    assert aligned.steps[0].observations[POS].policy == AssociationPolicy.NEAREST
    assert aligned.steps[0].observations["/cam"].policy == AssociationPolicy.NEAREST


def test_gap_is_missing_not_fabricated() -> None:
    aligned = _align(
        [state("/odom", 0, x=0.0), state("/odom", 4 * S, x=4.0)],
    )
    statuses = [s.observations[POS].status for s in aligned.steps]
    assert statuses[1:4] == [AlignedSignalStatus.MISSING] * 3
    assert all(
        s.observations[POS].value is None
        for s in aligned.steps
        if s.observations[POS].status == AlignedSignalStatus.MISSING
    )


def test_before_first_action_is_missing_never_backfilled() -> None:
    aligned = _align(
        [state("/odom", 0, x=0.0), action("/control", 2 * S, steering=1.0)]
    )
    assert aligned.steps[0].actions[STEER].status == AlignedSignalStatus.MISSING


def test_duplicates_after_us_quantization_are_counted_first_wins() -> None:
    aligned = _align(
        [
            state("/odom", 0, x=0.0),
            state("/odom", 400, x=9.0),  # same microsecond as the first
            state("/odom", S, x=1.0),
        ]
    )
    assert aligned.duplicate_discarded_count == 1
    assert aligned.steps[0].observations[POS].value.scalar == 0.0


def test_recording_order_does_not_affect_the_result() -> None:
    occurrences = [
        state("/odom", 0, x=0.0),
        action("/control", S // 2, steering=0.5),
        state("/odom", S, x=1.0),
        action("/control", 0, steering=0.0),
    ]
    a = _align(occurrences)
    b = _align(list(reversed(occurrences)))
    assert a == b


def test_nearest_beyond_tolerance_is_missing() -> None:
    aligned = _align(
        [state("/odom", 0, x=0.0), state("/odom", 3 * S, x=3.0)],
        config=TemporalAlignmentConfig(target_frequency_hz=1.0, tolerance_us=100_000),
    )
    assert aligned.steps[1].observations[POS].status == AlignedSignalStatus.MISSING


def test_interpolation_is_an_explicit_derived_choice() -> None:
    config = TemporalAlignmentConfig(
        target_frequency_hz=1.0,
        tolerance_us=1,
        channel_policies={
            POS: ChannelPolicyConfig(
                policy=AssociationPolicy.LINEAR_INTERPOLATION, max_gap_us=3_000_000
            )
        },
    )
    aligned = _align(
        [state("/odom", 0, x=0.0), state("/odom", 2 * S, x=2.0)], config=config
    )
    middle = aligned.steps[1].observations[POS]
    assert middle.status == AlignedSignalStatus.INTERPOLATED
    assert middle.value.scalar == pytest.approx(1.0)


def test_vector_fields_align_as_vectors_and_payloads_as_references() -> None:
    aligned = _align([state("/odom", 0, p=[1.0, 2.0, 3.0]), observation("/cam", 0)])
    step = aligned.steps[0]
    assert (
        step.observations[field_channel("/odom", "p")].value.kind
        == AlignedValueKind.NUMERIC_VECTOR
    )
    ref = step.observations["/cam"].value
    assert ref.kind == AlignedValueKind.REFERENCE
    assert ref.reference_metadata["artifact_id"].startswith("payload-")
    assert ref.reference_metadata["media_type"] == "image/jpeg"


def test_non_numeric_fields_and_event_streams_are_not_aligned() -> None:
    aligned = _align(
        [
            state("/odom", 0, x=0.0, mode="drive", moving=True),
            event("/mission", 0, state="running"),
        ]
    )
    channels = set(aligned.steps[0].observations) | set(aligned.steps[0].actions)
    assert channels == {POS}


def test_a_stream_on_another_clock_is_rejected_not_converted() -> None:
    with pytest.raises(ClockMismatchError):
        _align(
            [
                state("/odom", 0, x=0.0),
                action("/control", 5, clock="other.clock", u=1.0),
            ]
        )


def test_no_alignable_occurrence_has_no_bounds() -> None:
    with pytest.raises(InvalidEpisodeBoundsError):
        _align([event("/mission", 0, state="running")])


def test_semantics_version_and_clock_are_recorded() -> None:
    aligned = _align([state("/odom", 0, x=0.0)])
    assert aligned.alignment_semantics_version == ALIGNMENT_SEMANTICS_VERSION == "v2"
    assert aligned.source_clock == DEFAULT_CLOCK


def test_deterministic_and_inputs_not_mutated() -> None:
    manifest = episode_manifest(
        [state("/odom", 0, x=0.0), action("/control", S, steering=0.1)]
    )
    before = copy.deepcopy(manifest)
    config_before = _CONFIG.model_copy(deep=True)
    a = align_episode(manifest, _CONFIG, _CTX, episode_id="ep-1")
    b = align_episode(manifest, _CONFIG, _CTX, episode_id="ep-1")
    assert a == b
    assert manifest == before and _CONFIG == config_before


def test_128hz_uses_round_half_up_quantization() -> None:
    aligned = _align(
        [state("/odom", 0, x=0.0), state("/odom", S, x=1.0)],
        config=TemporalAlignmentConfig(target_frequency_hz=128.0, tolerance_us=10_000),
    )
    assert aligned.dt_us == 7813


def test_timeline_spans_the_episode_window_on_its_clock() -> None:
    aligned = _align([state("/odom", 2 * S, x=2.0)], window=(0, 4 * S + 1))
    assert [s.timestamp_us for s in aligned.steps] == [
        0,
        1_000_000,
        2_000_000,
        3_000_000,
        4_000_000,
    ]


def test_timeline_on_another_clock_spans_the_aligned_samples() -> None:
    aligned = _align(
        [
            state("/odom", 2 * S, x=2.0, clock="robot.clock"),
            state("/odom", 3 * S, x=3.0, clock="robot.clock"),
        ],
        ctx=TemporalSourceContext(source_clock="robot.clock"),
        window=(0, 10 * S),
        window_clock="mcap_log_time",
        extra_streams=(),
    )
    assert [s.timestamp_us for s in aligned.steps] == [2_000_000, 3_000_000]
