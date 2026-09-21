"""Tests for SceneOpsDataset, the read-only Parquet-backed native dataset
access layer (SceneOps V2 Request 2.7B).

Every test writes real Parquet files through a real LocalArtifactStore
under tmp_path (not mocked) -- proving the full
LearningDataExportManifest(+EpisodeCurationManifest) -> Parquet ->
SceneOpsDataset -> sceneops_core.episodes.learning (Request 2.7A) path
against genuine bytes on disk, mirroring the real
ALIGN -> EXPORT_LEARNING_DATA -> (CURATE) -> SceneOpsDataset.open() pipeline
without any worker Job/API machinery.
"""

from __future__ import annotations

from typing import Any

import polars as pl
import pytest

from sceneops_analytics import (
    AnalyticsTableWriter,
    CurationManifestMismatchError,
    DatasetManifestMismatchError,
    EpisodeNotFoundError,
    LearningDataIntegrityError,
    LearningTableMissingError,
    SceneOpsDataset,
    StepOutOfRangeError,
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
    FeatureAbsentError,
    FeatureMissingError,
    FeatureProjection,
    FeatureShapeMismatchError,
    SequenceBoundaryError,
)
from sceneops_core.episodes.learning_export import (
    AlignedArtifactRevision,
    LearningDataExportConfig,
    LearningDataExportManifest,
    learning_data_export_id,
)
from sceneops_core.episodes.schemas.enums import EpisodeOutcome
from sceneops_core.sensors import SensorModality
from sceneops_storage import LocalArtifactStore

DATASET_ID = "ds1"
DATASET_VERSION = "v1"

_CONFIG = TemporalAlignmentConfig(target_frequency_hz=10.0)


# ── signal builders ─────────────────────────────────────────────────────


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
        policy=AssociationPolicy.LINEAR_INTERPOLATION,
        status=AlignedSignalStatus.MISSING,
    )


def _interpolated(value: float) -> AlignedSignal:
    return AlignedSignal(
        channel="c",
        policy=AssociationPolicy.LINEAR_INTERPOLATION,
        status=AlignedSignalStatus.INTERPOLATED,
        value=AlignedValue(kind=AlignedValueKind.NUMERIC_SCALAR, scalar=value),
        source_before_timestamp_us=0,
        source_after_timestamp_us=200_000,
        interpolation_ratio=0.5,
    )


def _reference(uri: str) -> AlignedSignal:
    return AlignedSignal(
        channel="c",
        policy=AssociationPolicy.NEAREST,
        status=AlignedSignalStatus.RESOLVED,
        value=AlignedValue(
            kind=AlignedValueKind.REFERENCE,
            reference_modality=SensorModality.CAMERA,
            reference_uri=uri,
            reference_channel="camera.front",  # never persisted -- see reconstruct.py
        ),
        source_timestamp_us=0,
        time_delta_us=0,
    )


def _episode(
    episode_id: str, steps: list[LearningStep], **overrides: Any
) -> AlignedEpisode:
    defaults = dict(
        episode_id=episode_id,
        source_start_timestamp_us=0,
        source_end_timestamp_us=(len(steps) - 1) * 100_000,
        source_clock="mcap_log_time",
        alignment_semantics_version=ALIGNMENT_SEMANTICS_VERSION,
        alignment_config=_CONFIG,
        target_frequency_hz=10.0,
        achieved_frequency_hz=10.0,
        dt_us=100_000,
        step_count=len(steps),
        duplicate_discarded_count=0,
        task="pick",
        outcome=EpisodeOutcome.SUCCESS,
        steps=steps,
    )
    defaults.update(overrides)
    return AlignedEpisode(**defaults)


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


