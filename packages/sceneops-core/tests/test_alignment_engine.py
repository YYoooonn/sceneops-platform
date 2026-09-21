"""End-to-end tests for align_episode() against hand-authored, in-memory
EpisodeManifest fixtures (SceneOps V2 Request 2.2 §37-49). No MCAP, no
EpisodeBuilder, no I/O -- proves the pure engine's wiring, determinism, and
immutability, not just each policy in isolation (see test_alignment_policies.py
for that)."""

from __future__ import annotations

import copy

import pytest

from sceneops_core.episodes.alignment import (
    MCAP_LOG_TIME_CLOCK,
    ALIGNMENT_SEMANTICS_VERSION,
    AlignedSignalStatus,
    AssociationPolicy,
    ChannelPolicyConfig,
    TemporalAlignmentConfig,
    TemporalSourceContext,
    align_episode,
)
from sceneops_core.episodes.schemas import (
    EpisodeActionFrame,
    EpisodeManifest,
    EpisodeObservationFrame,
)
from sceneops_core.sensors import SensorModality

_CTX = TemporalSourceContext(source_clock=MCAP_LOG_TIME_CLOCK)


def _manifest(
    *,
    episode_id: str = "ep-1",
    observation_frames: list[EpisodeObservationFrame] | None = None,
    action_frames: list[EpisodeActionFrame] | None = None,
    start_timestamp_us: int | None,
    end_timestamp_us: int | None,
) -> EpisodeManifest:
    observation_frames = observation_frames or []
    action_frames = action_frames or []
    return EpisodeManifest(
        episode_id=episode_id,
        observation_frames=observation_frames,
        action_frames=action_frames,
        observation_channels=sorted({f.channel for f in observation_frames}),
        action_channels=sorted({f.channel for f in action_frames}),
        start_timestamp_us=start_timestamp_us,
        end_timestamp_us=end_timestamp_us,
        frame_count=len(observation_frames) + len(action_frames),
        task="park",
    )


def _state(t: int, channel: str, values: list[float]) -> EpisodeObservationFrame:
    return EpisodeObservationFrame(timestamp_us=t, channel=channel, values=values)


def _cam(
    t: int, channel: str = "CAM_FRONT", uri: str | None = "s3://x/frame.png"
) -> EpisodeObservationFrame:
    return EpisodeObservationFrame(
        timestamp_us=t, channel=channel, modality=SensorModality.CAMERA, uri=uri
    )


def _action(t: int, channel: str, value: float) -> EpisodeActionFrame:
    return EpisodeActionFrame(timestamp_us=t, channel=channel, value=value)


_DEFAULT_CONFIG = TemporalAlignmentConfig(target_frequency_hz=1.0, tolerance_us=500_000)


class TestFixture01PerfectlySynchronized:
    def test_every_channel_resolved_at_every_step(self) -> None:
        manifest = _manifest(
            observation_frames=[
                _state(0, "state.position", [0.0, 0.0, 0.0]),
                _state(1_000_000, "state.position", [1.0, 0.0, 0.0]),
            ],
            action_frames=[
                _action(0, "steering", 0.0),
                _action(1_000_000, "steering", 0.1),
            ],
            start_timestamp_us=0,
            end_timestamp_us=1_000_000,
        )
        aligned = align_episode(manifest, _DEFAULT_CONFIG, _CTX)
        assert aligned.step_count == 2
        for step in aligned.steps:
            assert (
                step.observations["state.position"].status
                == AlignedSignalStatus.RESOLVED
            )
            assert step.actions["steering"].status == AlignedSignalStatus.RESOLVED


class TestFixture02ObservationsSlowerThanActions:
    def test_action_updates_more_often_than_observation(self) -> None:
        manifest = _manifest(
            observation_frames=[_state(0, "state.position", [0.0, 0.0, 0.0])],
            action_frames=[
                _action(0, "steering", 0.0),
                _action(500_000, "steering", 0.1),
                _action(1_000_000, "steering", 0.2),
                _action(1_500_000, "steering", 0.3),
                _action(2_000_000, "steering", 0.4),
            ],
            start_timestamp_us=0,
            end_timestamp_us=2_000_000,
        )
        config = TemporalAlignmentConfig(
            target_frequency_hz=2.0, tolerance_us=1_000_000
        )
        aligned = align_episode(manifest, config, _CTX)
        steering_values = [s.actions["steering"].value.scalar for s in aligned.steps]
        assert steering_values == [0.0, 0.1, 0.2, 0.3, 0.4]


