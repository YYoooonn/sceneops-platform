from __future__ import annotations

from collections.abc import Sequence

from sceneops_core.episodes.alignment.enums import (
    AlignedSignalStatus,
    AlignedValueKind,
    ChannelNamespace,
)
from sceneops_core.episodes.alignment.schemas import AlignedSignal, LearningStep

from .enums import MissingFeaturePolicy
from .errors import (
    FeatureAbsentError,
    FeatureMissingError,
    FeatureShapeMismatchError,
    SequenceBoundaryError,
    UnsupportedFeatureKindError,
)
from .schemas import (
    EpisodeRef,
    FeatureProjection,
    FeatureSchema,
    FeatureSchemaEntry,
    SequenceRef,
    SequenceSample,
    StepSample,
)

# v1 dense training projection only supports these two structural kinds
# (SceneOps V2 Request 2.7A §5) -- orientation/reference are structurally
# ineligible and always raise UnsupportedFeatureKindError.
_SUPPORTED_KINDS = (AlignedValueKind.NUMERIC_SCALAR, AlignedValueKind.NUMERIC_VECTOR)


def _channel_dict(
    step: LearningStep, namespace: ChannelNamespace
) -> dict[str, AlignedSignal]:
    return (
        step.observations if namespace == ChannelNamespace.OBSERVATION else step.actions
    )


def _signal_shape(
    channel: str, namespace: ChannelNamespace, signal: AlignedSignal
) -> tuple[AlignedValueKind, int]:
    """Kind + flattened dimension of a RESOLVED/INTERPOLATED signal's value.
    Never called for status=MISSING (value is always None there)."""
    value = signal.value
    assert value is not None, "RESOLVED/INTERPOLATED signals always carry a value"
    if value.kind not in _SUPPORTED_KINDS:
        raise UnsupportedFeatureKindError(
            f"channel {channel!r} ({namespace.value}) resolves to "
            f"value.kind={value.kind!r}, which v1 dense projection does not "
            "support (only numeric_scalar/numeric_vector)"
        )
    if value.kind == AlignedValueKind.NUMERIC_SCALAR:
        return value.kind, 1
    return value.kind, len(value.vector or [])


def resolve_feature_schema(
    steps: Sequence[LearningStep], projection: FeatureProjection
) -> FeatureSchema:
    """Resolve a FeatureProjection's dense shape against a full sequence of
    LearningSteps (SceneOps V2 Request 2.7A §7/§8). Scans every step so a
    channel whose numeric kind/dimension changes partway through the
    Episode fails here, once, rather than surfacing later as an
    inconsistent per-step shape.

    A channel absent from the Episode's declared channel set raises
    FeatureAbsentError; a channel that is status=missing at every step (so
    no value ever exists to infer a shape from) raises FeatureMissingError.
    """
    observations, observation_dim = _resolve_namespace(
        steps, projection.observation_channels, ChannelNamespace.OBSERVATION
    )
    actions, action_dim = _resolve_namespace(
        steps, projection.action_channels, ChannelNamespace.ACTION
    )
    return FeatureSchema(
        observations=observations,
        actions=actions,
        observation_dim=observation_dim,
        action_dim=action_dim,
    )


def _resolve_namespace(
    steps: Sequence[LearningStep],
    channels: Sequence[str],
    namespace: ChannelNamespace,
) -> tuple[list[FeatureSchemaEntry], int]:
    if not channels:
        return [], 0
    if not steps:
        raise ValueError(
            "resolve_feature_schema requires at least one step to resolve a "
            "schema against"
        )

    entries: list[FeatureSchemaEntry] = []
    offset = 0
    first_step_channels = _channel_dict(steps[0], namespace)

    for channel in channels:
        if channel not in first_step_channels:
            raise FeatureAbsentError(
                f"channel {channel!r} ({namespace.value}) is not declared by "
                "this AlignedEpisode"
            )

        resolved: tuple[AlignedValueKind, int] | None = None
        for step in steps:
            signal = _channel_dict(step, namespace)[channel]
            if signal.status == AlignedSignalStatus.MISSING:
                continue
            shape = _signal_shape(channel, namespace, signal)
            if resolved is None:
                resolved = shape
            elif resolved != shape:
                raise FeatureShapeMismatchError(
                    f"channel {channel!r} ({namespace.value}) resolved to "
                    f"kind/dimension {resolved} earlier in the Episode but "
                    f"{shape} elsewhere -- numeric kind/dimension must stay "
                    "stable across an Episode"
                )

        if resolved is None:
            raise FeatureMissingError(
                f"channel {channel!r} ({namespace.value}) has status=missing "
                "at every step -- no value is ever available to resolve a "
                "dense schema from"
            )

        kind, dimension = resolved
        entries.append(
            FeatureSchemaEntry(
                channel=channel, kind=kind, dimension=dimension, offset=offset
            )
        )
        offset += dimension

    return entries, offset


