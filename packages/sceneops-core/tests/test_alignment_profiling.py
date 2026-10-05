"""Tests for AlignedEpisodeProfiler (SceneOps V2 Request 2.4 §44-47)."""

from __future__ import annotations

import pytest

from sceneops_core.episodes.alignment import (
    AlignedEpisodeArtifact,
    AlignedEpisodeProfiler,
    ChannelNamespace,
    EpisodeSourceRevision,
    TemporalAlignmentConfig,
    TemporalSourceContext,
    align_episode,
)


from sceneops_core.episodes.alignment import field_channel
from sceneops_core.episodes.testing import (
    DEFAULT_CLOCK,
    action as _action_occ,
    episode_manifest,
    observation as _observation_occ,
    state as _state_occ,
)

# Canonical Episode streams these tests align: one field ``v`` per topic.
BATTERY = field_channel("/state/battery", "v")
POSITION = field_channel("/state/position", "v")
VELOCITY = field_channel("/state/velocity", "v")
STEERING = field_channel("/control/steering", "v")
DUP = field_channel("/dup", "v")
CAM_FRONT = "/camera/front"
_TOPICS = {
    "state.battery": "/state/battery",
    "state.position": "/state/position",
    "state.velocity": "/state/velocity",
    "steering": "/control/steering",
    "dup": "/dup",
    "CAM_FRONT": CAM_FRONT,
}
_SCALAR = {"state.battery"}


def _obs(*, timestamp_us, channel, values=None, modality=None, **_):
    if modality is not None:
        return _observation_occ(_TOPICS[channel], timestamp_us * 1_000)
    values = list(values or [])
    v = values[0] if channel in _SCALAR else values
    return _state_occ(_TOPICS[channel], timestamp_us * 1_000, v=v)


def _act(*, timestamp_us, channel, value, **_):
    return _action_occ(_TOPICS[channel], timestamp_us * 1_000, v=value)


def _manifest_of(
    *,
    observation_frames=(),
    action_frames=(),
    start_timestamp_us,
    end_timestamp_us,
    **_,
):
    occurrences = [*observation_frames, *action_frames]
    stamps = [o.timestamp_ns for o in occurrences]
    return episode_manifest(
        occurrences,
        window=(
            min([start_timestamp_us * 1_000, *stamps]),
            max([end_timestamp_us * 1_000, *stamps]) + 1,
        ),
    )


def _with_battery_hold_last(config):
    """The battery channel holds its last value unless a test says
    otherwise (alignment has no name-based defaults)."""
    from sceneops_core.episodes.alignment import AssociationPolicy, ChannelPolicyConfig

    if BATTERY in config.channel_policies:
        return config
    policies = {
        **config.channel_policies,
        BATTERY: ChannelPolicyConfig(policy=AssociationPolicy.PREVIOUS),
    }
    return config.model_copy(update={"channel_policies": policies})


_CTX = TemporalSourceContext(source_clock=DEFAULT_CLOCK)
_PROFILER = AlignedEpisodeProfiler()


def _artifact_for(manifest, config: TemporalAlignmentConfig) -> AlignedEpisodeArtifact:
    aligned = align_episode(
        manifest, _with_battery_hold_last(config), _CTX, episode_id="ep-1"
    )
    return AlignedEpisodeArtifact(
        source_revision=EpisodeSourceRevision(
            episode_id="ep-1",
            episode_manifest_uri="mem://x.json",
            source_artifact_id="art-1",
            source_manifest_sha256="a" * 64,
        ),
        aligned_episode=aligned,
    )


def _channel(profile, namespace: ChannelNamespace, channel: str):
    return next(
        c
        for c in profile.channel_profiles
        if c.namespace == namespace and c.channel == channel
    )