class TestFixture03ActionsSlowerThanState:
    def test_state_dense_action_sparse_mirrors_real_topic_cadence_mismatch(
        self,
    ) -> None:
        # Mirrors /vehicle/odom (dense) vs /vehicle/control (sparse) -- the
        # real cadence mismatch documented in Request 2.1B §2 finding #2.
        manifest = _manifest(
            observation_frames=[
                _state(0, "state.position", [0.0, 0.0, 0.0]),
                _state(250_000, "state.position", [0.25, 0.0, 0.0]),
                _state(500_000, "state.position", [0.5, 0.0, 0.0]),
                _state(750_000, "state.position", [0.75, 0.0, 0.0]),
                _state(1_000_000, "state.position", [1.0, 0.0, 0.0]),
            ],
            action_frames=[_action(0, "steering", 0.0)],
            start_timestamp_us=0,
            end_timestamp_us=1_000_000,
        )
        # No global tolerance_us: steering (previous, no override) falls
        # through to unlimited hold-last; state.position gets its own
        # explicit nearest tolerance via override.
        config = TemporalAlignmentConfig(
            target_frequency_hz=4.0,
            channel_policies={
                "state.position": ChannelPolicyConfig(
                    policy=AssociationPolicy.NEAREST, tolerance_us=200_000
                ),
            },
        )
        aligned = align_episode(manifest, config, _CTX)
        assert all(
            s.actions["steering"].status == AlignedSignalStatus.RESOLVED
            for s in aligned.steps
        )
        assert all(
            s.observations["state.position"].status == AlignedSignalStatus.RESOLVED
            for s in aligned.steps
        )


class TestFixture04IrregularJitter:
    def test_irregular_source_timestamps_still_align_deterministically(self) -> None:
        manifest = _manifest(
            observation_frames=[
                _state(0, "state.battery", [90.0]),
                _state(497_211, "state.battery", [89.0]),
                _state(1_000_003, "state.battery", [88.0]),
            ],
            start_timestamp_us=0,
            end_timestamp_us=1_000_000,
        )
        config = TemporalAlignmentConfig(
            target_frequency_hz=2.0
        )  # previous, unlimited hold-last
        aligned = align_episode(manifest, config, _CTX)
        assert aligned.step_count == 3
        assert all(
            s.observations["state.battery"].status == AlignedSignalStatus.RESOLVED
            for s in aligned.steps
        )


class TestFixture05MissingMiddleObservation:
    def test_gap_inside_episode_marks_missing_not_fabricated(self) -> None:
        manifest = _manifest(
            observation_frames=[
                _state(0, "state.velocity", [1.0]),
                # gap: nothing near t=1_000_000
                _state(2_000_000, "state.velocity", [3.0]),
            ],
            start_timestamp_us=0,
            end_timestamp_us=2_000_000,
        )
        config = TemporalAlignmentConfig(target_frequency_hz=1.0, tolerance_us=100_000)
        aligned = align_episode(manifest, config, _CTX)
        statuses = [s.observations["state.velocity"].status for s in aligned.steps]
        assert statuses == [
            AlignedSignalStatus.RESOLVED,
            AlignedSignalStatus.MISSING,
            AlignedSignalStatus.RESOLVED,
        ]


class TestFixture06LeadingMissingAction:
    def test_before_first_action_is_missing_never_backfilled(self) -> None:
        manifest = _manifest(
            action_frames=[_action(2_000_000, "steering", 0.5)],
            start_timestamp_us=0,
            end_timestamp_us=2_000_000,
        )
        config = TemporalAlignmentConfig(target_frequency_hz=1.0)
        aligned = align_episode(manifest, config, _CTX)
        assert (
            aligned.steps[0].actions["steering"].status == AlignedSignalStatus.MISSING
        )
        assert (
            aligned.steps[1].actions["steering"].status == AlignedSignalStatus.MISSING
        )
        assert (
            aligned.steps[2].actions["steering"].status == AlignedSignalStatus.RESOLVED
        )


