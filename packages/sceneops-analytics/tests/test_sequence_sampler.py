"""Tests for SequenceSampler, the deterministic fixed-horizon sequence
index over SceneOpsDataset (SceneOps V2 Request 2.7C).

Self-contained (does not import fixtures from test_learning_dataset.py),
matching that module's own style. Every test writes real Parquet through a
real LocalArtifactStore under tmp_path.
"""

from __future__ import annotations

from collections import Counter

import polars as pl
import pytest

from sceneops_analytics import (
    AnalyticsTableWriter,
    SamplerSchemaMismatchError,
    SceneOpsDataset,
    SequenceSampler,
    build_learning_episodes_table,
    build_learning_signals_table,
    build_learning_steps_table,
)
from sceneops_core.episodes.alignment import (
    ALIGNED_EPISODE_PROFILE_SEMANTICS_VERSION,
    ALIGNED_EPISODE_VALIDATION_SEMANTICS_VERSION,
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
from sceneops_core.episodes.curation import (
    CURATION_SEMANTICS_VERSION,
    CurationCandidateSummary,
    CurationDecision,
    CurationPolicy,
    EpisodeCurationManifest,
    SourceLearningExportRef,
    curation_policy_hash,
    episode_curation_id,
)
from sceneops_core.episodes.learning import (
    EpisodeRef,
    FeatureMissingError,
    FeatureProjection,
    MissingFeaturePolicy,
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


# ── fixture builders ────────────────────────────────────────────────────


def _scalar(value: float) -> AlignedSignal:
    return AlignedSignal(
        channel="c",
        policy=AssociationPolicy.NEAREST,
        status=AlignedSignalStatus.RESOLVED,
        value=AlignedValue(kind=AlignedValueKind.NUMERIC_SCALAR, scalar=value),
        source_timestamp_us=0,
        time_delta_us=0,
    )


def _vector(values: list[float]) -> AlignedSignal:
    return AlignedSignal(
        channel="c",
        policy=AssociationPolicy.NEAREST,
        status=AlignedSignalStatus.RESOLVED,
        value=AlignedValue(kind=AlignedValueKind.NUMERIC_VECTOR, vector=values),
        source_timestamp_us=0,
        time_delta_us=0,
    )


def _missing() -> AlignedSignal:
    return AlignedSignal(
        channel="c",
        policy=AssociationPolicy.NEAREST,
        status=AlignedSignalStatus.MISSING,
    )


def _scalar_steps(n: int, *, channel: str = "pos", missing_index: int | None = None):
    return [
        LearningStep(
            timestamp_us=i * 1_000,
            observations={
                channel: _missing() if i == missing_index else _scalar(float(i))
            },
            actions={},
        )
        for i in range(n)
    ]


def _vector_steps(n: int, *, channel: str = "pos"):
    return [
        LearningStep(
            timestamp_us=i * 1_000,
            observations={channel: _vector([float(i), float(i) + 0.5])},
            actions={},
        )
        for i in range(n)
    ]


def _episode(episode_id: str, steps: list[LearningStep]) -> AlignedEpisode:
    return AlignedEpisode(
        episode_id=episode_id,
        source_start_timestamp_us=0,
        source_end_timestamp_us=max(len(steps) - 1, 0) * 1_000,
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


def _ref(episode_id: str, checksum: str) -> EpisodeRef:
    return EpisodeRef(episode_id=episode_id, aligned_artifact_checksum=checksum)


async def _write_snapshot(tmp_path, artifact_store, *, entries, suffix=""):
    export_config = LearningDataExportConfig()
    export_id = learning_data_export_id(
        aligned_checksums=[c for c, _ in entries], export_config=export_config
    )
    writer = AnalyticsTableWriter(
        artifact_store=artifact_store, root_uri=str(tmp_path / f"analytics{suffix}")
    )
    builders = {
        "learning_episodes": build_learning_episodes_table,
        "learning_steps": build_learning_steps_table,
        "learning_signals": build_learning_signals_table,
    }
    table_uris: dict[str, str] = {}
    row_counts: dict[str, int] = {}
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
        row_counts[name] = df.height

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
        row_counts=row_counts,
        episode_count=len({a.aligned_episode.episode_id for _, a in entries}),
    )
    write_result = await writer.write_learning_export_manifest(
        manifest,
        dataset_id=DATASET_ID,
        dataset_version=DATASET_VERSION,
        export_id=export_id,
    )
    return manifest, write_result.checksum


async def _open_dataset(tmp_path, store, entries, *, suffix="", curation_manifest=None):
    manifest, checksum = await _write_snapshot(
        tmp_path, store, entries=entries, suffix=suffix
    )
    dataset = await SceneOpsDataset.open(
        learning_manifest=manifest,
        learning_manifest_checksum=checksum,
        artifact_store=store,
        curation_manifest=curation_manifest,
    )
    return dataset, manifest, checksum


def _curation_manifest(*, source_export_checksum, selected, all_candidates):
    selected_set = set(selected)
    decisions = [
        CurationDecision(
            episode_id=eid,
            aligned_artifact_checksum=chk,
            selected=(eid, chk) in selected_set,
        )
        for eid, chk in all_candidates
    ]
    policy = CurationPolicy()
    policy_hash = curation_policy_hash(policy)
    curation_id = episode_curation_id(
        source_export_checksum=source_export_checksum, policy_hash=policy_hash
    )
    return EpisodeCurationManifest(
        curation_id=curation_id,
        dataset_id=DATASET_ID,
        dataset_version=DATASET_VERSION,
        source_learning_export=SourceLearningExportRef(
            artifact_id="manifest-art-1", checksum=source_export_checksum
        ),
        policy=policy,
        policy_hash=policy_hash,
        curation_semantics_version=CURATION_SEMANTICS_VERSION,
        validation_semantics_version=ALIGNED_EPISODE_VALIDATION_SEMANTICS_VERSION,
        profile_semantics_version=ALIGNED_EPISODE_PROFILE_SEMANTICS_VERSION,
        candidates=CurationCandidateSummary(
            total=len(all_candidates),
            selected=len(selected),
            rejected=len(all_candidates) - len(selected),
        ),
        decisions=decisions,
        selected_aligned_artifact_checksums=sorted(c for _, c in selected),
    )


_PROJECTION = FeatureProjection(observation_channels=["pos"])


# ── window count formula ─────────────────────────────────────────────────


class TestWindowCountFormula:
    async def test_normal_short_and_zero_window_episodes(self, tmp_path):
        store = LocalArtifactStore(root_uri=str(tmp_path))
        entries = [
            ("chk-100", _artifact(_episode("ep-100", _scalar_steps(100)))),
            ("chk-30", _artifact(_episode("ep-30", _scalar_steps(30)))),
            ("chk-10", _artifact(_episode("ep-10", _scalar_steps(10)))),
        ]
        dataset, _m, _c = await _open_dataset(tmp_path, store, entries)
        sampler = await SequenceSampler.create(
            dataset, projection=_PROJECTION, horizon=16, stride=1
        )

        counts = Counter(ref.episode_ref for ref in sampler.sequence_refs())
        assert counts[_ref("ep-100", "chk-100")] == 85
        assert counts[_ref("ep-30", "chk-30")] == 15
        assert _ref("ep-10", "chk-10") not in counts  # 0 windows -- contributes nothing
        assert len(sampler) == 85 + 15

    async def test_exact_horizon_length_episode_contributes_one_window(self, tmp_path):
        store = LocalArtifactStore(root_uri=str(tmp_path))
        entries = [("chk-16", _artifact(_episode("ep-16", _scalar_steps(16))))]
        dataset, _m, _c = await _open_dataset(tmp_path, store, entries)
        sampler = await SequenceSampler.create(
            dataset, projection=_PROJECTION, horizon=16, stride=1
        )

        assert len(sampler) == 1
        ref = sampler.sequence_ref(0)
        assert ref.start_step == 0
        assert ref.horizon == 16


# ── stride ────────────────────────────────────────────────────────────────


class TestStride:
    async def test_stride_one_and_stride_greater_than_one(self, tmp_path):
        store = LocalArtifactStore(root_uri=str(tmp_path))
        entries = [("chk-10", _artifact(_episode("ep-10", _scalar_steps(10))))]
        dataset, _m, _c = await _open_dataset(tmp_path, store, entries)

        sampler_s1 = await SequenceSampler.create(
            dataset, projection=_PROJECTION, horizon=3, stride=1
        )
        starts_s1 = [ref.start_step for ref in sampler_s1.sequence_refs()]
        assert starts_s1 == [0, 1, 2, 3, 4, 5, 6, 7]  # (10-3)//1+1 = 8

        sampler_s2 = await SequenceSampler.create(
            dataset, projection=_PROJECTION, horizon=3, stride=2
        )
        starts_s2 = [ref.start_step for ref in sampler_s2.sequence_refs()]
        assert starts_s2 == [0, 2, 4, 6]  # (10-3)//2+1 = 4

    async def test_rejects_non_positive_horizon_and_stride(self, tmp_path):
        store = LocalArtifactStore(root_uri=str(tmp_path))
        entries = [("chk-10", _artifact(_episode("ep-10", _scalar_steps(10))))]
        dataset, _m, _c = await _open_dataset(tmp_path, store, entries)

        with pytest.raises(ValueError):
            await SequenceSampler.create(
                dataset, projection=_PROJECTION, horizon=0, stride=1
            )
        with pytest.raises(ValueError):
            await SequenceSampler.create(
                dataset, projection=_PROJECTION, horizon=-1, stride=1
            )
        with pytest.raises(ValueError):
            await SequenceSampler.create(
                dataset, projection=_PROJECTION, horizon=3, stride=0
            )
        with pytest.raises(ValueError):
            await SequenceSampler.create(
                dataset, projection=_PROJECTION, horizon=3, stride=-2
            )


# ── episode boundaries ───────────────────────────────────────────────────


class TestEpisodeBoundaries:
    async def test_no_sequence_ref_crosses_an_episode_boundary(self, tmp_path):
        store = LocalArtifactStore(root_uri=str(tmp_path))
        entries = [
            ("chk-100", _artifact(_episode("ep-100", _scalar_steps(100)))),
            ("chk-30", _artifact(_episode("ep-30", _scalar_steps(30)))),
        ]
        dataset, _m, _c = await _open_dataset(tmp_path, store, entries)
        sampler = await SequenceSampler.create(
            dataset, projection=_PROJECTION, horizon=16, stride=1
        )

        for ref in sampler.sequence_refs():
            step_count = dataset.get_episode(ref.episode_ref).step_count
            assert ref.start_step + ref.horizon <= step_count


# ── multiple revisions ───────────────────────────────────────────────────


class TestMultipleRevisions:
    async def test_same_episode_id_different_checksums_get_independent_ranges(
        self, tmp_path
    ):
        store = LocalArtifactStore(root_uri=str(tmp_path))
        entries = [
            ("chk-1", _artifact(_episode("ep-multi", _scalar_steps(20)))),
            ("chk-2", _artifact(_episode("ep-multi", _scalar_steps(12)))),
        ]
        dataset, _m, _c = await _open_dataset(tmp_path, store, entries)
        sampler = await SequenceSampler.create(
            dataset, projection=_PROJECTION, horizon=5, stride=1
        )

        counts = Counter(ref.episode_ref for ref in sampler.sequence_refs())
        assert counts[_ref("ep-multi", "chk-1")] == 16  # (20-5)//1+1
        assert counts[_ref("ep-multi", "chk-2")] == 8  # (12-5)//1+1
        assert len(sampler) == 24


# ── deterministic ordering ──────────────────────────────────────────────


class TestDeterministicOrdering:
    async def test_same_dataset_and_config_yield_identical_index(self, tmp_path):
        store = LocalArtifactStore(root_uri=str(tmp_path))
        entries = [
            ("chk-b", _artifact(_episode("ep-b", _scalar_steps(10)))),
            ("chk-a", _artifact(_episode("ep-a", _scalar_steps(10)))),
        ]
        dataset, _m, _c = await _open_dataset(tmp_path, store, entries)

        sampler_1 = await SequenceSampler.create(
            dataset, projection=_PROJECTION, horizon=3, stride=1
        )
        sampler_2 = await SequenceSampler.create(
            dataset, projection=_PROJECTION, horizon=3, stride=1
        )

        refs_1 = list(sampler_1.sequence_refs())
        refs_2 = list(sampler_2.sequence_refs())
        assert refs_1 == refs_2

        # Canonical order: dataset.episodes() order, then increasing start_step.
        expected_episode_order = dataset.episodes()
        observed_episode_order = list(dict.fromkeys(ref.episode_ref for ref in refs_1))
        assert observed_episode_order == expected_episode_order
        for episode_ref in observed_episode_order:
            starts = [
                ref.start_step for ref in refs_1 if ref.episode_ref == episode_ref
            ]
            assert starts == sorted(starts)


# ── curation ─────────────────────────────────────────────────────────────


class TestCurationInteraction:
    async def test_curation_hidden_episode_contributes_zero_windows(self, tmp_path):
        store = LocalArtifactStore(root_uri=str(tmp_path))
        entries = [
            ("chk-visible", _artifact(_episode("ep-visible", _scalar_steps(20)))),
            ("chk-hidden", _artifact(_episode("ep-hidden", _scalar_steps(20)))),
        ]
        manifest, checksum = await _write_snapshot(tmp_path, store, entries=entries)
        curation = _curation_manifest(
            source_export_checksum=checksum,
            selected=[("ep-visible", "chk-visible")],
            all_candidates=[("ep-visible", "chk-visible"), ("ep-hidden", "chk-hidden")],
        )
        dataset = await SceneOpsDataset.open(
            learning_manifest=manifest,
            learning_manifest_checksum=checksum,
            artifact_store=store,
            curation_manifest=curation,
        )
        sampler = await SequenceSampler.create(
            dataset, projection=_PROJECTION, horizon=5, stride=1
        )

        counts = Counter(ref.episode_ref for ref in sampler.sequence_refs())
        assert _ref("ep-hidden", "chk-hidden") not in counts
        assert counts[_ref("ep-visible", "chk-visible")] == 16
        assert len(sampler) == 16


# ── schema compatibility ─────────────────────────────────────────────────


class TestSchemaCompatibility:
    async def test_compatible_schemas_across_episodes_succeed(self, tmp_path):
        store = LocalArtifactStore(root_uri=str(tmp_path))
        entries = [
            ("chk-1", _artifact(_episode("ep-1", _scalar_steps(20)))),
            ("chk-2", _artifact(_episode("ep-2", _scalar_steps(20)))),
        ]
        dataset, _m, _c = await _open_dataset(tmp_path, store, entries)
        sampler = await SequenceSampler.create(
            dataset, projection=_PROJECTION, horizon=5, stride=1
        )

        assert sampler.feature_schema is not None
        assert sampler.feature_schema.observation_dim == 1

    async def test_incompatible_schemas_across_episodes_raise(self, tmp_path):
        store = LocalArtifactStore(root_uri=str(tmp_path))
        entries = [
            ("chk-scalar", _artifact(_episode("ep-scalar", _scalar_steps(20)))),
            ("chk-vector", _artifact(_episode("ep-vector", _vector_steps(20)))),
        ]
        dataset, _m, _c = await _open_dataset(tmp_path, store, entries)

        with pytest.raises(SamplerSchemaMismatchError):
            await SequenceSampler.create(
                dataset, projection=_PROJECTION, horizon=5, stride=1
            )

    async def test_short_episode_schema_incompatibility_is_irrelevant(self, tmp_path):
        """An Episode too short to ever contribute a window is never
        schema-checked -- its schema (even if incompatible) must not block
        sampler construction."""
        store = LocalArtifactStore(root_uri=str(tmp_path))
        entries = [
            ("chk-scalar", _artifact(_episode("ep-scalar", _scalar_steps(20)))),
            (
                "chk-vector-short",
                _artifact(_episode("ep-vector-short", _vector_steps(3))),
            ),
        ]
        dataset, _m, _c = await _open_dataset(tmp_path, store, entries)

        sampler = await SequenceSampler.create(
            dataset, projection=_PROJECTION, horizon=5, stride=1
        )
        assert _ref("ep-vector-short", "chk-vector-short") not in {
            ref.episode_ref for ref in sampler.sequence_refs()
        }


# ── delegation ───────────────────────────────────────────────────────────


class TestDelegation:
    async def test_get_matches_direct_dataset_get_window(self, tmp_path):
        store = LocalArtifactStore(root_uri=str(tmp_path))
        entries = [("chk-20", _artifact(_episode("ep-20", _scalar_steps(20))))]
        dataset, _m, _c = await _open_dataset(tmp_path, store, entries)
        sampler = await SequenceSampler.create(
            dataset, projection=_PROJECTION, horizon=5, stride=1
        )

        index = 4
        via_sampler = await sampler.get(index)
        ref = sampler.sequence_ref(index)
        via_dataset = await dataset.get_window(
            ref.episode_ref,
            ref.start_step,
            ref.horizon,
            _PROJECTION,
            missing_policy=MissingFeaturePolicy.ERROR,
        )

        assert via_sampler == via_dataset


# ── missing/absent propagation ──────────────────────────────────────────


class TestMissingPropagation:
    async def test_geometrically_valid_window_can_still_fail_to_project(self, tmp_path):
        store = LocalArtifactStore(root_uri=str(tmp_path))
        entries = [
            (
                "chk-missing",
                _artifact(_episode("ep-missing", _scalar_steps(5, missing_index=1))),
            )
        ]
        dataset, _m, _c = await _open_dataset(tmp_path, store, entries)
        sampler = await SequenceSampler.create(
            dataset, projection=_PROJECTION, horizon=3, stride=1
        )
        # windows: start=0 -> steps[0,1,2] (covers missing index 1)
        #          start=1 -> steps[1,2,3] (covers missing index 1)
        #          start=2 -> steps[2,3,4] (does not cover it)
        assert len(sampler) == 3

        with pytest.raises(FeatureMissingError):
            await sampler.get(0)
        with pytest.raises(FeatureMissingError):
            await sampler.get(1)

        sample = await sampler.get(2)
        assert sample.observation == [[2.0], [3.0], [4.0]]


# ── lazy construction ────────────────────────────────────────────────────


class TestLazyConstruction:
    async def test_create_never_calls_get_window(self, tmp_path):
        store = LocalArtifactStore(root_uri=str(tmp_path))
        entries = [
            ("chk-100", _artifact(_episode("ep-100", _scalar_steps(100)))),
            ("chk-30", _artifact(_episode("ep-30", _scalar_steps(30)))),
        ]
        dataset, _m, _c = await _open_dataset(tmp_path, store, entries)

        calls = []
        original_get_window = dataset.get_window

        async def _counting_get_window(*args, **kwargs):
            calls.append((args, kwargs))
            return await original_get_window(*args, **kwargs)

        dataset.get_window = _counting_get_window

        sampler = await SequenceSampler.create(
            dataset, projection=_PROJECTION, horizon=16, stride=1
        )
        assert calls == []  # construction never materializes a window

        await sampler.get(0)
        assert len(calls) == 1  # only on-demand access does

    async def test_short_episode_schema_is_never_resolved(self, tmp_path):
        """A poisoned invalid value_kind on a too-short Episode's only
        channel must never surface -- proving SequenceSampler.create()
        skips schema resolution entirely for Episodes with zero windows."""
        inner = LocalArtifactStore(root_uri=str(tmp_path))

        entries = [
            ("chk-long", _artifact(_episode("ep-long", _scalar_steps(20)))),
            ("chk-short", _artifact(_episode("ep-short", _scalar_steps(5)))),
        ]
        export_config = LearningDataExportConfig()
        export_id = learning_data_export_id(
            aligned_checksums=[c for c, _ in entries], export_config=export_config
        )
        signals_df = build_learning_signals_table(
            dataset_id=DATASET_ID,
            dataset_version=DATASET_VERSION,
            export_id=export_id,
            entries=entries,
        )
        poisoned = signals_df.with_columns(
            pl.when(pl.col("aligned_artifact_checksum") == "chk-short")
            .then(pl.lit("not-a-real-kind"))
            .otherwise(pl.col("value_kind"))
            .alias("value_kind")
        )

        writer = AnalyticsTableWriter(
            artifact_store=inner, root_uri=str(tmp_path / "analytics-lazy")
        )
        episodes_df = build_learning_episodes_table(
            dataset_id=DATASET_ID,
            dataset_version=DATASET_VERSION,
            export_id=export_id,
            entries=entries,
        )
        steps_df = build_learning_steps_table(
            dataset_id=DATASET_ID,
            dataset_version=DATASET_VERSION,
            export_id=export_id,
            entries=entries,
        )
        table_uris = {}
        for name, df in (
            ("learning_episodes", episodes_df),
            ("learning_steps", steps_df),
            ("learning_signals", poisoned),
        ):
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
            episode_count=2,
        )
        write_result = await writer.write_learning_export_manifest(
            manifest,
            dataset_id=DATASET_ID,
            dataset_version=DATASET_VERSION,
            export_id=export_id,
        )
        dataset = await SceneOpsDataset.open(
            learning_manifest=manifest,
            learning_manifest_checksum=write_result.checksum,
            artifact_store=inner,
        )

        # horizon=16 > 5 (ep-short's step_count) -- ep-short contributes 0
        # windows and must never have its schema resolved.
        sampler = await SequenceSampler.create(
            dataset, projection=_PROJECTION, horizon=16, stride=1
        )
        assert len(sampler) == (20 - 16) // 1 + 1

        # Confirm the poison is real: an explicit resolve on ep-short does fail.
        with pytest.raises(ValueError):
            await dataset.resolve_feature_schema(
                _ref("ep-short", "chk-short"), _PROJECTION
            )