class TestAllResolved:
    def test_dense_channel_is_fully_resolved(self) -> None:
        manifest = _manifest_of(
            episode_id="ep-1",
            observation_frames=[
                _obs(timestamp_us=t, channel="state.battery", values=[50.0])
                for t in range(0, 3_000_001, 1_000_000)
            ],
            observation_channels=[BATTERY],
            start_timestamp_us=0,
            end_timestamp_us=3_000_000,
            frame_count=4,
        )
        config = TemporalAlignmentConfig(target_frequency_hz=1.0)
        profile = _PROFILER.profile(_artifact_for(manifest, config))
        cp = _channel(profile, ChannelNamespace.OBSERVATION, BATTERY)
        assert cp.resolved_count == 4
        assert cp.missing_count == 0
        assert cp.resolved_ratio == 1.0
        assert cp.missing_ratio == 0.0


class TestAllMissing:
    def test_channel_with_no_samples_at_all_is_fully_missing(self) -> None:
        manifest = _manifest_of(
            episode_id="ep-1",
            observation_frames=[
                _obs(timestamp_us=0, channel="state.battery", values=[50.0]),
                # No further samples -- everything past tolerance is missing.
            ],
            observation_channels=[BATTERY],
            start_timestamp_us=0,
            end_timestamp_us=3_000_000,
            frame_count=1,
        )
        config = TemporalAlignmentConfig(target_frequency_hz=1.0, tolerance_us=1)
        profile = _PROFILER.profile(_artifact_for(manifest, config))
        cp = _channel(profile, ChannelNamespace.OBSERVATION, BATTERY)
        # previous, tolerance_us=1: only t=0 resolves.
        assert cp.resolved_count == 1
        assert cp.missing_count == 3
        assert cp.missing_ratio == pytest.approx(0.75)
        assert cp.longest_missing_run_steps == 3


class TestMixedResolvedMissing:
    def test_leading_missing_then_resolved(self) -> None:
        manifest = _manifest_of(
            episode_id="ep-1",
            action_frames=[
                _act(timestamp_us=2_000_000, channel="steering", value=0.5),
            ],
            action_channels=[STEERING],
            start_timestamp_us=0,
            end_timestamp_us=2_000_000,
            frame_count=1,
        )
        config = TemporalAlignmentConfig(target_frequency_hz=1.0)
        profile = _PROFILER.profile(_artifact_for(manifest, config))
        cp = _channel(profile, ChannelNamespace.ACTION, STEERING)
        assert cp.missing_count == 2  # t=0, t=1_000_000
        assert cp.resolved_count == 1  # t=2_000_000
        assert cp.longest_missing_run_steps == 2
        assert cp.longest_missing_run_us == 2_000_000


class TestMixedInterpolatedDirect:
    def test_interpolated_and_direct_counts_are_distinct(self) -> None:
        manifest = _manifest_of(
            episode_id="ep-1",
            observation_frames=[
                _obs(timestamp_us=0, channel="state.velocity", values=[0.0]),
                _obs(timestamp_us=2_000_000, channel="state.velocity", values=[2.0]),
            ],
            observation_channels=[VELOCITY],
            start_timestamp_us=0,
            end_timestamp_us=2_000_000,
            frame_count=2,
        )
        from sceneops_core.episodes.alignment import (
            AssociationPolicy,
            ChannelPolicyConfig,
        )

        config = TemporalAlignmentConfig(
            target_frequency_hz=1.0,
            channel_policies={
                VELOCITY: ChannelPolicyConfig(
                    policy=AssociationPolicy.LINEAR_INTERPOLATION, max_gap_us=2_000_000
                )
            },
        )
        profile = _PROFILER.profile(_artifact_for(manifest, config))
        cp = _channel(profile, ChannelNamespace.OBSERVATION, VELOCITY)
        # t=0 and t=2_000_000 are exact hits (resolved); t=1_000_000 interpolates.
        assert cp.resolved_count == 2
        assert cp.interpolated_count == 1
        assert cp.mean_interpolation_span_us == 2_000_000
        assert cp.max_interpolation_span_us == 2_000_000