class TestFixture07TrailingGap:
    def test_after_last_observation_stays_missing_by_default(self) -> None:
        manifest = _manifest(
            observation_frames=[_state(0, "state.velocity", [1.0])],
            start_timestamp_us=0,
            end_timestamp_us=2_000_000,
        )
        config = TemporalAlignmentConfig(target_frequency_hz=1.0, tolerance_us=100_000)
        aligned = align_episode(manifest, config, _CTX)
        assert (
            aligned.steps[0].observations["state.velocity"].status
            == AlignedSignalStatus.RESOLVED
        )
        assert (
            aligned.steps[1].observations["state.velocity"].status
            == AlignedSignalStatus.MISSING
        )
        assert (
            aligned.steps[2].observations["state.velocity"].status
            == AlignedSignalStatus.MISSING
        )

    def test_trailing_hold_last_action_remains_valid_within_tolerance(self) -> None:
        manifest = _manifest(
            action_frames=[_action(0, "steering", 0.5)],
            start_timestamp_us=0,
            end_timestamp_us=2_000_000,
        )
        config = TemporalAlignmentConfig(
            target_frequency_hz=1.0
        )  # tolerance_us=None -> unlimited
        aligned = align_episode(manifest, config, _CTX)
        assert all(
            s.actions["steering"].status == AlignedSignalStatus.RESOLVED
            for s in aligned.steps
        )


class TestFixture08SameChannelDuplicateTimestamp:
    def test_duplicate_discarded_count_and_first_wins_end_to_end(self) -> None:
        manifest = _manifest(
            observation_frames=[
                _state(0, "state.battery", [50.0]),  # first -- wins
                _state(0, "state.battery", [99.0]),  # duplicate -- discarded
            ],
            start_timestamp_us=0,
            end_timestamp_us=0,
        )
        config = TemporalAlignmentConfig(target_frequency_hz=1.0)
        aligned = align_episode(manifest, config, _CTX)
        assert aligned.duplicate_discarded_count == 1
        assert aligned.steps[0].observations["state.battery"].value.scalar == 50.0


class TestFixture09OutOfOrderNonDuplicateInput:
    def test_input_order_does_not_affect_result(self) -> None:
        forward = _manifest(
            observation_frames=[
                _state(0, "state.battery", [90.0]),
                _state(500_000, "state.battery", [80.0]),
                _state(1_000_000, "state.battery", [70.0]),
            ],
            start_timestamp_us=0,
            end_timestamp_us=1_000_000,
        )
        reversed_ = _manifest(
            observation_frames=[
                _state(1_000_000, "state.battery", [70.0]),
                _state(0, "state.battery", [90.0]),
                _state(500_000, "state.battery", [80.0]),
            ],
            start_timestamp_us=0,
            end_timestamp_us=1_000_000,
        )
        config = TemporalAlignmentConfig(target_frequency_hz=2.0)
        aligned_forward = align_episode(forward, config, _CTX)
        aligned_reversed = align_episode(reversed_, config, _CTX)
        assert aligned_forward.model_dump() == aligned_reversed.model_dump()


class TestFixture10AssociationGapBeyondTolerance:
    def test_nearest_beyond_tolerance_is_missing(self) -> None:
        manifest = _manifest(
            observation_frames=[_state(0, "state.velocity", [1.0])],
            start_timestamp_us=0,
            end_timestamp_us=1_000_000,
        )
        config = TemporalAlignmentConfig(target_frequency_hz=1.0, tolerance_us=100)
        aligned = align_episode(manifest, config, _CTX)
        assert (
            aligned.steps[1].observations["state.velocity"].status
            == AlignedSignalStatus.MISSING
        )


class TestFixture11InterpolationGapBeyondMaxGap:
    def test_interpolation_override_beyond_max_gap_is_missing(self) -> None:
        manifest = _manifest(
            observation_frames=[
                _state(0, "state.velocity", [0.0]),
                _state(10_000_000, "state.velocity", [10.0]),
            ],
            start_timestamp_us=0,
            end_timestamp_us=10_000_000,
        )
        config = TemporalAlignmentConfig(
            target_frequency_hz=1.0,
            channel_policies={
                "state.velocity": ChannelPolicyConfig(
                    policy=AssociationPolicy.LINEAR_INTERPOLATION, max_gap_us=1_000_000
                )
            },
        )
        aligned = align_episode(manifest, config, _CTX)
        assert (
            aligned.steps[5].observations["state.velocity"].status
            == AlignedSignalStatus.MISSING
        )


