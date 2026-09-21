"""Tests for sceneops_analytics.learning_dataset.numpy_adapter (SceneOps V2
Request 2.7D) -- the required, torch-free consumer conversion layer.
torch_adapter's tests live separately in test_torch_adapter.py (skipped via
pytest.importorskip if the ``torch`` extra isn't installed) so this module
never needs torch to run.

Self-contained fixtures (does not import from the other learning_dataset
test modules). Every dataset-backed test writes real Parquet through a real
LocalArtifactStore under tmp_path.
"""

from __future__ import annotations

import asyncio

import numpy as np

from sceneops_analytics import (
    AnalyticsTableWriter,
    NumPySequenceSample,
    SceneOpsDataset,
    SequenceSampler,
    build_learning_episodes_table,
    build_learning_signals_table,
    build_learning_steps_table,
    materialize_sequences,
    to_numpy,
)
from sceneops_core.episodes.alignment import (
    ALIGNMENT_SEMANTICS_VERSION,
    AlignedEpisodeArtifact,
    AlignedSignal,
    AlignedSignalStatus,
    AlignedValue,
    AlignedValueKind,
    AssociationPolicy,
    EpisodeSourceRevision,
    TemporalAlignmentConfig,
)
from sceneops_core.episodes.alignment.schemas import AlignedEpisode, LearningStep
from sceneops_core.episodes.learning import (
    EpisodeRef,
    FeatureProjection,
    SequenceSample,
)
from sceneops_core.episodes.learning_export import (
    AlignedArtifactRevision,
    LearningDataExportConfig,
    LearningDataExportManifest,
    learning_data_export_id,
)
from sceneops_core.episodes.schemas.enums import EpisodeOutcome
from sceneops_storage import LocalArtifactStore

DATASET_ID = "ds1"
DATASET_VERSION = "v1"
_CONFIG = TemporalAlignmentConfig(target_frequency_hz=10.0)
_PROJECTION = FeatureProjection(
    observation_channels=["state.position", "state.velocity"],
    action_channels=["steering"],
)


# ── fixture builders ────────────────────────────────────────────────────


def _vector(values: list[float]) -> AlignedSignal:
    return AlignedSignal(
        channel="c",
        policy=AssociationPolicy.NEAREST,
        status=AlignedSignalStatus.RESOLVED,
        value=AlignedValue(kind=AlignedValueKind.NUMERIC_VECTOR, vector=values),
        source_timestamp_us=0,
        time_delta_us=0,
    )


def _scalar(value: float) -> AlignedSignal:
    return AlignedSignal(
        channel="c",
        policy=AssociationPolicy.NEAREST,
        status=AlignedSignalStatus.RESOLVED,
        value=AlignedValue(kind=AlignedValueKind.NUMERIC_SCALAR, scalar=value),
        source_timestamp_us=0,
        time_delta_us=0,
    )


def _steps(n: int) -> list[LearningStep]:
    return [
        LearningStep(
            timestamp_us=i * 1_000,
            observations={
                "state.position": _vector([float(i), float(i) + 0.5]),
                "state.velocity": _scalar(float(i) * 0.25),
            },
            actions={"steering": _scalar(float(i) * 0.1)},
        )
        for i in range(n)
    ]


def _episode(episode_id: str, steps: list[LearningStep]) -> AlignedEpisode:
    return AlignedEpisode(
        episode_id=episode_id,
        source_start_timestamp_us=0,
        source_end_timestamp_us=(len(steps) - 1) * 1_000,
        source_clock="mcap_log_time",
        alignment_semantics_version=ALIGNMENT_SEMANTICS_VERSION,
        alignment_config=_CONFIG,
        target_frequency_hz=10.0,
        achieved_frequency_hz=10.0,
        dt_us=1_000,
        step_count=len(steps),
        duplicate_discarded_count=0,
        task="pick",
        outcome=EpisodeOutcome.SUCCESS,
        steps=steps,
    )


def _artifact(episode: AlignedEpisode) -> AlignedEpisodeArtifact:
    return AlignedEpisodeArtifact(
        source_revision=EpisodeSourceRevision(
            episode_id=episode.episode_id,
            episode_manifest_uri=f"mem://episodes/{episode.episode_id}.json",
            source_artifact_id="src-1",
            source_manifest_sha256="s" * 64,
        ),
        aligned_episode=episode,
    )


