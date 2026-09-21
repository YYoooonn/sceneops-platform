"""Tests for the optional PyTorch adapter (SceneOps V2 Request 2.7D §6).

This module is the only test module in the package that imports torch --
skipped entirely (not erroring) if the ``torch`` extra isn't installed, via
``pytest.importorskip`` below. test_learning_consumer_adapter.py's NumPy-only
tests must pass regardless of whether this module runs.
"""

from __future__ import annotations

import asyncio
import pickle

import pytest

torch = pytest.importorskip("torch")

from sceneops_analytics import (  # noqa: E402
    AnalyticsTableWriter,
    SceneOpsDataset,
    SequenceSampler,
    build_learning_episodes_table,
    build_learning_signals_table,
    build_learning_steps_table,
    materialize_sequences,
    to_numpy,
)
from sceneops_analytics.learning_dataset.torch_adapter import SceneOpsTorchDataset  # noqa: E402
from sceneops_core.episodes.alignment import (  # noqa: E402
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
from sceneops_core.episodes.alignment.schemas import AlignedEpisode, LearningStep  # noqa: E402
from sceneops_core.episodes.learning import FeatureProjection  # noqa: E402
from sceneops_core.episodes.learning_export import (  # noqa: E402
    AlignedArtifactRevision,
    LearningDataExportConfig,
    LearningDataExportManifest,
    learning_data_export_id,
)
from sceneops_core.episodes.schemas.enums import EpisodeOutcome  # noqa: E402
from sceneops_storage import LocalArtifactStore  # noqa: E402

DATASET_ID = "ds1"
DATASET_VERSION = "v1"
_CONFIG = TemporalAlignmentConfig(target_frequency_hz=10.0)
_PROJECTION = FeatureProjection(
    observation_channels=["state.position", "state.velocity"],
    action_channels=["steering"],
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


async def _materialize(tmp_path):
    store = LocalArtifactStore(root_uri=str(tmp_path))
    _dataset, sampler = await _open_sampler(tmp_path, store, n_steps=10, horizon=4)
    materialized = await materialize_sequences(sampler)
    return sampler, materialized


class TestTorchAdapter:
    async def test_len_getitem_shapes_dtypes_and_metadata(self, tmp_path):
        sampler, materialized = await _materialize(tmp_path)
        torch_dataset = SceneOpsTorchDataset(materialized)

        assert len(torch_dataset) == len(sampler)

        item = torch_dataset[0]
        native = to_numpy(await sampler.get(0))

        assert item["episode_id"] == native.episode_ref.episode_id
        assert (
            item["aligned_artifact_checksum"]
            == native.episode_ref.aligned_artifact_checksum
        )
        assert item["start_step"] == native.start_step
        assert item["horizon"] == native.horizon

        assert isinstance(item["timestamps_us"], torch.Tensor)
        assert isinstance(item["observation"], torch.Tensor)
        assert isinstance(item["action"], torch.Tensor)

        assert item["timestamps_us"].dtype == torch.int64
        assert item["observation"].dtype == torch.float32
        assert item["action"].dtype == torch.float32

        assert tuple(item["observation"].shape) == (4, 3)
        assert tuple(item["action"].shape) == (4, 1)

        assert torch.equal(item["observation"], torch.from_numpy(native.observation))
        assert torch.equal(item["action"], torch.from_numpy(native.action))

    async def test_determinism(self, tmp_path):
        _sampler, materialized = await _materialize(tmp_path)
        torch_dataset = SceneOpsTorchDataset(materialized)

        a = torch_dataset[1]
        b = torch_dataset[1]
        assert torch.equal(a["observation"], b["observation"])
        assert torch.equal(a["action"], b["action"])
        assert torch.equal(a["timestamps_us"], b["timestamps_us"])

    def test_is_picklable_for_dataloader_worker_processes(self, tmp_path):
        """SceneOpsTorchDataset must survive pickling (fork/spawn worker
        handoff) -- it holds only plain ndarrays/primitives, no
        ArtifactStore/event-loop/async state."""
        _sampler, materialized = asyncio.run(_materialize(tmp_path))
        torch_dataset = SceneOpsTorchDataset(materialized)

        restored: SceneOpsTorchDataset = pickle.loads(pickle.dumps(torch_dataset))
        assert len(restored) == len(torch_dataset)
        for i in range(len(torch_dataset)):
            original_item = torch_dataset[i]
            restored_item = restored[i]
            assert torch.equal(
                original_item["observation"], restored_item["observation"]
            )
            assert torch.equal(original_item["action"], restored_item["action"])
            assert original_item["episode_id"] == restored_item["episode_id"]

    def test_getitem_never_creates_or_runs_an_event_loop(self, tmp_path, monkeypatch):
        """Proves __getitem__ hides no async-to-sync bridging: patch every
        event-loop entry point to raise, then exercise __getitem__ and
        confirm nothing touches them."""
        _sampler, materialized = asyncio.run(_materialize(tmp_path))
        torch_dataset = SceneOpsTorchDataset(materialized)

        def _forbidden(*args, **kwargs):
            raise AssertionError(
                "SceneOpsTorchDataset.__getitem__ must never touch asyncio"
            )

        monkeypatch.setattr(asyncio, "run", _forbidden)
        monkeypatch.setattr(asyncio, "new_event_loop", _forbidden)
        monkeypatch.setattr(asyncio, "get_event_loop", _forbidden)

        for i in range(len(torch_dataset)):
            item = torch_dataset[i]
            assert isinstance(item["observation"], torch.Tensor)

    def test_holds_no_dataset_or_storage_references(self):
        """Structural proof that the torch adapter never queries Parquet or
        knows ArtifactStore details -- it stores only the materialized
        sample list."""
        torch_dataset = SceneOpsTorchDataset([])
        assert vars(torch_dataset).keys() == {"_samples"}
