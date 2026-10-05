"""Tests for AlignedEpisodeValidator (SceneOps V2 Request 2.4 §43).

Builds a real, valid AlignedEpisodeArtifact via align_episode() (Request
2.2's already-tested pure engine) as the baseline, then mutates individual
fields via model_copy(update=...) to construct each invalid fixture --
cheaper and more realistic than hand-authoring raw JSON for every case, and
proves the validator against genuinely engine-shaped data.
"""

from __future__ import annotations

from sceneops_core.episodes.alignment import (
    AlignedEpisodeArtifact,
    AlignedEpisodeValidator,
    AlignedSignalStatus,
    AlignedValueKind,
    AssociationPolicy,
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
_VALIDATOR = AlignedEpisodeValidator()


def _manifest():
    return _manifest_of(
        episode_id="ep-1",
        observation_frames=[
            _obs(timestamp_us=0, channel="state.position", values=[0.0]),
            _obs(timestamp_us=1_000_000, channel="state.position", values=[1.0]),
        ],
        action_frames=[
            _act(timestamp_us=0, channel="steering", value=0.1),
        ],
        observation_channels=[POSITION],
        action_channels=[STEERING],
        start_timestamp_us=0,
        end_timestamp_us=1_000_000,
        frame_count=3,
    )


def _valid_artifact() -> AlignedEpisodeArtifact:
    config = TemporalAlignmentConfig(target_frequency_hz=1.0, tolerance_us=500_000)
    aligned = align_episode(_manifest(), config, _CTX, episode_id="ep-1")
    return AlignedEpisodeArtifact(
        source_revision=EpisodeSourceRevision(
            episode_id="ep-1",
            episode_manifest_uri="mem://episodes/ep-1.json",
            source_artifact_id="art-1",
            source_manifest_sha256="a" * 64,
        ),
        aligned_episode=aligned,
    )


def _issue_types(artifact: AlignedEpisodeArtifact) -> set[str]:
    return {i.type for i in _VALIDATOR.validate(artifact).issues}


class TestValidBaseline:
    def test_valid_baseline_has_no_issues(self) -> None:
        report = _VALIDATOR.validate(_valid_artifact())
        assert report.valid is True
        assert report.issues == []
        assert "envelope" in report.checked_fields
        assert "signals" in report.checked_fields


class TestEnvelopeChecks:
    def test_unsupported_schema_version(self) -> None:
        artifact = _valid_artifact().model_copy(update={"schema_version": "v99"})
        assert "unsupported_schema_version" in _issue_types(artifact)

    def test_episode_id_mismatch(self) -> None:
        artifact = _valid_artifact()
        bad_revision = artifact.source_revision.model_copy(
            update={"episode_id": "ep-2"}
        )
        artifact = artifact.model_copy(update={"source_revision": bad_revision})
        assert "episode_id_mismatch" in _issue_types(artifact)

    def test_malformed_source_manifest_sha256(self) -> None:
        artifact = _valid_artifact()
        bad_revision = artifact.source_revision.model_copy(
            update={"source_manifest_sha256": "not-a-hash"}
        )
        artifact = artifact.model_copy(update={"source_revision": bad_revision})
        assert "malformed_source_manifest_sha256" in _issue_types(artifact)

    def test_missing_source_artifact_id(self) -> None:
        artifact = _valid_artifact()
        bad_revision = artifact.source_revision.model_copy(
            update={"source_artifact_id": ""}
        )
        artifact = artifact.model_copy(update={"source_revision": bad_revision})
        assert "missing_source_artifact_id" in _issue_types(artifact)

    def test_unsupported_alignment_semantics_version(self) -> None:
        artifact = _valid_artifact()
        ae = artifact.aligned_episode.model_copy(
            update={"alignment_semantics_version": "v99"}
        )
        artifact = artifact.model_copy(update={"aligned_episode": ae})
        assert "unsupported_alignment_semantics_version" in _issue_types(artifact)


class TestBoundsAndTimeline:
    def test_start_after_end(self) -> None:
        artifact = _valid_artifact()
        ae = artifact.aligned_episode.model_copy(
            update={"source_start_timestamp_us": 2_000_000}
        )
        artifact = artifact.model_copy(update={"aligned_episode": ae})
        assert "invalid_bounds" in _issue_types(artifact)

    def test_step_count_mismatch(self) -> None:
        artifact = _valid_artifact()
        ae = artifact.aligned_episode.model_copy(update={"step_count": 999})
        artifact = artifact.model_copy(update={"aligned_episode": ae})
        assert "step_count_mismatch" in _issue_types(artifact)
        assert "step_count_inconsistent_with_bounds" in _issue_types(artifact)

    def test_first_step_not_at_start(self) -> None:
        artifact = _valid_artifact()
        steps = list(artifact.aligned_episode.steps)
        steps[0] = steps[0].model_copy(update={"timestamp_us": 999})
        ae = artifact.aligned_episode.model_copy(update={"steps": steps})
        artifact = artifact.model_copy(update={"aligned_episode": ae})
        assert "first_step_not_at_start" in _issue_types(artifact)

    def test_non_monotonic_timestamps(self) -> None:
        artifact = _valid_artifact()
        steps = list(artifact.aligned_episode.steps)
        # two steps -> force second to equal first
        steps[1] = steps[1].model_copy(update={"timestamp_us": steps[0].timestamp_us})
        ae = artifact.aligned_episode.model_copy(update={"steps": steps})
        artifact = artifact.model_copy(update={"aligned_episode": ae})
        assert "non_monotonic_timestamps" in _issue_types(artifact)

    def test_wrong_dt_spacing(self) -> None:
        artifact = _valid_artifact()
        steps = list(artifact.aligned_episode.steps)
        steps[1] = steps[1].model_copy(
            update={"timestamp_us": steps[0].timestamp_us + 500_000}
        )
        ae = artifact.aligned_episode.model_copy(update={"steps": steps})
        artifact = artifact.model_copy(update={"aligned_episode": ae})
        assert "wrong_step_spacing" in _issue_types(artifact)

    def test_step_outside_bounds(self) -> None:
        artifact = _valid_artifact()
        steps = list(artifact.aligned_episode.steps)
        steps[1] = steps[1].model_copy(update={"timestamp_us": 5_000_000})
        ae = artifact.aligned_episode.model_copy(update={"steps": steps})
        artifact = artifact.model_copy(update={"aligned_episode": ae})
        assert "step_outside_bounds" in _issue_types(artifact)

    def test_invalid_achieved_frequency(self) -> None:
        artifact = _valid_artifact()
        ae = artifact.aligned_episode.model_copy(update={"achieved_frequency_hz": -1.0})
        artifact = artifact.model_copy(update={"aligned_episode": ae})
        assert "invalid_achieved_frequency" in _issue_types(artifact)

    def test_achieved_frequency_inconsistent_with_dt(self) -> None:
        artifact = _valid_artifact()
        ae = artifact.aligned_episode.model_copy(update={"achieved_frequency_hz": 42.0})
        artifact = artifact.model_copy(update={"aligned_episode": ae})
        assert "achieved_frequency_inconsistent_with_dt" in _issue_types(artifact)


class TestSignalConsistency:
    def test_resolved_signal_without_value(self) -> None:
        artifact = _valid_artifact()
        step0 = artifact.aligned_episode.steps[0]
        signal = step0.actions[STEERING]
        assert signal.status == AlignedSignalStatus.RESOLVED
        bad_signal = signal.model_copy(update={"value": None})
        step0 = step0.model_copy(
            update={"actions": {**step0.actions, STEERING: bad_signal}}
        )
        steps = [step0] + list(artifact.aligned_episode.steps[1:])
        ae = artifact.aligned_episode.model_copy(update={"steps": steps})
        artifact = artifact.model_copy(update={"aligned_episode": ae})
        assert "resolved_signal_has_no_value" in _issue_types(artifact)

    def test_missing_signal_with_contradictory_value(self) -> None:
        artifact = _valid_artifact()
        step1 = artifact.aligned_episode.steps[1]
        resolved_signal = step1.observations[POSITION]
        assert resolved_signal.status == AlignedSignalStatus.RESOLVED
        # Force a MISSING status but leave the value/provenance populated --
        # a direct contradiction.
        bad_signal = resolved_signal.model_copy(
            update={"status": AlignedSignalStatus.MISSING}
        )
        step1 = step1.model_copy(
            update={"observations": {**step1.observations, POSITION: bad_signal}}
        )
        steps = [artifact.aligned_episode.steps[0], step1]
        ae = artifact.aligned_episode.model_copy(update={"steps": steps})
        artifact = artifact.model_copy(update={"aligned_episode": ae})
        types = _issue_types(artifact)
        assert "missing_signal_has_value" in types
        assert "missing_signal_has_provenance" in types

    def test_exact_policy_with_nonzero_delta(self) -> None:
        artifact = _valid_artifact()
        step0 = artifact.aligned_episode.steps[0]
        signal = step0.actions[STEERING].model_copy(
            update={"policy": AssociationPolicy.EXACT, "time_delta_us": 5}
        )
        step0 = step0.model_copy(
            update={"actions": {**step0.actions, STEERING: signal}}
        )
        steps = [step0] + list(artifact.aligned_episode.steps[1:])
        ae = artifact.aligned_episode.model_copy(update={"steps": steps})
        artifact = artifact.model_copy(update={"aligned_episode": ae})
        assert "exact_policy_nonzero_delta" in _issue_types(artifact)

    def test_previous_policy_with_negative_delta(self) -> None:
        artifact = _valid_artifact()
        step0 = artifact.aligned_episode.steps[0]
        original = step0.actions[STEERING]
        assert original.policy == AssociationPolicy.PREVIOUS
        signal = original.model_copy(update={"time_delta_us": -5})
        step0 = step0.model_copy(
            update={"actions": {**step0.actions, STEERING: signal}}
        )
        steps = [step0] + list(artifact.aligned_episode.steps[1:])
        ae = artifact.aligned_episode.model_copy(update={"steps": steps})
        artifact = artifact.model_copy(update={"aligned_episode": ae})
        types = _issue_types(artifact)
        assert "previous_policy_negative_delta" in types
        assert (
            "time_delta_inconsistent" in types
        )  # delta no longer matches timestamp math

    def test_interpolated_signal_without_bracket(self) -> None:
        artifact = _valid_artifact()
        step0 = artifact.aligned_episode.steps[0]
        original = step0.observations[POSITION]
        bad_signal = original.model_copy(
            update={
                "status": AlignedSignalStatus.INTERPOLATED,
                "policy": AssociationPolicy.LINEAR_INTERPOLATION,
                "source_before_timestamp_us": None,
                "source_after_timestamp_us": None,
            }
        )
        step0 = step0.model_copy(
            update={"observations": {**step0.observations, POSITION: bad_signal}}
        )
        steps = [step0] + list(artifact.aligned_episode.steps[1:])
        ae = artifact.aligned_episode.model_copy(update={"steps": steps})
        artifact = artifact.model_copy(update={"aligned_episode": ae})
        assert "interpolated_signal_missing_bracket" in _issue_types(artifact)

    def test_invalid_interpolation_bracket_ordering(self) -> None:
        artifact = _valid_artifact()
        step0 = artifact.aligned_episode.steps[0]
        original = step0.observations[POSITION]
        bad_signal = original.model_copy(
            update={
                "status": AlignedSignalStatus.INTERPOLATED,
                "policy": AssociationPolicy.LINEAR_INTERPOLATION,
                "value": original.value,
                "source_before_timestamp_us": 500,
                "source_after_timestamp_us": 100,  # after < before: invalid
            }
        )
        step0 = step0.model_copy(
            update={"observations": {**step0.observations, POSITION: bad_signal}}
        )
        steps = [step0] + list(artifact.aligned_episode.steps[1:])
        ae = artifact.aligned_episode.model_copy(update={"steps": steps})
        artifact = artifact.model_copy(update={"aligned_episode": ae})
        assert "interpolation_bracket_ordering_invalid" in _issue_types(artifact)

    def test_binary_reference_interpolated_is_invalid(self) -> None:
        from sceneops_core.episodes.alignment import AlignedValue

        artifact = _valid_artifact()
        step0 = artifact.aligned_episode.steps[0]
        original = step0.observations[POSITION]
        reference_value = AlignedValue(
            kind=AlignedValueKind.REFERENCE, reference_uri=None
        )
        bad_signal = original.model_copy(
            update={
                "status": AlignedSignalStatus.INTERPOLATED,
                "policy": AssociationPolicy.LINEAR_INTERPOLATION,
                "value": reference_value,
                "source_before_timestamp_us": 0,
                "source_after_timestamp_us": 1_000_000,
            }
        )
        step0 = step0.model_copy(
            update={"observations": {**step0.observations, POSITION: bad_signal}}
        )
        steps = [step0] + list(artifact.aligned_episode.steps[1:])
        ae = artifact.aligned_episode.model_copy(update={"steps": steps})
        artifact = artifact.model_copy(update={"aligned_episode": ae})
        assert "interpolated_unsupported_value_kind" in _issue_types(artifact)

    def test_unstable_channel_keys(self) -> None:
        artifact = _valid_artifact()
        step1 = artifact.aligned_episode.steps[1]
        step1 = step1.model_copy(update={"observations": {}})
        steps = [artifact.aligned_episode.steps[0], step1]
        ae = artifact.aligned_episode.model_copy(update={"steps": steps})
        artifact = artifact.model_copy(update={"aligned_episode": ae})
        assert "unstable_observation_channel_keys" in _issue_types(artifact)


class TestDuplicateDiagnostic:
    def test_negative_duplicate_discarded_count(self) -> None:
        artifact = _valid_artifact()
        ae = artifact.aligned_episode.model_copy(
            update={"duplicate_discarded_count": -1}
        )
        artifact = artifact.model_copy(update={"aligned_episode": ae})
        assert "negative_duplicate_discarded_count" in _issue_types(artifact)


class TestDescriptiveConditionsAreNotFailures:
    """SceneOps V2 Request 2.4 §18: undesirable-but-policy-valid conditions
    must never fail structural validation."""

    def test_large_but_valid_nearest_delta_is_not_a_failure(self) -> None:
        manifest = _manifest_of(
            episode_id="ep-1",
            observation_frames=[
                _obs(timestamp_us=0, channel="state.position", values=[0.0]),
                _obs(timestamp_us=9_000_000, channel="state.position", values=[9.0]),
            ],
            observation_channels=[POSITION],
            start_timestamp_us=0,
            end_timestamp_us=10_000_000,
            frame_count=2,
        )
        config = TemporalAlignmentConfig(
            target_frequency_hz=1.0, tolerance_us=10_000_000
        )
        aligned = align_episode(manifest, config, _CTX, episode_id="ep-1")
        artifact = AlignedEpisodeArtifact(
            source_revision=EpisodeSourceRevision(
                episode_id="ep-1",
                episode_manifest_uri="mem://x.json",
                source_artifact_id="art-1",
                source_manifest_sha256="a" * 64,
            ),
            aligned_episode=aligned,
        )
        report = _VALIDATOR.validate(artifact)
        assert report.valid is True

    def test_mostly_missing_episode_is_still_structurally_valid(self) -> None:
        manifest = _manifest_of(
            episode_id="ep-1",
            observation_frames=[
                _obs(timestamp_us=0, channel="state.position", values=[0.0]),
            ],
            observation_channels=[POSITION],
            start_timestamp_us=0,
            end_timestamp_us=10_000_000,
            frame_count=1,
        )
        config = TemporalAlignmentConfig(target_frequency_hz=1.0, tolerance_us=1)
        aligned = align_episode(manifest, config, _CTX, episode_id="ep-1")
        artifact = AlignedEpisodeArtifact(
            source_revision=EpisodeSourceRevision(
                episode_id="ep-1",
                episode_manifest_uri="mem://x.json",
                source_artifact_id="art-1",
                source_manifest_sha256="a" * 64,
            ),
            aligned_episode=aligned,
        )
        report = _VALIDATOR.validate(artifact)
        assert report.valid is True