class TestNamespaceCollisionSafety:
    def test_same_channel_string_in_both_namespaces_stays_distinct(self) -> None:
        # Canonical topics are unique, so the collision is forced on the
        # aligned output directly.
        manifest = _manifest_of(
            observation_frames=[_obs(timestamp_us=0, channel="dup", modality="camera")],
            action_frames=[_act(timestamp_us=0, channel="steering", value=9.0)],
            start_timestamp_us=0,
            end_timestamp_us=0,
        )
        config = TemporalAlignmentConfig(target_frequency_hz=1.0, tolerance_us=1)
        artifact = _artifact_for(manifest, config)
        steps = [
            step.model_copy(update={"actions": {"/dup": step.actions[STEERING]}})
            for step in artifact.aligned_episode.steps
        ]
        artifact = artifact.model_copy(
            update={
                "aligned_episode": artifact.aligned_episode.model_copy(
                    update={"steps": steps}
                )
            }
        )
        profile = _PROFILER.profile(artifact)
        obs = _channel(profile, ChannelNamespace.OBSERVATION, "/dup")
        act = _channel(profile, ChannelNamespace.ACTION, "/dup")
        assert obs is not act
        assert len(profile.channel_profiles) == 2


class TestSignedTimeDeltas:
    def test_signed_and_absolute_deltas_differ_for_a_later_source_sample(self) -> None:
        # `previous` (state.battery's default) can never select a source
        # sample after the aligned timestamp, so a negative delta requires
        # `nearest` explicitly here.
        from sceneops_core.episodes.alignment import (
            AssociationPolicy,
            ChannelPolicyConfig,
        )

        manifest = _manifest_of(
            episode_id="ep-1",
            observation_frames=[
                _obs(timestamp_us=800_000, channel="state.battery", values=[1.0]),
            ],
            observation_channels=[BATTERY],
            start_timestamp_us=0,
            end_timestamp_us=0,
            frame_count=1,
        )
        config = TemporalAlignmentConfig(
            target_frequency_hz=1.0,
            channel_policies={
                BATTERY: ChannelPolicyConfig(
                    policy=AssociationPolicy.NEAREST, tolerance_us=1_000_000
                )
            },
        )
        profile = _PROFILER.profile(_artifact_for(manifest, config))
        cp = _channel(profile, ChannelNamespace.OBSERVATION, BATTERY)
        # aligned t=0, source t=800_000 -> time_delta_us = 0 - 800_000 = -800_000
        assert cp.mean_signed_time_delta_us == -800_000
        assert cp.mean_abs_time_delta_us == 800_000
        assert cp.max_abs_time_delta_us == 800_000


class TestMissingUriOnResolvedReference:
    def test_resolved_reference_with_uri_none_is_not_missing(self) -> None:
        manifest = _manifest_of(
            episode_id="ep-1",
            observation_frames=[
                _obs(
                    timestamp_us=0,
                    channel="CAM_FRONT",
                    modality="camera",
                    uri=None,
                ),
            ],
            observation_channels=[CAM_FRONT],
            start_timestamp_us=0,
            end_timestamp_us=0,
            frame_count=1,
        )
        config = TemporalAlignmentConfig(target_frequency_hz=1.0, tolerance_us=1)
        profile = _PROFILER.profile(_artifact_for(manifest, config))
        cp = _channel(profile, ChannelNamespace.OBSERVATION, CAM_FRONT)
        assert cp.resolved_count == 1
        assert cp.missing_count == 0
        assert cp.resolved_reference_count == 1
        assert cp.payload_uri_available_count == 0
        assert cp.payload_uri_missing_count == 1
        assert cp.payload_uri_available_ratio == 0.0


class TestDuplicateDiagnosticsPassthrough:
    def test_duplicate_discarded_count_passes_through_to_profile(self) -> None:
        manifest = _manifest_of(
            episode_id="ep-1",
            observation_frames=[
                _obs(timestamp_us=0, channel="state.battery", values=[1.0]),
                _obs(timestamp_us=0, channel="state.battery", values=[2.0]),
            ],
            observation_channels=[BATTERY],
            start_timestamp_us=0,
            end_timestamp_us=0,
            frame_count=2,
        )
        config = TemporalAlignmentConfig(target_frequency_hz=1.0)
        profile = _PROFILER.profile(_artifact_for(manifest, config))
        assert profile.duplicate_discarded_count == 1