# ── fixture episodes ────────────────────────────────────────────────────
#
# ep-1 / chk-ep1-a: main happy-path fixture (4 steps).
#   pos       (obs, scalar)   0.0, 1.0, 2.0, 3.0            -- always resolved
#   imu       (obs, scalar)   MISSING, INTERPOLATED(1.5), 2.0, 3.0
#   accel     (obs, vector2)  [0.1,0.2] [0.3,0.4] [0.5,0.6] [0.7,0.8]
#   steering  (act, scalar)   0.0, 0.5, 1.0, 1.5
#   shared    (obs AND act, scalar) 7.0 / 8.0 at every step -- namespace separation
#   "camera.front" channel is never declared at all -- ABSENT probe target.
#
# ep-1 / chk-ep1-b: SAME episode_id, DIFFERENT checksum, DIFFERENT shape
# (2 steps) -- proves EpisodeRef distinctness at the dataset level.
#
# ep-2 / chk-ep2: independent episode (2 steps) carrying a REFERENCE-kind
# channel, for reference-value reconstruction.


def _fixture_ep1_a_steps() -> list[LearningStep]:
    imu = [_missing(), _interpolated(1.5), _scalar(2.0), _scalar(3.0)]
    pos = [_scalar(float(i)) for i in range(4)]
    accel = [_vector(v) for v in ([0.1, 0.2], [0.3, 0.4], [0.5, 0.6], [0.7, 0.8])]
    steering = [_scalar(float(i) * 0.5) for i in range(4)]
    shared_obs = [_scalar(7.0) for _ in range(4)]
    shared_act = [_scalar(8.0) for _ in range(4)]
    return [
        LearningStep(
            timestamp_us=i * 100_000,
            observations={
                "pos": pos[i],
                "imu": imu[i],
                "accel": accel[i],
                "shared": shared_obs[i],
            },
            actions={"steering": steering[i], "shared": shared_act[i]},
        )
        for i in range(4)
    ]


def _fixture_ep1_b_steps() -> list[LearningStep]:
    return [
        LearningStep(
            timestamp_us=i * 100_000,
            observations={"pos": _scalar(10.0 + i)},
            actions={"steering": _scalar(0.0)},
        )
        for i in range(2)
    ]


def _fixture_ep2_steps() -> list[LearningStep]:
    return [
        LearningStep(
            timestamp_us=i * 100_000,
            observations={
                "pos": _scalar(100.0 + i),
                "camera.front": _reference(f"mem://frames/ep2/{i}.png"),
            },
            actions={"steering": _scalar(float(i))},
        )
        for i in range(2)
    ]


def _fixture_broken_shape_steps() -> list[LearningStep]:
    """Deliberately shape-inconsistent -- 'steering' is a scalar at step 0
    but a vector at step 1. align_episode() would never produce this; here
    it is hand-constructed to exercise FeatureShapeMismatchError through
    the dataset layer."""
    return [
        LearningStep(
            timestamp_us=0, observations={}, actions={"steering": _scalar(0.1)}
        ),
        LearningStep(
            timestamp_us=100_000,
            observations={},
            actions={"steering": _vector([0.1, 0.2])},
        ),
    ]


# ── manifest/table writing helpers ──────────────────────────────────────