def project_step(
    step: LearningStep,
    *,
    step_index: int,
    episode_ref: EpisodeRef,
    schema: FeatureSchema,
    missing_policy: MissingFeaturePolicy = MissingFeaturePolicy.ERROR,
) -> StepSample:
    """Project one LearningStep into a dense StepSample against an
    already-resolved FeatureSchema (SceneOps V2 Request 2.7A §11)."""
    observation = _project_namespace(
        step,
        schema.observations,
        ChannelNamespace.OBSERVATION,
        step_index,
        missing_policy,
    )
    action = _project_namespace(
        step, schema.actions, ChannelNamespace.ACTION, step_index, missing_policy
    )
    return StepSample(
        episode_ref=episode_ref,
        step_index=step_index,
        timestamp_us=step.timestamp_us,
        observation=observation,
        action=action,
    )


def _project_namespace(
    step: LearningStep,
    entries: Sequence[FeatureSchemaEntry],
    namespace: ChannelNamespace,
    step_index: int,
    missing_policy: MissingFeaturePolicy,
) -> list[float]:
    channel_dict = _channel_dict(step, namespace)
    dense: list[float] = []

    for entry in entries:
        if entry.channel not in channel_dict:
            raise FeatureAbsentError(
                f"channel {entry.channel!r} ({namespace.value}) is not "
                f"declared at step_index={step_index}"
            )

        signal = channel_dict[entry.channel]
        if signal.status == AlignedSignalStatus.MISSING:
            # v1 supports exactly one policy -- MissingFeaturePolicy.ERROR
            # (Request 2.7A §9). The parameter is threaded through here so a
            # future masking/drop policy doesn't require changing this
            # function's signature.
            if missing_policy is not MissingFeaturePolicy.ERROR:
                raise NotImplementedError(
                    f"MissingFeaturePolicy.{missing_policy.name} is not implemented"
                )
            raise FeatureMissingError(
                f"channel {entry.channel!r} ({namespace.value}) has "
                f"status=missing at step_index={step_index} "
                f"(timestamp_us={step.timestamp_us})"
            )

        shape = _signal_shape(entry.channel, namespace, signal)
        if shape != (entry.kind, entry.dimension):
            raise FeatureShapeMismatchError(
                f"channel {entry.channel!r} ({namespace.value}) resolved to "
                f"schema shape {(entry.kind, entry.dimension)} but "
                f"step_index={step_index} has shape {shape}"
            )

        value = signal.value
        assert value is not None
        if entry.kind == AlignedValueKind.NUMERIC_SCALAR:
            assert value.scalar is not None
            dense.append(float(value.scalar))
        else:
            dense.extend(float(x) for x in (value.vector or []))

    return dense


def validate_sequence_bounds(sequence_ref: SequenceRef, *, step_count: int) -> None:
    """Pure Episode-boundary contract (SceneOps V2 Request 2.7A §14): a
    sequence is valid only if start_step + horizon <= step_count."""
    end = sequence_ref.start_step + sequence_ref.horizon
    if end > step_count:
        raise SequenceBoundaryError(
            f"SequenceRef(start_step={sequence_ref.start_step}, "
            f"horizon={sequence_ref.horizon}) needs steps up to index "
            f"{end - 1}, but the Episode only has step_count={step_count}"
        )


def project_sequence(
    steps: Sequence[LearningStep],
    *,
    schema: FeatureSchema,
    sequence_ref: SequenceRef,
    missing_policy: MissingFeaturePolicy = MissingFeaturePolicy.ERROR,
) -> SequenceSample:
    """Project a contiguous window of LearningSteps into a dense,
    fixed-horizon SequenceSample (SceneOps V2 Request 2.7A §13). Never
    crosses an Episode boundary and never pads -- validate_sequence_bounds
    enforces start_step + horizon <= len(steps) before any projection
    happens. All steps come from the same `steps` sequence, i.e. the same
    exact aligned revision."""
    validate_sequence_bounds(sequence_ref, step_count=len(steps))

    window = steps[
        sequence_ref.start_step : sequence_ref.start_step + sequence_ref.horizon
    ]
    step_samples = [
        project_step(
            step,
            step_index=sequence_ref.start_step + i,
            episode_ref=sequence_ref.episode_ref,
            schema=schema,
            missing_policy=missing_policy,
        )
        for i, step in enumerate(window)
    ]

    return SequenceSample(
        episode_ref=sequence_ref.episode_ref,
        start_step=sequence_ref.start_step,
        horizon=sequence_ref.horizon,
        timestamps_us=[s.timestamp_us for s in step_samples],
        observation=[s.observation for s in step_samples],
        action=[s.action for s in step_samples],
    )