async def _write_snapshot(tmp_path, artifact_store, *, entries):
    export_config = LearningDataExportConfig()
    export_id = learning_data_export_id(
        aligned_checksums=[c for c, _ in entries], export_config=export_config
    )
    writer = AnalyticsTableWriter(
        artifact_store=artifact_store, root_uri=str(tmp_path / "analytics")
    )
    builders = {
        "learning_episodes": build_learning_episodes_table,
        "learning_steps": build_learning_steps_table,
        "learning_signals": build_learning_signals_table,
    }
    table_uris: dict[str, str] = {}
    for name, builder in builders.items():
        df = builder(
            dataset_id=DATASET_ID,
            dataset_version=DATASET_VERSION,
            export_id=export_id,
            entries=entries,
        )
        result = await writer.write_learning_table(
            name,
            df,
            dataset_id=DATASET_ID,
            dataset_version=DATASET_VERSION,
            export_id=export_id,
        )
        table_uris[name] = result.uri

    manifest = LearningDataExportManifest(
        export_id=export_id,
        dataset_id=DATASET_ID,
        dataset_version=DATASET_VERSION,
        inputs=[
            AlignedArtifactRevision(
                episode_id=artifact.aligned_episode.episode_id,
                aligned_artifact_id=f"art-{checksum}",
                aligned_artifact_checksum=checksum,
            )
            for checksum, artifact in entries
        ],
        export_config=export_config,
        table_uris=table_uris,
        episode_count=len({a.aligned_episode.episode_id for _, a in entries}),
    )
    write_result = await writer.write_learning_export_manifest(
        manifest,
        dataset_id=DATASET_ID,
        dataset_version=DATASET_VERSION,
        export_id=export_id,
    )
    return manifest, write_result.checksum


async def _open_sampler(tmp_path, store, *, n_steps=10, horizon=4, stride=1):
    entries = [("chk-a", _artifact(_episode("ep-a", _steps(n_steps))))]
    manifest, checksum = await _write_snapshot(tmp_path, store, entries=entries)
    dataset = await SceneOpsDataset.open(
        learning_manifest=manifest,
        learning_manifest_checksum=checksum,
        artifact_store=store,
    )
    sampler = await SequenceSampler.create(
        dataset, projection=_PROJECTION, horizon=horizon, stride=stride
    )
    return dataset, sampler


# ── NumPy conversion ─────────────────────────────────────────────────────


class TestToNumpy:
    async def test_shapes_values_dtypes_and_metadata(self, tmp_path):
        store = LocalArtifactStore(root_uri=str(tmp_path))
        _dataset, sampler = await _open_sampler(tmp_path, store, n_steps=10, horizon=4)

        native = await sampler.get(0)
        converted = to_numpy(native)

        assert converted.episode_ref == EpisodeRef(
            episode_id="ep-a", aligned_artifact_checksum="chk-a"
        )
        assert converted.start_step == 0
        assert converted.horizon == 4

        assert converted.timestamps_us.dtype == np.int64
        assert converted.observation.dtype == np.float32
        assert converted.action.dtype == np.float32

        assert converted.timestamps_us.shape == (4,)
        assert converted.observation.shape == (4, 3)  # position[2] + velocity[1]
        assert converted.action.shape == (4, 1)

        np.testing.assert_array_equal(
            converted.timestamps_us, np.array([0, 1000, 2000, 3000])
        )
        np.testing.assert_allclose(
            converted.observation,
            np.array(
                [[0.0, 0.5, 0.0], [1.0, 1.5, 0.25], [2.0, 2.5, 0.5], [3.0, 3.5, 0.75]]
            ),
        )
        np.testing.assert_allclose(
            converted.action, np.array([[0.0], [0.1], [0.2], [0.3]])
        )

    async def test_custom_dtype(self, tmp_path):
        store = LocalArtifactStore(root_uri=str(tmp_path))
        _dataset, sampler = await _open_sampler(tmp_path, store)
        native = await sampler.get(0)

        converted = to_numpy(native, dtype=np.float64)
        assert converted.observation.dtype == np.float64
        assert converted.action.dtype == np.float64
        assert (
            converted.timestamps_us.dtype == np.int64
        )  # always int64, regardless of dtype

    async def test_does_not_mutate_original_sequence_sample(self, tmp_path):
        store = LocalArtifactStore(root_uri=str(tmp_path))
        _dataset, sampler = await _open_sampler(tmp_path, store)
        native = await sampler.get(0)
        original_observation = [list(row) for row in native.observation]
        original_action = [list(row) for row in native.action]
        original_timestamps = list(native.timestamps_us)

        converted = to_numpy(native)
        converted.observation[0, 0] = 999.0
        converted.action[0, 0] = 999.0
        converted.timestamps_us[0] = 999

        assert isinstance(native, SequenceSample)
        assert [list(row) for row in native.observation] == original_observation
        assert [list(row) for row in native.action] == original_action
        assert list(native.timestamps_us) == original_timestamps

    async def test_feature_order_is_preserved(self, tmp_path):
        store = LocalArtifactStore(root_uri=str(tmp_path))
        entries = [("chk-a", _artifact(_episode("ep-a", _steps(5))))]
        manifest, checksum = await _write_snapshot(tmp_path, store, entries=entries)
        dataset = await SceneOpsDataset.open(
            learning_manifest=manifest,
            learning_manifest_checksum=checksum,
            artifact_store=store,
        )

        proj_forward = FeatureProjection(
            observation_channels=["state.position", "state.velocity"]
        )
        proj_reverse = FeatureProjection(
            observation_channels=["state.velocity", "state.position"]
        )

        sampler_forward = await SequenceSampler.create(
            dataset, projection=proj_forward, horizon=3, stride=1
        )
        sampler_reverse = await SequenceSampler.create(
            dataset, projection=proj_reverse, horizon=3, stride=1
        )

        forward = to_numpy(await sampler_forward.get(0))
        reverse = to_numpy(await sampler_reverse.get(0))

        # forward: [pos_x, pos_y, vel] per row: [0.0, 0.5, 0.0]
        # reverse: [vel, pos_x, pos_y] per row: [0.0, 0.0, 0.5]
        np.testing.assert_allclose(forward.observation[0], [0.0, 0.5, 0.0])
        np.testing.assert_allclose(reverse.observation[0], [0.0, 0.0, 0.5])


