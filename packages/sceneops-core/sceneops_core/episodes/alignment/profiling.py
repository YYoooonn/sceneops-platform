from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import SceneOpsBaseModel

from .enums import AlignedSignalStatus, AlignedValueKind, ChannelNamespace
from .persistence import AlignedEpisodeArtifact
from .schemas import AlignedEpisode, AlignedSignal

# Distinct from ALIGNED_EPISODE_ARTIFACT_SCHEMA_VERSION and
# ALIGNMENT_SEMANTICS_VERSION -- this is the *profiler's own metric
# definitions* version (SceneOps V2 Request 2.4 §31). Bump it when a
# metric's definition changes, independent of either of the other two.
ALIGNED_EPISODE_PROFILE_SEMANTICS_VERSION = "v1"


class ChannelCoverageProfile(SceneOpsBaseModel):
    """Descriptive coverage/association facts for one channel (SceneOps V2
    Request 2.4 §21-26). ``namespace`` + ``channel`` together are the key --
    ``observation:state.position`` and ``action:steering`` never collide
    even if two channels ever shared a bare string name."""

    namespace: ChannelNamespace
    channel: str

    total_step_count: int
    resolved_count: int
    missing_count: int
    interpolated_count: int

    resolved_ratio: float
    missing_ratio: float
    interpolated_ratio: float

    # Direct association (exact/nearest/previous) delta statistics.
    direct_association_count: int
    mean_abs_time_delta_us: float | None = None
    max_abs_time_delta_us: int | None = None
    mean_signed_time_delta_us: float | None = None

    # Interpolation span statistics.
    mean_interpolation_span_us: float | None = None
    max_interpolation_span_us: int | None = None

    # Temporal continuity of gaps -- a scattered 10% missing and one 10%
    # outage are materially different for curation even at equal ratios.
    longest_missing_run_steps: int = 0
    longest_missing_run_us: int = 0

    # Reference/binary channels only (None for numeric/orientation channels,
    # and also None if every step for this channel was missing -- an
    # all-missing channel never reveals its own value kind). Resolved
    # temporal association is distinct from payload URI availability
    # (Request 2.2 §20/§31): a matched frame with uri=None is resolved, not
    # missing.
    resolved_reference_count: int | None = None
    payload_uri_available_count: int | None = None
    payload_uri_missing_count: int | None = None
    payload_uri_available_ratio: float | None = None


class AlignedEpisodeProfile(SceneOpsBaseModel):
    """Descriptive-only analysis of one AlignedEpisodeArtifact (SceneOps V2
    Request 2.4). Contains measurable facts, never a verdict -- no
    good/bad/ready/blocked/score field exists anywhere on this schema by
    design."""

    profile_semantics_version: str = ALIGNED_EPISODE_PROFILE_SEMANTICS_VERSION
    episode_id: str

    step_count: int
    duration_us: int

    target_frequency_hz: float
    achieved_frequency_hz: float
    frequency_error_hz: float
    frequency_error_ratio: float

    observation_channel_count: int
    action_channel_count: int

    duplicate_discarded_count: int

    channel_profiles: list[ChannelCoverageProfile] = Field(default_factory=list)

    # Episode-level aggregates -- each is a plain mathematical function of
    # the per-channel values above, never an independent judgment call.
    overall_missing_ratio: float
    max_channel_missing_ratio: float
    max_abs_sync_delta_us: int | None = None