class TestFixture12MultiChannelDifferentRates:
    def test_camera_lidar_and_two_state_channels_at_different_rates(self) -> None:
        manifest = _manifest(
            observation_frames=[
                _cam(0, "CAM_FRONT"),
                _cam(900_000, "CAM_FRONT"),
                _cam(0, "LIDAR_TOP"),
                _state(0, "state.position", [0.0, 0.0, 0.0]),
                _state(500_000, "state.position", [0.5, 0.0, 0.0]),
                _state(1_000_000, "state.position", [1.0, 0.0, 0.0]),
                _state(0, "state.battery", [90.0]),
            ],
            start_timestamp_us=0,
            end_timestamp_us=1_000_000,
        )
        config = TemporalAlignmentConfig(
            target_frequency_hz=2.0, tolerance_us=1_000_000
        )
        aligned = align_episode(manifest, config, _CTX)
        assert set(aligned.steps[0].observations) == {
            "CAM_FRONT",
            "LIDAR_TOP",
            "state.position",
            "state.battery",
        }


class TestFixture13ZeroDurationEpisode:
    def test_single_step_at_start(self) -> None:
        manifest = _manifest(
            observation_frames=[_state(5_000, "state.battery", [50.0])],
            start_timestamp_us=5_000,
            end_timestamp_us=5_000,
        )
        config = TemporalAlignmentConfig(target_frequency_hz=1.0)
        aligned = align_episode(manifest, config, _CTX)
        assert aligned.step_count == 1
        assert aligned.steps[0].timestamp_us == 5_000


class TestFixture14ShorterThanOneDt:
    def test_single_step_at_start_when_duration_below_dt(self) -> None:
        manifest = _manifest(
            observation_frames=[_state(0, "state.battery", [50.0])],
            start_timestamp_us=0,
            end_timestamp_us=100,
        )
        config = TemporalAlignmentConfig(target_frequency_hz=1.0)
        aligned = align_episode(manifest, config, _CTX)
        assert aligned.step_count == 1


class TestFixture15NearestEquidistantTie:
    def test_earlier_wins_through_full_engine(self) -> None:
        manifest = _manifest(
            observation_frames=[
                _state(0, "state.velocity", [1.0]),
                _state(1_000_000, "state.velocity", [2.0]),
            ],
            start_timestamp_us=0,
            end_timestamp_us=1_000_000,
        )
        config = TemporalAlignmentConfig(
            target_frequency_hz=2.0,  # midpoint step at t=500_000 is equidistant
            tolerance_us=1_000_000,
        )
        aligned = align_episode(manifest, config, _CTX)
        mid_step = next(s for s in aligned.steps if s.timestamp_us == 500_000)
        assert mid_step.observations["state.velocity"].source_timestamp_us == 0


class TestFixture16NonIntegerMicrosecondPeriod:
    def test_128hz_uses_round_half_up_quantization(self) -> None:
        manifest = _manifest(
            observation_frames=[_state(0, "state.battery", [50.0])],
            start_timestamp_us=0,
            end_timestamp_us=1_000_000,
        )
        config = TemporalAlignmentConfig(target_frequency_hz=128.0)
        aligned = align_episode(manifest, config, _CTX)
        assert aligned.dt_us == 7_813
        assert aligned.achieved_frequency_hz != 128.0


class TestFixture17BinaryObservationUriNone:
    def test_matched_frame_with_uri_none_is_resolved_not_missing(self) -> None:
        manifest = _manifest(
            observation_frames=[_cam(0, "CAM_FRONT", uri=None)],
            start_timestamp_us=0,
            end_timestamp_us=0,
        )
        config = TemporalAlignmentConfig(target_frequency_hz=1.0, tolerance_us=1_000)
        aligned = align_episode(manifest, config, _CTX)
        signal = aligned.steps[0].observations["CAM_FRONT"]
        assert signal.status == AlignedSignalStatus.RESOLVED
        assert signal.value.reference_uri is None
        assert signal.value.reference_modality == SensorModality.CAMERA

    def test_no_frame_within_tolerance_is_genuinely_missing(self) -> None:
        manifest = _manifest(
            observation_frames=[_cam(0, "CAM_FRONT", uri=None)],
            start_timestamp_us=0,
            end_timestamp_us=1_000_000,
        )
        config = TemporalAlignmentConfig(target_frequency_hz=1.0, tolerance_us=100)
        aligned = align_episode(manifest, config, _CTX)
        assert (
            aligned.steps[1].observations["CAM_FRONT"].status
            == AlignedSignalStatus.MISSING
        )


