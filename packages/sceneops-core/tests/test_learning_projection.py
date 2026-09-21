"""Tests for the native learning dataset contract (SceneOps V2 Request
2.7A). Pure in-memory fixtures only -- no Parquet/DB/ArtifactStore/Polars/
DuckDB/network/worker access anywhere in this module.
"""

from __future__ import annotations

import pytest

from sceneops_core.episodes.alignment import (
    AlignedSignal,
    AlignedSignalStatus,
    AlignedValue,
    AlignedValueKind,
    AssociationPolicy,
    LearningStep,
)
from sceneops_core.episodes.learning import (
    DuplicateFeatureChannelError,
    EpisodeRef,
    FeatureAbsentError,
    FeatureMissingError,
    FeatureProjection,
    FeatureShapeMismatchError,
    SequenceBoundaryError,
    SequenceRef,
    UnsupportedFeatureKindError,
    project_sequence,
    project_step,
    resolve_feature_schema,
    validate_sequence_bounds,
)

# ── fixture builders ────────────────────────────────────────────────────


def _episode_ref(checksum: str = "a" * 64) -> EpisodeRef:
    return EpisodeRef(episode_id="ep-1", aligned_artifact_checksum=checksum)


def _scalar(value: float) -> AlignedSignal:
    return AlignedSignal(
        channel="c",
        policy=AssociationPolicy.EXACT,
        status=AlignedSignalStatus.RESOLVED,
        value=AlignedValue(kind=AlignedValueKind.NUMERIC_SCALAR, scalar=value),
        source_timestamp_us=0,
        time_delta_us=0,
    )


def _vector(values: list[float]) -> AlignedSignal:
    return AlignedSignal(
        channel="c",
        policy=AssociationPolicy.EXACT,
        status=AlignedSignalStatus.RESOLVED,
        value=AlignedValue(kind=AlignedValueKind.NUMERIC_VECTOR, vector=values),
        source_timestamp_us=0,
        time_delta_us=0,
    )


def _interpolated_scalar(value: float) -> AlignedSignal:
    return AlignedSignal(
        channel="c",
        policy=AssociationPolicy.LINEAR_INTERPOLATION,
        status=AlignedSignalStatus.INTERPOLATED,
        value=AlignedValue(kind=AlignedValueKind.NUMERIC_SCALAR, scalar=value),
        source_before_timestamp_us=0,
        source_after_timestamp_us=1_000,
        interpolation_ratio=0.5,
    )


def _missing() -> AlignedSignal:
    return AlignedSignal(
        channel="c",
        policy=AssociationPolicy.EXACT,
        status=AlignedSignalStatus.MISSING,
    )


def _reference() -> AlignedSignal:
    return AlignedSignal(
        channel="c",
        policy=AssociationPolicy.EXACT,
        status=AlignedSignalStatus.RESOLVED,
        value=AlignedValue(
            kind=AlignedValueKind.REFERENCE,
            reference_channel="camera.front",
            reference_uri="mem://frame.png",
        ),
        source_timestamp_us=0,
        time_delta_us=0,
    )


def _step(timestamp_us: int, observations: dict, actions: dict) -> LearningStep:
    return LearningStep(
        timestamp_us=timestamp_us, observations=observations, actions=actions
    )


def _multi_step_episode(n: int) -> list[LearningStep]:
    # 0.5 multiples stay exactly representable in binary floating point, so
    # equality assertions below never trip on rounding.
    return [
        _step(
            i * 1_000,
            observations={"state.position": _scalar(float(i))},
            actions={"steering": _scalar(float(i) * 0.5)},
        )
        for i in range(n)
    ]


# ── EpisodeRef identity ─────────────────────────────────────────────────


class TestEpisodeRefIdentity:
    def test_same_episode_id_different_checksum_are_distinct(self) -> None:
        a = _episode_ref("a" * 64)
        b = _episode_ref("b" * 64)
        assert a != b
        assert hash(a) != hash(b)
        assert len({a, b}) == 2

    def test_same_episode_id_same_checksum_are_equal(self) -> None:
        a = _episode_ref("a" * 64)
        b = _episode_ref("a" * 64)
        assert a == b
        assert hash(a) == hash(b)


# ── feature ordering ────────────────────────────────────────────────────