async def _write_snapshot(
    tmp_path, artifact_store, *, entries: list[tuple[str, AlignedEpisodeArtifact]]
) -> tuple[LearningDataExportManifest, str]:
    """entries: (aligned_artifact_checksum, AlignedEpisodeArtifact) pairs.
    Mirrors export_learning_data.py's job handler (minus ArtifactRecord/DB
    bookkeeping): builds the three columnar tables, writes them as real
    Parquet through AnalyticsTableWriter, then writes+checksums the
    LearningDataExportManifest itself the same way."""
    export_config = LearningDataExportConfig()
    export_id = learning_data_export_id(
        aligned_checksums=[checksum for checksum, _ in entries],
        export_config=export_config,
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
    table_checksums: dict[str, str] = {}
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
        table_checksums[name] = result.checksum
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
        table_checksums=table_checksums,
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


def _main_entries() -> list[tuple[str, AlignedEpisodeArtifact]]:
    return [
        ("chk-ep1-a", _artifact(_episode("ep-1", _fixture_ep1_a_steps()))),
        ("chk-ep1-b", _artifact(_episode("ep-1", _fixture_ep1_b_steps()))),
        ("chk-ep2", _artifact(_episode("ep-2", _fixture_ep2_steps()))),
    ]


async def _open_main_dataset(tmp_path, artifact_store, *, curation_manifest=None):
    manifest, checksum = await _write_snapshot(
        tmp_path, artifact_store, entries=_main_entries()
    )
    dataset = await SceneOpsDataset.open(
        learning_manifest=manifest,
        learning_manifest_checksum=checksum,
        artifact_store=artifact_store,
        curation_manifest=curation_manifest,
    )
    return dataset, manifest, checksum


def _ref(episode_id: str, checksum: str) -> EpisodeRef:
    return EpisodeRef(episode_id=episode_id, aligned_artifact_checksum=checksum)


def _curation_manifest(
    *,
    source_export_checksum: str,
    selected: list[tuple[str, str]],
    all_candidates: list[tuple[str, str]],
    dataset_id: str = DATASET_ID,
    dataset_version: str = DATASET_VERSION,
) -> EpisodeCurationManifest:
    selected_set = set(selected)
    decisions = [
        CurationDecision(
            episode_id=episode_id,
            aligned_artifact_checksum=checksum,
            selected=(episode_id, checksum) in selected_set,
        )
        for episode_id, checksum in all_candidates
    ]
    policy = CurationPolicy()
    policy_hash = curation_policy_hash(policy)
    curation_id = episode_curation_id(
        source_export_checksum=source_export_checksum, policy_hash=policy_hash
    )
    return EpisodeCurationManifest(
        curation_id=curation_id,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
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


class _CountingArtifactStore:
    """Wraps a real ArtifactStore, counting read_bytes calls by URI --
    used to prove SceneOpsDataset only fetches each table at most once and
    never touches tables it doesn't need."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.read_bytes_calls: list[str] = []

    def join_uri(self, root, *parts):
        return self._inner.join_uri(root, *parts)

    async def exists(self, uri):
        return await self._inner.exists(uri)

    async def read_json(self, uri):
        return await self._inner.read_json(uri)

    async def write_json(self, uri, payload):
        return await self._inner.write_json(uri, payload)

    async def read_bytes(self, uri):
        self.read_bytes_calls.append(uri)
        return await self._inner.read_bytes(uri)

    async def write_bytes(self, uri, data):
        return await self._inner.write_bytes(uri, data)

    async def list_json(self, uri):
        return await self._inner.list_json(uri)

    async def delete_prefix(self, uri):
        return await self._inner.delete_prefix(uri)

    def public_url(self, uri):
        return self._inner.public_url(uri)


# ── dataset selection ────────────────────────────────────────────────────


class TestDatasetSelection:
    async def test_open_without_curation_exposes_all_declared_revisions(self, tmp_path):
        store = LocalArtifactStore(root_uri=str(tmp_path))
        dataset, _manifest, _checksum = await _open_main_dataset(tmp_path, store)

        assert len(dataset) == 3
        assert dataset.episodes() == sorted(
            [
                _ref("ep-1", "chk-ep1-a"),
                _ref("ep-1", "chk-ep1-b"),
                _ref("ep-2", "chk-ep2"),
            ],
            key=lambda r: (r.episode_id, r.aligned_artifact_checksum),
        )

    async def test_same_episode_id_different_checksums_remain_distinct(self, tmp_path):
        store = LocalArtifactStore(root_uri=str(tmp_path))
        dataset, _m, _c = await _open_main_dataset(tmp_path, store)

        ref_a = _ref("ep-1", "chk-ep1-a")
        ref_b = _ref("ep-1", "chk-ep1-b")
        assert ref_a != ref_b
        assert ref_a in dataset and ref_b in dataset
        assert dataset.get_episode(ref_a).step_count == 4
        assert dataset.get_episode(ref_b).step_count == 2

    async def test_open_with_curation_narrows_to_selected_revisions(self, tmp_path):
        store = LocalArtifactStore(root_uri=str(tmp_path))
        manifest, checksum = await _write_snapshot(
            tmp_path, store, entries=_main_entries()
        )
        curation = _curation_manifest(
            source_export_checksum=checksum,
            selected=[("ep-1", "chk-ep1-a"), ("ep-2", "chk-ep2")],
            all_candidates=[
                ("ep-1", "chk-ep1-a"),
                ("ep-1", "chk-ep1-b"),
                ("ep-2", "chk-ep2"),
            ],
        )
        dataset = await SceneOpsDataset.open(
            learning_manifest=manifest,
            learning_manifest_checksum=checksum,
            artifact_store=store,
            curation_manifest=curation,
        )

        assert len(dataset) == 2
        assert _ref("ep-1", "chk-ep1-a") in dataset
        assert _ref("ep-2", "chk-ep2") in dataset
        assert _ref("ep-1", "chk-ep1-b") not in dataset

    async def test_mismatched_curation_checksum_rejected(self, tmp_path):
        store = LocalArtifactStore(root_uri=str(tmp_path))
        manifest, _checksum = await _write_snapshot(
            tmp_path, store, entries=_main_entries()
        )
        curation = _curation_manifest(
            source_export_checksum="0" * 64,  # wrong checksum
            selected=[("ep-1", "chk-ep1-a")],
            all_candidates=[("ep-1", "chk-ep1-a")],
        )
        with pytest.raises(CurationManifestMismatchError):
            await SceneOpsDataset.open(
                learning_manifest=manifest,
                learning_manifest_checksum="1" * 64,
                artifact_store=store,
                curation_manifest=curation,
            )

    async def test_mismatched_curation_dataset_rejected(self, tmp_path):
        store = LocalArtifactStore(root_uri=str(tmp_path))
        manifest, checksum = await _write_snapshot(
            tmp_path, store, entries=_main_entries()
        )
        curation = _curation_manifest(
            source_export_checksum=checksum,
            selected=[("ep-1", "chk-ep1-a")],
            all_candidates=[("ep-1", "chk-ep1-a")],
            dataset_id="some-other-dataset",
        )
        with pytest.raises(DatasetManifestMismatchError):
            await SceneOpsDataset.open(
                learning_manifest=manifest,
                learning_manifest_checksum=checksum,
                artifact_store=store,
                curation_manifest=curation,
            )

    async def test_selected_checksum_not_declared_by_export_rejected(self, tmp_path):
        store = LocalArtifactStore(root_uri=str(tmp_path))
        manifest, checksum = await _write_snapshot(
            tmp_path, store, entries=_main_entries()
        )
        curation = _curation_manifest(
            source_export_checksum=checksum,
            selected=[("ep-ghost", "chk-ghost-undeclared")],
            all_candidates=[("ep-ghost", "chk-ghost-undeclared")],
        )
        with pytest.raises(CurationManifestMismatchError):
            await SceneOpsDataset.open(
                learning_manifest=manifest,
                learning_manifest_checksum=checksum,
                artifact_store=store,
                curation_manifest=curation,
            )

    async def test_selected_checksum_declared_but_absent_from_table_rejected(
        self, tmp_path
    ):
        store = LocalArtifactStore(root_uri=str(tmp_path))
        manifest, checksum = await _write_snapshot(
            tmp_path, store, entries=_main_entries()
        )
        # Declared in inputs (so it passes the "is this known to the
        # export" check) but never had a row written -- an inconsistent
        # snapshot.
        phantom_manifest = manifest.model_copy(
            update={
                "inputs": [
                    *manifest.inputs,
                    AlignedArtifactRevision(
                        episode_id="ep-ghost",
                        aligned_artifact_id="art-ghost",
                        aligned_artifact_checksum="chk-ghost-declared",
                    ),
                ]
            }
        )
        curation = _curation_manifest(
            source_export_checksum=checksum,
            selected=[("ep-ghost", "chk-ghost-declared")],
            all_candidates=[("ep-ghost", "chk-ghost-declared")],
        )
        with pytest.raises(LearningDataIntegrityError):
            await SceneOpsDataset.open(
                learning_manifest=phantom_manifest,
                learning_manifest_checksum=checksum,
                artifact_store=store,
                curation_manifest=curation,
            )

    async def test_missing_required_table_rejected(self, tmp_path):
        store = LocalArtifactStore(root_uri=str(tmp_path))
        manifest, checksum = await _write_snapshot(
            tmp_path, store, entries=_main_entries()
        )
        broken_manifest = manifest.model_copy(
            update={
                "table_uris": {
                    k: v
                    for k, v in manifest.table_uris.items()
                    if k != "learning_signals"
                }
            }
        )
        with pytest.raises(LearningTableMissingError):
            await SceneOpsDataset.open(
                learning_manifest=broken_manifest,
                learning_manifest_checksum=checksum,
                artifact_store=store,
            )


# ── step reconstruction ──────────────────────────────────────────────────


class TestStepReconstruction:
    async def test_first_and_last_step(self, tmp_path):
        store = LocalArtifactStore(root_uri=str(tmp_path))
        dataset, _m, _c = await _open_main_dataset(tmp_path, store)
        ref = _ref("ep-1", "chk-ep1-a")

        first = await dataset.get_step(ref, 0)
        assert first.timestamp_us == 0
        assert first.observations["pos"].value.scalar == 0.0

        last = await dataset.get_step(ref, 3)
        assert last.timestamp_us == 300_000
        assert last.observations["pos"].value.scalar == 3.0

    async def test_step_out_of_range(self, tmp_path):
        store = LocalArtifactStore(root_uri=str(tmp_path))
        dataset, _m, _c = await _open_main_dataset(tmp_path, store)
        ref = _ref("ep-1", "chk-ep1-a")

        with pytest.raises(StepOutOfRangeError):
            await dataset.get_step(ref, 4)
        with pytest.raises(StepOutOfRangeError):
            await dataset.get_step(ref, -1)

    async def test_unknown_episode_ref(self, tmp_path):
        store = LocalArtifactStore(root_uri=str(tmp_path))
        dataset, _m, _c = await _open_main_dataset(tmp_path, store)
        with pytest.raises(EpisodeNotFoundError):
            await dataset.get_step(_ref("ep-999", "does-not-exist"), 0)

    async def test_observation_and_action_same_channel_name_do_not_collide(
        self, tmp_path
    ):
        store = LocalArtifactStore(root_uri=str(tmp_path))
        dataset, _m, _c = await _open_main_dataset(tmp_path, store)
        ref = _ref("ep-1", "chk-ep1-a")

        step = await dataset.get_step(ref, 0)
        assert step.observations["shared"].value.scalar == 7.0
        assert step.actions["shared"].value.scalar == 8.0

    async def test_absent_vs_missing(self, tmp_path):
        store = LocalArtifactStore(root_uri=str(tmp_path))
        dataset, _m, _c = await _open_main_dataset(tmp_path, store)
        ref = _ref("ep-1", "chk-ep1-a")

        step0 = await dataset.get_step(ref, 0)
        assert "camera.front" not in step0.observations  # ABSENT
        assert step0.observations["imu"].status == AlignedSignalStatus.MISSING
        assert step0.observations["imu"].value is None

    async def test_scalar_and_vector_reconstruction(self, tmp_path):
        store = LocalArtifactStore(root_uri=str(tmp_path))
        dataset, _m, _c = await _open_main_dataset(tmp_path, store)
        ref = _ref("ep-1", "chk-ep1-a")

        step2 = await dataset.get_step(ref, 2)
        assert step2.observations["pos"].value.kind == AlignedValueKind.NUMERIC_SCALAR
        assert step2.observations["pos"].value.scalar == 2.0
        assert step2.observations["accel"].value.kind == AlignedValueKind.NUMERIC_VECTOR
        assert step2.observations["accel"].value.vector == [0.5, 0.6]

    async def test_interpolated_reconstruction(self, tmp_path):
        store = LocalArtifactStore(root_uri=str(tmp_path))
        dataset, _m, _c = await _open_main_dataset(tmp_path, store)
        ref = _ref("ep-1", "chk-ep1-a")

        step1 = await dataset.get_step(ref, 1)
        imu = step1.observations["imu"]
        assert imu.status == AlignedSignalStatus.INTERPOLATED
        assert imu.value.scalar == 1.5
        assert imu.interpolation_ratio == 0.5

    async def test_reference_reconstruction(self, tmp_path):
        store = LocalArtifactStore(root_uri=str(tmp_path))
        dataset, _m, _c = await _open_main_dataset(tmp_path, store)
        ref = _ref("ep-2", "chk-ep2")

        step0 = await dataset.get_step(ref, 0)
        camera = step0.observations["camera.front"]
        assert camera.value.kind == AlignedValueKind.REFERENCE
        assert camera.value.reference_uri == "mem://frames/ep2/0.png"
        assert camera.value.reference_modality == SensorModality.CAMERA
        # Not persisted by learning_signals.parquet -- documented lossy edge.
        assert camera.value.reference_channel is None
        assert camera.value.reference_metadata == {}


# ── 2.7A projection integration ─────────────────────────────────────────


class TestProjectionIntegration:
    async def test_feature_ordering_through_dataset(self, tmp_path):
        store = LocalArtifactStore(root_uri=str(tmp_path))
        dataset, _m, _c = await _open_main_dataset(tmp_path, store)
        ref = _ref("ep-1", "chk-ep1-a")

        sample_ab = await dataset.project_step(
            ref, 2, FeatureProjection(observation_channels=["pos", "accel"])
        )
        sample_ba = await dataset.project_step(
            ref, 2, FeatureProjection(observation_channels=["accel", "pos"])
        )

        assert sample_ab.observation == [2.0, 0.5, 0.6]
        assert sample_ba.observation == [0.5, 0.6, 2.0]

    async def test_absent_channel_raises_through_dataset(self, tmp_path):
        store = LocalArtifactStore(root_uri=str(tmp_path))
        dataset, _m, _c = await _open_main_dataset(tmp_path, store)
        ref = _ref("ep-1", "chk-ep1-a")

        with pytest.raises(FeatureAbsentError):
            await dataset.resolve_feature_schema(
                ref, FeatureProjection(observation_channels=["camera.front"])
            )

    async def test_missing_signal_raises_through_dataset(self, tmp_path):
        store = LocalArtifactStore(root_uri=str(tmp_path))
        dataset, _m, _c = await _open_main_dataset(tmp_path, store)
        ref = _ref("ep-1", "chk-ep1-a")

        with pytest.raises(FeatureMissingError):
            await dataset.project_step(
                ref, 0, FeatureProjection(observation_channels=["imu"])
            )

    async def test_shape_mismatch_raises_through_dataset(self, tmp_path):
        store = LocalArtifactStore(root_uri=str(tmp_path))
        entries = [
            (
                "chk-ep3-broken",
                _artifact(_episode("ep-3", _fixture_broken_shape_steps())),
            )
        ]
        manifest, checksum = await _write_snapshot(tmp_path, store, entries=entries)
        dataset = await SceneOpsDataset.open(
            learning_manifest=manifest,
            learning_manifest_checksum=checksum,
            artifact_store=store,
        )
        ref = _ref("ep-3", "chk-ep3-broken")

        with pytest.raises(FeatureShapeMismatchError):
            await dataset.resolve_feature_schema(
                ref, FeatureProjection(action_channels=["steering"])
            )


# ── window ────────────────────────────────────────────────────────────────


class TestWindow:
    async def test_exact_fit_window(self, tmp_path):
        store = LocalArtifactStore(root_uri=str(tmp_path))
        dataset, _m, _c = await _open_main_dataset(tmp_path, store)
        ref = _ref("ep-1", "chk-ep1-a")
        projection = FeatureProjection(
            observation_channels=["pos"], action_channels=["steering"]
        )

        sample = await dataset.get_window(ref, 0, 4, projection)

        assert sample.horizon == 4
        assert sample.timestamps_us == [0, 100_000, 200_000, 300_000]
        assert sample.observation == [[0.0], [1.0], [2.0], [3.0]]
        assert sample.action == [[0.0], [0.5], [1.0], [1.5]]
        assert len(sample.observation) == 4
        assert all(len(row) == 1 for row in sample.observation)
        assert all(len(row) == 1 for row in sample.action)

    async def test_overflow_window_raises(self, tmp_path):
        store = LocalArtifactStore(root_uri=str(tmp_path))
        dataset, _m, _c = await _open_main_dataset(tmp_path, store)
        ref = _ref("ep-1", "chk-ep1-a")
        projection = FeatureProjection(observation_channels=["pos"])

        with pytest.raises(SequenceBoundaryError):
            await dataset.get_window(ref, 1, 4, projection)


# ── lazy query behavior ──────────────────────────────────────────────────


class TestLazyQueryBehavior:
    async def test_opening_and_listing_episodes_never_reads_step_or_signal_tables(
        self, tmp_path
    ):
        inner = LocalArtifactStore(root_uri=str(tmp_path))
        store = _CountingArtifactStore(inner)
        dataset, manifest, _checksum = await _open_main_dataset(tmp_path, store)

        # episodes()/get_episode() are answerable purely from the already
        # -loaded learning_episodes table.
        dataset.episodes()
        dataset.get_episode(_ref("ep-1", "chk-ep1-a"))

        read_uris = store.read_bytes_calls
        assert manifest.table_uris["learning_episodes"] in read_uris
        assert manifest.table_uris["learning_steps"] not in read_uris
        assert manifest.table_uris["learning_signals"] not in read_uris

    async def test_each_table_fetched_at_most_once(self, tmp_path):
        inner = LocalArtifactStore(root_uri=str(tmp_path))
        store = _CountingArtifactStore(inner)
        dataset, manifest, _checksum = await _open_main_dataset(tmp_path, store)
        ref = _ref("ep-1", "chk-ep1-a")

        await dataset.get_step(ref, 0)
        await dataset.get_step(ref, 1)
        await dataset.resolve_feature_schema(
            ref, FeatureProjection(observation_channels=["pos"])
        )
        await dataset.get_window(
            ref, 0, 2, FeatureProjection(observation_channels=["pos"])
        )

        steps_uri = manifest.table_uris["learning_steps"]
        signals_uri = manifest.table_uris["learning_signals"]
        assert store.read_bytes_calls.count(steps_uri) == 1
        assert store.read_bytes_calls.count(signals_uri) == 1

    async def test_reading_one_episode_never_touches_another_episodes_rows(
        self, tmp_path
    ):
        """Injects a row for ep-2 with an invalid status value that would
        raise if ever parsed. Querying only ep-1 must never construct that
        row into an AlignedSignal -- proving the Polars filter (not a
        full-table Python-level parse) is what scopes each request."""
        inner = LocalArtifactStore(root_uri=str(tmp_path))

        entries = _main_entries()
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
            pl.when(pl.col("aligned_artifact_checksum") == "chk-ep2")
            .then(pl.lit("not-a-real-status"))
            .otherwise(pl.col("status"))
            .alias("status")
        )

        writer = AnalyticsTableWriter(
            artifact_store=inner, root_uri=str(tmp_path / "analytics-poisoned")
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

        # Querying ep-1 must never blow up on ep-2's poisoned rows.
        step = await dataset.get_step(_ref("ep-1", "chk-ep1-a"), 0)
        assert step.observations["pos"].value.scalar == 0.0

        # Confirm the poison is real: touching ep-2 itself does fail.
        with pytest.raises(ValueError):
            await dataset.get_step(_ref("ep-2", "chk-ep2"), 0)