class AlignedEpisodeProfiler:
    """Pure descriptive profiler for AlignedEpisodeArtifact (SceneOps V2
    Request 2.4). No DB/storage/network dependency; deterministic --
    channel_profiles is always sorted (namespace, channel) so output never
    depends on dict iteration order.

    Assumes the artifact is already structurally valid (step_count >= 1) --
    AlignedEpisodeValidator is the layer responsible for catching a
    zero-step artifact; this profiler fails with a clear, explicit error
    rather than silently dividing by zero if handed one anyway (Request 2.4
    §47).
    """

    def profile(self, artifact: AlignedEpisodeArtifact) -> AlignedEpisodeProfile:
        ae = artifact.aligned_episode
        if ae.step_count < 1 or not ae.steps:
            raise ValueError(
                f"cannot profile episode_id={ae.episode_id!r}: step_count="
                f"{ae.step_count} / len(steps)={len(ae.steps)} -- an "
                "AlignedEpisode must have at least one step under the v1 "
                "timeline contract"
            )

        observation_channels = sorted(ae.steps[0].observations.keys())
        action_channels = sorted(ae.steps[0].actions.keys())

        channel_profiles = [
            self._profile_channel(
                ae, "observations", channel, ChannelNamespace.OBSERVATION
            )
            for channel in observation_channels
        ] + [
            self._profile_channel(ae, "actions", channel, ChannelNamespace.ACTION)
            for channel in action_channels
        ]

        overall_total = sum(cp.total_step_count for cp in channel_profiles)
        overall_missing = sum(cp.missing_count for cp in channel_profiles)
        overall_missing_ratio = (
            overall_missing / overall_total if overall_total else 0.0
        )
        max_channel_missing_ratio = max(
            (cp.missing_ratio for cp in channel_profiles), default=0.0
        )
        channel_max_deltas = [
            cp.max_abs_time_delta_us
            for cp in channel_profiles
            if cp.max_abs_time_delta_us is not None
        ]
        max_abs_sync_delta_us = max(channel_max_deltas) if channel_max_deltas else None

        duration_us = ae.source_end_timestamp_us - ae.source_start_timestamp_us
        frequency_error_hz = ae.achieved_frequency_hz - ae.target_frequency_hz
        frequency_error_ratio = (
            frequency_error_hz / ae.target_frequency_hz
            if ae.target_frequency_hz
            else 0.0
        )

        return AlignedEpisodeProfile(
            episode_id=ae.episode_id,
            step_count=ae.step_count,
            duration_us=duration_us,
            target_frequency_hz=ae.target_frequency_hz,
            achieved_frequency_hz=ae.achieved_frequency_hz,
            frequency_error_hz=frequency_error_hz,
            frequency_error_ratio=frequency_error_ratio,
            observation_channel_count=len(observation_channels),
            action_channel_count=len(action_channels),
            duplicate_discarded_count=ae.duplicate_discarded_count,
            channel_profiles=channel_profiles,
            overall_missing_ratio=overall_missing_ratio,
            max_channel_missing_ratio=max_channel_missing_ratio,
            max_abs_sync_delta_us=max_abs_sync_delta_us,
        )

    @staticmethod
    def _profile_channel(
        ae: AlignedEpisode,
        group_attr: str,
        channel: str,
        namespace: ChannelNamespace,
    ) -> ChannelCoverageProfile:
        total = len(ae.steps)
        resolved = missing = interpolated = 0
        deltas: list[int] = []
        interpolation_spans: list[int] = []

        current_missing_run = 0
        longest_missing_run = 0

        is_reference = False
        resolved_reference = 0
        uri_available = 0
        uri_missing = 0

        for step in ae.steps:
            signal: AlignedSignal = getattr(step, group_attr)[channel]

            if signal.status == AlignedSignalStatus.MISSING:
                missing += 1
                current_missing_run += 1
                longest_missing_run = max(longest_missing_run, current_missing_run)
                continue

            current_missing_run = 0

            if signal.status == AlignedSignalStatus.RESOLVED:
                resolved += 1
                if signal.time_delta_us is not None:
                    deltas.append(signal.time_delta_us)
                if signal.value is not None and signal.value.kind == (
                    AlignedValueKind.REFERENCE
                ):
                    is_reference = True
                    resolved_reference += 1
                    if signal.value.reference_uri is not None:
                        uri_available += 1
                    else:
                        uri_missing += 1
            elif signal.status == AlignedSignalStatus.INTERPOLATED:
                interpolated += 1
                if (
                    signal.source_before_timestamp_us is not None
                    and signal.source_after_timestamp_us is not None
                ):
                    interpolation_spans.append(
                        signal.source_after_timestamp_us
                        - signal.source_before_timestamp_us
                    )

        direct_count = len(deltas)
        abs_deltas = [abs(d) for d in deltas]

        return ChannelCoverageProfile(
            namespace=namespace,
            channel=channel,
            total_step_count=total,
            resolved_count=resolved,
            missing_count=missing,
            interpolated_count=interpolated,
            resolved_ratio=resolved / total,
            missing_ratio=missing / total,
            interpolated_ratio=interpolated / total,
            direct_association_count=direct_count,
            mean_abs_time_delta_us=(
                sum(abs_deltas) / direct_count if direct_count else None
            ),
            max_abs_time_delta_us=max(abs_deltas) if abs_deltas else None,
            mean_signed_time_delta_us=(
                sum(deltas) / direct_count if direct_count else None
            ),
            mean_interpolation_span_us=(
                sum(interpolation_spans) / len(interpolation_spans)
                if interpolation_spans
                else None
            ),
            max_interpolation_span_us=(
                max(interpolation_spans) if interpolation_spans else None
            ),
            longest_missing_run_steps=longest_missing_run,
            longest_missing_run_us=longest_missing_run * ae.dt_us,
            resolved_reference_count=resolved_reference if is_reference else None,
            payload_uri_available_count=uri_available if is_reference else None,
            payload_uri_missing_count=uri_missing if is_reference else None,
            payload_uri_available_ratio=(
                uri_available / resolved_reference
                if is_reference and resolved_reference
                else None
            ),
        )