class TestMultipleSamples:
    async def test_two_indices_produce_independent_correct_samples(self, tmp_path):
        store = LocalArtifactStore(root_uri=str(tmp_path))
        _dataset, sampler = await _open_sampler(tmp_path, store, n_steps=10, horizon=4)

        sample_0 = to_numpy(await sampler.get(0))
        sample_2 = to_numpy(await sampler.get(2))

        assert sample_0.start_step == 0
        assert sample_2.start_step == 2
        np.testing.assert_array_equal(sample_0.timestamps_us, [0, 1000, 2000, 3000])
        np.testing.assert_array_equal(sample_2.timestamps_us, [2000, 3000, 4000, 5000])
        assert not np.array_equal(sample_0.observation, sample_2.observation)


class TestDeterminism:
    async def test_same_sequence_sample_yields_same_numpy_result(self, tmp_path):
        store = LocalArtifactStore(root_uri=str(tmp_path))
        _dataset, sampler = await _open_sampler(tmp_path, store)
        native = await sampler.get(1)

        a = to_numpy(native)
        b = to_numpy(native)

        np.testing.assert_array_equal(a.timestamps_us, b.timestamps_us)
        np.testing.assert_array_equal(a.observation, b.observation)
        np.testing.assert_array_equal(a.action, b.action)
        assert a.episode_ref == b.episode_ref
        assert a.start_step == b.start_step
        assert a.horizon == b.horizon


class TestMaterializeSequences:
    async def test_materializes_all_windows_in_canonical_order(self, tmp_path):
        store = LocalArtifactStore(root_uri=str(tmp_path))
        _dataset, sampler = await _open_sampler(tmp_path, store, n_steps=10, horizon=4)

        materialized = await materialize_sequences(sampler)

        assert len(materialized) == len(sampler)
        assert all(isinstance(s, NumPySequenceSample) for s in materialized)
        assert [s.start_step for s in materialized] == list(range(len(sampler)))

    async def test_materializes_explicit_indices_in_given_order(self, tmp_path):
        store = LocalArtifactStore(root_uri=str(tmp_path))
        _dataset, sampler = await _open_sampler(tmp_path, store, n_steps=10, horizon=4)

        materialized = await materialize_sequences(sampler, indices=[3, 0, 1])

        assert [s.start_step for s in materialized] == [
            3,
            0,
            1,
        ]  # caller order preserved


# ── async safety ─────────────────────────────────────────────────────────


class TestAsyncSafety:
    def test_materialize_sequences_requires_await(self, tmp_path):
        """materialize_sequences is a coroutine function -- proving it is
        never silently run synchronously/implicitly."""
        assert asyncio.iscoroutinefunction(materialize_sequences)

    def test_to_numpy_is_plain_sync_and_needs_no_event_loop(self, tmp_path):
        """to_numpy operates purely on an in-memory SequenceSample -- no
        event loop involved at all."""
        sample = SequenceSample(
            episode_ref=EpisodeRef(episode_id="e", aligned_artifact_checksum="c" * 8),
            start_step=0,
            horizon=2,
            timestamps_us=[0, 1000],
            observation=[[1.0], [2.0]],
            action=[[0.1], [0.2]],
        )
        assert not asyncio.iscoroutinefunction(to_numpy)
        result = to_numpy(sample)
        assert result.observation.shape == (2, 1)