class TestZeroDurationSingleStep:
    def test_zero_duration_episode_profiles_cleanly(self) -> None:
        manifest = _manifest_of(
            episode_id="ep-1",
            observation_frames=[
                _obs(timestamp_us=0, channel="state.battery", values=[1.0]),
            ],
            observation_channels=[BATTERY],
            start_timestamp_us=0,
            end_timestamp_us=0,
            frame_count=1,
        )
        config = TemporalAlignmentConfig(target_frequency_hz=1.0)
        profile = _PROFILER.profile(_artifact_for(manifest, config))
        assert profile.step_count == 1
        assert profile.duration_us == 0
        assert profile.overall_missing_ratio == 0.0


class TestMultipleChannelsDifferentCoverage:
    def test_each_channel_computed_independently(self) -> None:
        manifest = _manifest_of(
            episode_id="ep-1",
            observation_frames=[
                _obs(timestamp_us=t, channel="state.battery", values=[1.0])
                for t in range(0, 3_000_001, 1_000_000)
            ]
            + [_obs(timestamp_us=0, channel="state.velocity", values=[1.0])],
            observation_channels=[BATTERY, VELOCITY],
            start_timestamp_us=0,
            end_timestamp_us=3_000_000,
            frame_count=5,
        )
        config = TemporalAlignmentConfig(target_frequency_hz=1.0, tolerance_us=1)
        profile = _PROFILER.profile(_artifact_for(manifest, config))
        battery = _channel(profile, ChannelNamespace.OBSERVATION, BATTERY)
        velocity = _channel(profile, ChannelNamespace.OBSERVATION, VELOCITY)
        assert battery.missing_ratio == 0.0
        assert velocity.missing_ratio == pytest.approx(0.75)
        assert profile.max_channel_missing_ratio == pytest.approx(0.75)


class TestZeroStepRaisesExplicitly:
    def test_raises_a_clear_domain_error_rather_than_dividing_by_zero(self) -> None:
        manifest = _manifest_of(
            observation_frames=[
                _obs(timestamp_us=0, channel="state.battery", values=[1.0])
            ],
            start_timestamp_us=0,
            end_timestamp_us=0,
        )
        config = _with_battery_hold_last(
            TemporalAlignmentConfig(target_frequency_hz=1.0)
        )
        aligned = align_episode(manifest, config, _CTX, episode_id="ep-1")
        # A genuine align_episode() output always has step_count>=1 -- force
        # the impossible case directly to exercise the profiler's own guard.
        broken = aligned.model_copy(update={"step_count": 0, "steps": []})
        artifact = AlignedEpisodeArtifact(
            source_revision=EpisodeSourceRevision(
                episode_id="ep-1",
                episode_manifest_uri="mem://x.json",
                source_artifact_id="art-1",
                source_manifest_sha256="a" * 64,
            ),
            aligned_episode=broken,
        )
        with pytest.raises(ValueError, match="zero steps|step_count"):
            _PROFILER.profile(artifact)


class TestDeterminism:
    def test_channel_profiles_are_deterministically_sorted(self) -> None:
        manifest = _manifest_of(
            episode_id="ep-1",
            observation_frames=[
                _obs(timestamp_us=0, channel="state.velocity", values=[1.0]),
                _obs(timestamp_us=0, channel="state.battery", values=[1.0]),
            ],
            observation_channels=[BATTERY, VELOCITY],
            start_timestamp_us=0,
            end_timestamp_us=0,
            frame_count=2,
        )
        config = TemporalAlignmentConfig(target_frequency_hz=1.0, tolerance_us=1)
        artifact = _artifact_for(manifest, config)
        profile_a = _PROFILER.profile(artifact)
        profile_b = _PROFILER.profile(artifact)
        assert profile_a.model_dump() == profile_b.model_dump()
        assert [c.channel for c in profile_a.channel_profiles] == [
            BATTERY,
            VELOCITY,
        ]