class TestSemanticsVersion:
    def test_every_output_carries_the_current_version(self) -> None:
        manifest = _manifest(start_timestamp_us=0, end_timestamp_us=0)
        aligned = align_episode(manifest, _DEFAULT_CONFIG, _CTX)
        assert (
            aligned.alignment_semantics_version == ALIGNMENT_SEMANTICS_VERSION == "v1"
        )


class TestSourceClockProvenance:
    def test_source_clock_is_carried_from_context_not_hardcoded(self) -> None:
        manifest = _manifest(start_timestamp_us=0, end_timestamp_us=0)
        custom_ctx = TemporalSourceContext(source_clock="synthetic_test_clock")
        aligned = align_episode(manifest, _DEFAULT_CONFIG, custom_ctx)
        assert aligned.source_clock == "synthetic_test_clock"


class TestDeterminism:
    def test_identical_inputs_produce_identical_output(self) -> None:
        manifest = _manifest(
            observation_frames=[
                _state(0, "state.position", [0.0, 0.0, 0.0]),
                _state(1_000_000, "state.position", [1.0, 0.0, 0.0]),
            ],
            action_frames=[_action(0, "steering", 0.1)],
            start_timestamp_us=0,
            end_timestamp_us=1_000_000,
        )
        run1 = align_episode(manifest, _DEFAULT_CONFIG, _CTX)
        run2 = align_episode(manifest, _DEFAULT_CONFIG, _CTX)
        assert run1.model_dump() == run2.model_dump()


class TestInputImmutability:
    def test_manifest_and_config_are_not_mutated(self) -> None:
        manifest = _manifest(
            observation_frames=[
                _state(1_000_000, "state.position", [1.0, 0.0, 0.0]),
                _state(0, "state.position", [0.0, 0.0, 0.0]),
            ],
            action_frames=[_action(0, "steering", 0.1)],
            start_timestamp_us=0,
            end_timestamp_us=1_000_000,
        )
        config = TemporalAlignmentConfig(target_frequency_hz=1.0, tolerance_us=500_000)
        ctx = TemporalSourceContext(source_clock=MCAP_LOG_TIME_CLOCK)

        manifest_before = copy.deepcopy(manifest.model_dump())
        config_before = copy.deepcopy(config.model_dump())
        ctx_before = copy.deepcopy(ctx.model_dump())

        align_episode(manifest, config, ctx)

        assert manifest.model_dump() == manifest_before
        assert config.model_dump() == config_before
        assert ctx.model_dump() == ctx_before
        # The raw frame list's own order must survive untouched too --
        # canonicalization must operate on copies, not sort in place.
        assert manifest.observation_frames[0].timestamp_us == 1_000_000


class TestMissingnessNeverFabricates:
    def test_missing_status_never_carries_a_fabricated_value(self) -> None:
        manifest = _manifest(
            observation_frames=[_state(0, "state.velocity", [1.0])],
            start_timestamp_us=0,
            end_timestamp_us=1_000_000,
        )
        config = TemporalAlignmentConfig(target_frequency_hz=1.0, tolerance_us=1)
        aligned = align_episode(manifest, config, _CTX)
        missing_signal = aligned.steps[1].observations["state.velocity"]
        assert missing_signal.status == AlignedSignalStatus.MISSING
        assert missing_signal.value is None


class TestEpisodeLevelMetadataPassthrough:
    def test_task_and_outcome_carried_from_manifest(self) -> None:
        manifest = _manifest(start_timestamp_us=0, end_timestamp_us=0)
        aligned = align_episode(manifest, _DEFAULT_CONFIG, _CTX)
        assert aligned.task == "park"


class TestBoundsRejection:
    def test_missing_manifest_bounds_fail_alignment_not_infer(self) -> None:
        from sceneops_core.episodes.alignment import InvalidEpisodeBoundsError

        manifest = _manifest(start_timestamp_us=None, end_timestamp_us=None)
        with pytest.raises(InvalidEpisodeBoundsError):
            align_episode(manifest, _DEFAULT_CONFIG, _CTX)