class TestFeatureOrdering:
    def test_order_determines_dense_output(self) -> None:
        step = _step(
            0,
            observations={
                "state.position": _scalar(1.0),
                "state.velocity": _scalar(2.0),
            },
            actions={},
        )
        ref = _episode_ref()

        schema_ab = resolve_feature_schema(
            [step],
            FeatureProjection(
                observation_channels=["state.position", "state.velocity"]
            ),
        )
        sample_ab = project_step(step, step_index=0, episode_ref=ref, schema=schema_ab)

        schema_ba = resolve_feature_schema(
            [step],
            FeatureProjection(
                observation_channels=["state.velocity", "state.position"]
            ),
        )
        sample_ba = project_step(step, step_index=0, episode_ref=ref, schema=schema_ba)

        assert sample_ab.observation == [1.0, 2.0]
        assert sample_ba.observation == [2.0, 1.0]
        assert sample_ab.observation != sample_ba.observation


class TestDuplicateFeatureChannel:
    def test_duplicate_in_same_namespace_rejected(self) -> None:
        with pytest.raises(DuplicateFeatureChannelError):
            FeatureProjection(observation_channels=["a", "a"])

    def test_duplicate_across_namespaces_allowed(self) -> None:
        FeatureProjection(observation_channels=["a"], action_channels=["a"])


class TestNamespaceSeparation:
    def test_same_channel_name_in_both_namespaces_does_not_collide(self) -> None:
        step = _step(
            0, observations={"signal": _scalar(1.0)}, actions={"signal": _scalar(9.0)}
        )
        schema = resolve_feature_schema(
            [step],
            FeatureProjection(
                observation_channels=["signal"], action_channels=["signal"]
            ),
        )
        sample = project_step(
            step, step_index=0, episode_ref=_episode_ref(), schema=schema
        )
        assert sample.observation == [1.0]
        assert sample.action == [9.0]


# ── scalar/vector flattening ────────────────────────────────────────────


class TestScalarVectorFlattening:
    def test_deterministic_flatten(self) -> None:
        step = _step(
            0,
            observations={
                "state.position": _vector([1.0, 2.0, 3.0]),
                "state.velocity": _vector([4.0, 5.0, 6.0]),
            },
            actions={"steering": _scalar(0.3)},
        )
        proj = FeatureProjection(
            observation_channels=["state.position", "state.velocity"],
            action_channels=["steering"],
        )
        schema = resolve_feature_schema([step], proj)
        sample = project_step(
            step, step_index=0, episode_ref=_episode_ref(), schema=schema
        )

        assert sample.observation == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
        assert sample.action == [0.3]
        assert schema.observation_dim == 6
        assert schema.action_dim == 1


# ── missing / absent / interpolated semantics ───────────────────────────


class TestAbsentChannel:
    def test_absent_channel_raises_on_schema_resolution(self) -> None:
        step = _step(0, observations={"state.position": _scalar(1.0)}, actions={})
        with pytest.raises(FeatureAbsentError):
            resolve_feature_schema(
                [step], FeatureProjection(observation_channels=["camera.front"])
            )


class TestMissingSignal:
    def test_missing_status_raises_during_per_step_projection(self) -> None:
        resolved_step = _step(
            0, observations={"state.position": _scalar(1.0)}, actions={}
        )
        missing_step = _step(
            1_000, observations={"state.position": _missing()}, actions={}
        )
        steps = [resolved_step, missing_step]
        proj = FeatureProjection(observation_channels=["state.position"])

        schema = resolve_feature_schema(steps, proj)
        with pytest.raises(FeatureMissingError):
            project_step(
                missing_step, step_index=1, episode_ref=_episode_ref(), schema=schema
            )

    def test_missing_at_every_step_raises_during_schema_resolution(self) -> None:
        steps = [_step(0, observations={"state.position": _missing()}, actions={})]
        with pytest.raises(FeatureMissingError):
            resolve_feature_schema(
                steps, FeatureProjection(observation_channels=["state.position"])
            )


class TestResolvedAndInterpolatedAreUsable:
    def test_resolved_is_usable(self) -> None:
        step = _step(0, observations={"state.position": _scalar(1.0)}, actions={})
        schema = resolve_feature_schema(
            [step], FeatureProjection(observation_channels=["state.position"])
        )
        sample = project_step(
            step, step_index=0, episode_ref=_episode_ref(), schema=schema
        )
        assert sample.observation == [1.0]

    def test_interpolated_is_usable(self) -> None:
        step = _step(
            0, observations={"state.position": _interpolated_scalar(2.5)}, actions={}
        )
        schema = resolve_feature_schema(
            [step], FeatureProjection(observation_channels=["state.position"])
        )
        sample = project_step(
            step, step_index=0, episode_ref=_episode_ref(), schema=schema
        )
        assert sample.observation == [2.5]


class TestUnsupportedFeatureKind:
    def test_reference_kind_raises_during_schema_resolution(self) -> None:
        step = _step(0, observations={"camera.front": _reference()}, actions={})
        with pytest.raises(UnsupportedFeatureKindError):
            resolve_feature_schema(
                [step], FeatureProjection(observation_channels=["camera.front"])
            )


# ── shape consistency ────────────────────────────────────────────────────


class TestShapeConsistency:
    def test_vector_dimension_change_raises(self) -> None:
        steps = [
            _step(
                0, observations={"state.position": _vector([1.0, 2.0, 3.0])}, actions={}
            ),
            _step(
                1_000, observations={"state.position": _vector([1.0, 2.0])}, actions={}
            ),
        ]
        with pytest.raises(FeatureShapeMismatchError):
            resolve_feature_schema(
                steps, FeatureProjection(observation_channels=["state.position"])
            )

    def test_scalar_to_vector_kind_change_raises(self) -> None:
        steps = [
            _step(0, observations={"steering": _scalar(0.1)}, actions={}),
            _step(1_000, observations={"steering": _vector([0.1, 0.2])}, actions={}),
        ]
        with pytest.raises(FeatureShapeMismatchError):
            resolve_feature_schema(
                steps, FeatureProjection(observation_channels=["steering"])
            )


# ── sequence boundary ────────────────────────────────────────────────────


class TestSequenceBoundary:
    def test_exact_fit_is_valid(self) -> None:
        ref = SequenceRef(episode_ref=_episode_ref(), start_step=84, horizon=16)
        validate_sequence_bounds(ref, step_count=100)  # no raise

    def test_overflow_raises(self) -> None:
        ref = SequenceRef(episode_ref=_episode_ref(), start_step=85, horizon=16)
        with pytest.raises(SequenceBoundaryError):
            validate_sequence_bounds(ref, step_count=100)


# ── multi-step SequenceSample output ────────────────────────────────────


class TestSequenceSampleOutput:
    def test_multi_step_sequence_sample_shapes_and_ordering(self) -> None:
        steps = _multi_step_episode(4)
        ref = _episode_ref()
        proj = FeatureProjection(
            observation_channels=["state.position"], action_channels=["steering"]
        )
        schema = resolve_feature_schema(steps, proj)
        seq_ref = SequenceRef(episode_ref=ref, start_step=1, horizon=2)

        sample = project_sequence(steps, schema=schema, sequence_ref=seq_ref)

        assert sample.episode_ref == ref
        assert sample.start_step == 1
        assert sample.horizon == 2
        assert sample.timestamps_us == [1_000, 2_000]
        assert sample.observation == [[1.0], [2.0]]
        assert sample.action == [[0.5], [1.0]]
        assert (
            len(sample.timestamps_us)
            == len(sample.observation)
            == len(sample.action)
            == 2
        )

    def test_sequence_never_crosses_episode_boundary(self) -> None:
        steps = _multi_step_episode(4)
        proj = FeatureProjection(observation_channels=["state.position"])
        schema = resolve_feature_schema(steps, proj)
        seq_ref = SequenceRef(episode_ref=_episode_ref(), start_step=3, horizon=2)
        with pytest.raises(SequenceBoundaryError):
            project_sequence(steps, schema=schema, sequence_ref=seq_ref)


# ── determinism ──────────────────────────────────────────────────────────


class TestDeterminism:
    def test_same_inputs_same_projection_yield_identical_outputs(self) -> None:
        steps = _multi_step_episode(4)
        ref = _episode_ref()
        proj = FeatureProjection(
            observation_channels=["state.position"], action_channels=["steering"]
        )

        schema_1 = resolve_feature_schema(steps, proj)
        schema_2 = resolve_feature_schema(steps, proj)
        assert schema_1 == schema_2

        sample_1 = project_step(
            steps[0], step_index=0, episode_ref=ref, schema=schema_1
        )
        sample_2 = project_step(
            steps[0], step_index=0, episode_ref=ref, schema=schema_2
        )
        assert sample_1 == sample_2

        seq_ref = SequenceRef(episode_ref=ref, start_step=0, horizon=4)
        seq_1 = project_sequence(steps, schema=schema_1, sequence_ref=seq_ref)
        seq_2 = project_sequence(steps, schema=schema_2, sequence_ref=seq_ref)
        assert seq_1 == seq_2
