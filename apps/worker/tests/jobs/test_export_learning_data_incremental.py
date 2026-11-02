"""Tests for incremental learning-data export (SceneOps V2 Request 5.5).

Unlike test_export_learning_data_handler.py (which mocks
``context.analytics_writer`` entirely), these tests use a real
``LocalArtifactStore`` + ``AnalyticsTableWriter`` so base-manifest
resolution, shard reuse, and the ``learning_episodes`` merge are exercised
against real Parquet/JSON bytes on disk -- only aligned-artifact resolution
(``episode_artifact_store``/``artifact_record_store.get``) and lineage
recording (``artifact_record_store.register``) are mocked, exactly like
_aligned_episode_resolution's role in every other export/curation test.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import io

import polars as pl
import pyarrow.parquet as pq

from sceneops_analytics import AnalyticsTableWriter, SceneOpsDataset
from sceneops_analytics.testing.learning_migration import (
    temporal_entries,
    write_v1_export,
)
from sceneops_core.artifacts.schemas import (
    ArtifactKind,
    ArtifactOwnerType,
    ArtifactRecord,
)
from sceneops_core.episodes.alignment import (
    AlignedEpisodeArtifact,
    EpisodeSourceRevision,
    TemporalAlignmentConfig,
    TemporalSourceContext,
    align_episode,
    load_aligned_episode_artifact,
)
from sceneops_core.episodes.learning_export import (
    IncrementalExportUnsupportedBaseError,
    LearningDataExportManifest,
)
from sceneops_core.episodes.testing import (
    DEFAULT_CLOCK,
    action,
    episode_manifest,
    state,
)
from sceneops_core.jobs.schemas import (
    ExportLearningDataJobParams,
    JobManifest,
    JobStatus,
    JobType,
)
from sceneops_storage.backends.local import LocalArtifactStore
from sceneops_worker.jobs.base import JobHandlerRequest
from sceneops_worker.jobs.dataset.export_learning_data import (
    ExportLearningDataJobHandler,
)


def _artifact_bytes(episode_id: str) -> bytes:
    manifest = episode_manifest(
        [
            state("/vehicle/odom", 0, x=0.0),
            state("/vehicle/odom", 1_000_000_000, x=1.0),
            action("/vehicle/control", 0, steering=0.1),
        ],
        window=(0, 1_000_000_001),
    )
    config = TemporalAlignmentConfig(target_frequency_hz=1.0, tolerance_us=500_000)
    ctx = TemporalSourceContext(source_clock=DEFAULT_CLOCK)
    aligned = align_episode(manifest, config, ctx, episode_id=episode_id)
    artifact = AlignedEpisodeArtifact(
        source_revision=EpisodeSourceRevision(
            episode_id=episode_id,
            episode_manifest_uri=f"mem://episodes/{episode_id}.json",
            source_artifact_id="art-src-1",
            source_manifest_sha256="a" * 64,
        ),
        aligned_episode=aligned,
    )
    return json.dumps(
        artifact.to_artifact_dict(), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _checksum(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _aligned_record(
    *, artifact_id: str, episode_id: str, checksum: str, uri: str
) -> ArtifactRecord:
    return ArtifactRecord(
        artifact_id=artifact_id,
        kind=ArtifactKind.ALIGNED_EPISODE_MANIFEST.value,
        uri=uri,
        owner_type=ArtifactOwnerType.EPISODE.value,
        owner_id=episode_id,
        checksum=checksum,
    )


class _Fixture:
    """One aligned-episode-resolution universe shared by a base export and
    its incremental follow-up -- new episode_ids can be added to
    ``episodes`` between runs, simulating new EpisodeRefs becoming
    available for a later incremental export."""

    def __init__(self, root_uri: str) -> None:
        self.records_by_artifact_id: dict[str, ArtifactRecord] = {}
        self.bytes_by_uri: dict[str, bytes] = {}
        self.writer = AnalyticsTableWriter(
            artifact_store=LocalArtifactStore(root_uri=root_uri), root_uri=root_uri
        )

    def add_episode(self, episode_id: str, *, artifact_id: str) -> str:
        data = _artifact_bytes(episode_id)
        uri = f"mem://episodes/{episode_id}/aligned/x.json"
        self.bytes_by_uri[uri] = data
        self.records_by_artifact_id[artifact_id] = _aligned_record(
            artifact_id=artifact_id,
            episode_id=episode_id,
            checksum=_checksum(data),
            uri=uri,
        )
        return artifact_id

    def make_context(self) -> MagicMock:
        context = MagicMock()

        async def _get(artifact_id: str):
            return self.records_by_artifact_id.get(artifact_id)

        async def _read_bytes(uri: str):
            return self.bytes_by_uri.get(uri)

        context.artifact_record_store.get = AsyncMock(side_effect=_get)
        context.episode_store.get_member_revision = AsyncMock(
            return_value=SimpleNamespace(
                manifest_artifact_id="art-src-1",
                manifest_checksum="sha256:" + "a" * 64,
            )
        )
        context.episode_artifact_store.read_aligned_episode_bytes = AsyncMock(
            side_effect=_read_bytes
        )
        context.analytics_writer = self.writer
        context.artifact_record_store.register = AsyncMock()
        context.commit = AsyncMock()
        return context

    def make_request(self, context: MagicMock, **param_overrides) -> JobHandlerRequest:
        job = JobManifest(
            job_id=f"job-{len(param_overrides)}-{param_overrides.get('base_export_id', 'base')}",
            type=JobType.EXPORT_LEARNING_DATA,
            status=JobStatus.RUNNING,
        )
        defaults = dict(dataset_id="d1", dataset_version="v1", inputs=[])
        defaults.update(param_overrides)
        params = ExportLearningDataJobParams(**defaults)
        return JobHandlerRequest(job=job, params=params, context=context)


@pytest.fixture
def fixture(tmp_path: Path) -> _Fixture:
    return _Fixture(root_uri=str(tmp_path))


def _input_for(episode_id: str, artifact_id: str) -> dict:
    return {"episode_id": episode_id, "aligned_artifact_id": artifact_id}


@pytest.mark.asyncio
async def test_incremental_export_reuses_base_shards_verbatim(
    fixture: _Fixture,
) -> None:
    for i in range(3):
        fixture.add_episode(f"ep-{i}", artifact_id=f"art-{i}")

    base_context = fixture.make_context()
    base_request = fixture.make_request(
        base_context,
        inputs=[_input_for(f"ep-{i}", f"art-{i}") for i in range(3)],
    )
    base_result = await ExportLearningDataJobHandler().run(base_request)
    assert base_result.reused_shard_counts == {}
    assert base_result.new_shard_counts == {}
    base_export_id = base_result.export_id

    fixture.add_episode("ep-3", artifact_id="art-3")
    fixture.add_episode("ep-4", artifact_id="art-4")

    incremental_context = fixture.make_context()
    incremental_request = fixture.make_request(
        incremental_context,
        inputs=[_input_for("ep-3", "art-3"), _input_for("ep-4", "art-4")],
        base_export_id=base_export_id,
    )
    incremental_result = await ExportLearningDataJobHandler().run(incremental_request)

    assert incremental_result.base_export_id == base_export_id
    assert incremental_result.episode_count == 5
    # All 3 base episodes fit in one shard under the default policy, so the
    # base's single shard is reused verbatim; the 2 new episodes land in a
    # freshly-written shard numbered after it.
    assert incremental_result.reused_shard_counts == {
        "learning_steps": 1,
        "learning_signals": 1,
    }
    assert incremental_result.new_shard_counts == {
        "learning_steps": 1,
        "learning_signals": 1,
    }
    assert incremental_result.shard_counts == {
        "learning_steps": 2,
        "learning_signals": 2,
    }
    assert incremental_result.row_counts["learning_episodes"] == 5

    # Only the new shards + new learning_episodes + manifest get lineage
    # records -- never the base's own (already-recorded) shards.
    created_uris = {
        call.kwargs["ref"].uri
        for call in incremental_context.artifact_record_store.register.await_args_list
    }
    assert len(created_uris) == 4  # learning_episodes + 2 new shards + manifest
    base_manifest_bytes = await fixture.writer.read_learning_export_manifest_bytes(
        fixture.writer.learning_export_manifest_uri(
            dataset_id="d1", dataset_version="v1", export_id=base_export_id
        )
    )
    base_manifest = LearningDataExportManifest.model_validate_json(base_manifest_bytes)
    base_steps_uris = {shard.uri for shard in base_manifest.shard_index.learning_steps}
    base_signals_uris = {
        shard.uri for shard in base_manifest.shard_index.learning_signals
    }
    assert created_uris.isdisjoint(base_steps_uris | base_signals_uris)

    incremental_manifest_bytes = (
        await fixture.writer.read_learning_export_manifest_bytes(
            fixture.writer.learning_export_manifest_uri(
                dataset_id="d1",
                dataset_version="v1",
                export_id=incremental_result.export_id,
            )
        )
    )
    incremental_manifest = LearningDataExportManifest.model_validate_json(
        incremental_manifest_bytes
    )
    assert incremental_manifest.base_export_id == base_export_id
    assert {ref.episode_id for ref in incremental_manifest.inputs} == {
        f"ep-{i}" for i in range(5)
    }
    # Reused shards keep base_export_id's own URI -- never rewritten.
    assert {
        shard.uri for shard in incremental_manifest.shard_index.learning_steps
    } >= base_steps_uris
    assert {
        shard.uri for shard in incremental_manifest.shard_index.learning_signals
    } >= base_signals_uris


@pytest.mark.asyncio
async def test_incremental_export_new_revision_of_existing_episode(
    fixture: _Fixture,
) -> None:
    fixture.add_episode("ep-0", artifact_id="art-0-r1")
    base_context = fixture.make_context()
    base_request = fixture.make_request(
        base_context, inputs=[_input_for("ep-0", "art-0-r1")]
    )
    base_result = await ExportLearningDataJobHandler().run(base_request)

    # A second, distinct aligned revision of the SAME episode_id -- must
    # coexist as a distinct EpisodeRef, never dedup by episode_id alone.
    # The same source revision under another recipe, the only other aligned
    # revision a DatasetVersion pinning that source can use.
    manifest = episode_manifest(
        [
            state("/vehicle/odom", 0, x=0.0),
            state("/vehicle/odom", 1_000_000_000, x=1.0),
            action("/vehicle/control", 0, steering=0.1),
        ],
        window=(0, 1_000_000_001),
    )
    config = TemporalAlignmentConfig(target_frequency_hz=1.0, tolerance_us=400_000)
    ctx = TemporalSourceContext(source_clock=DEFAULT_CLOCK)
    aligned = align_episode(manifest, config, ctx, episode_id="ep-0")
    artifact_r2 = AlignedEpisodeArtifact(
        source_revision=EpisodeSourceRevision(
            episode_id="ep-0",
            episode_manifest_uri="mem://episodes/ep-0.json",
            source_artifact_id="art-src-1",
            source_manifest_sha256="a" * 64,
        ),
        aligned_episode=aligned,
    )
    data_r2 = json.dumps(
        artifact_r2.to_artifact_dict(), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    uri_r2 = "mem://episodes/ep-0/aligned/r2.json"
    fixture.bytes_by_uri[uri_r2] = data_r2
    fixture.records_by_artifact_id["art-0-r2"] = _aligned_record(
        artifact_id="art-0-r2",
        episode_id="ep-0",
        checksum=_checksum(data_r2),
        uri=uri_r2,
    )

    incremental_context = fixture.make_context()
    incremental_request = fixture.make_request(
        incremental_context,
        inputs=[_input_for("ep-0", "art-0-r2")],
        base_export_id=base_result.export_id,
    )
    incremental_result = await ExportLearningDataJobHandler().run(incremental_request)

    assert incremental_result.episode_count == 1
    assert incremental_result.new_shard_counts == {
        "learning_steps": 1,
        "learning_signals": 1,
    }
    assert incremental_result.row_counts["learning_episodes"] == 2


@pytest.mark.asyncio
async def test_incremental_export_rejects_delta_that_redeclares_base_revision(
    fixture: _Fixture,
) -> None:
    """The job handler always merges ``base_manifest.inputs`` with the
    delta (SceneOps V2 Request 5.5 §1) -- there is no job-params way to
    *drop* a base EpisodeRef (that unsupported-base check in
    ``plan_incremental_export`` is exercised directly at the sceneops-core
    unit-test level instead). What a caller *can* accidentally do is
    resubmit a revision already included in the base as part of the
    delta -- caught as a duplicate-key overlap.
    """
    fixture.add_episode("ep-0", artifact_id="art-0")
    base_context = fixture.make_context()
    base_request = fixture.make_request(
        base_context, inputs=[_input_for("ep-0", "art-0")]
    )
    base_result = await ExportLearningDataJobHandler().run(base_request)

    incremental_context = fixture.make_context()
    incremental_request = fixture.make_request(
        incremental_context,
        inputs=[_input_for("ep-0", "art-0")],
        base_export_id=base_result.export_id,
    )

    with pytest.raises(Exception, match="duplicate"):
        await ExportLearningDataJobHandler().run(incremental_request)
    incremental_context.artifact_record_store.register.assert_not_called()
    incremental_context.commit.assert_not_called()


async def _open_dataset(fixture: _Fixture, export_id: str) -> SceneOpsDataset:
    uri = fixture.writer.learning_export_manifest_uri(
        dataset_id="d1", dataset_version="v1", export_id=export_id
    )
    raw_bytes = await fixture.writer.read_learning_export_manifest_bytes(uri)
    manifest = LearningDataExportManifest.model_validate_json(raw_bytes)
    return await SceneOpsDataset.open(
        learning_manifest=manifest,
        learning_manifest_checksum=f"sha256:{hashlib.sha256(raw_bytes).hexdigest()}",
        artifact_store=fixture.writer.artifact_store,
    )


@pytest.mark.asyncio
async def test_full_build_and_incremental_export_expose_identical_logical_data(
    fixture: _Fixture,
) -> None:
    """SceneOpsDataset needs zero incremental-specific code (SceneOps V2
    Request 5.5 §9): a full export over {ep-0..ep-4} and an incremental
    export that arrives at the same final set via a base {ep-0,ep-1,ep-2}
    plus a delta {ep-3,ep-4} must expose identical episodes() and identical
    per-step data through the same reader.
    """
    for i in range(5):
        fixture.add_episode(f"ep-{i}", artifact_id=f"art-{i}")

    full_context = fixture.make_context()
    full_request = fixture.make_request(
        full_context,
        inputs=[_input_for(f"ep-{i}", f"art-{i}") for i in range(5)],
    )
    full_result = await ExportLearningDataJobHandler().run(full_request)

    base_context = fixture.make_context()
    base_request = fixture.make_request(
        base_context,
        inputs=[_input_for(f"ep-{i}", f"art-{i}") for i in range(3)],
    )
    base_result = await ExportLearningDataJobHandler().run(base_request)

    incremental_context = fixture.make_context()
    incremental_request = fixture.make_request(
        incremental_context,
        inputs=[_input_for(f"ep-{i}", f"art-{i}") for i in range(3, 5)],
        base_export_id=base_result.export_id,
    )
    incremental_result = await ExportLearningDataJobHandler().run(incremental_request)

    # Two physically different exports, so two ids: neither overwrote the other.
    assert full_result.export_id != incremental_result.export_id

    full_dataset = await _open_dataset(fixture, full_result.export_id)
    incremental_dataset = await _open_dataset(fixture, incremental_result.export_id)

    assert full_dataset.episodes() == incremental_dataset.episodes()
    assert len(full_dataset.episodes()) == 5

    for ref in full_dataset.episodes():
        full_metadata = full_dataset.get_episode(ref)
        incremental_metadata = incremental_dataset.get_episode(ref)
        assert full_metadata.step_count == incremental_metadata.step_count
        assert full_metadata.task == incremental_metadata.task
        assert full_metadata.outcome == incremental_metadata.outcome

        for step_index in range(full_metadata.step_count):
            full_step = await full_dataset.get_step(ref, step_index)
            incremental_step = await incremental_dataset.get_step(ref, step_index)
            assert full_step == incremental_step


async def _objects(fixture: _Fixture) -> dict[str, bytes]:
    store = fixture.writer.artifact_store
    return {
        item.uri: await store.read_bytes(item.uri)
        for item in await store.list_objects(fixture.writer.root_uri)
    }


async def _base_export(fixture: _Fixture) -> tuple[str, LearningDataExportManifest]:
    fixture.add_episode("ep-0", artifact_id="art-0")
    result = await ExportLearningDataJobHandler().run(
        fixture.make_request(
            fixture.make_context(), inputs=[_input_for("ep-0", "art-0")]
        )
    )
    data = await fixture.writer.read_learning_export_manifest_bytes(
        fixture.writer.learning_export_manifest_uri(
            dataset_id="d1", dataset_version="v1", export_id=result.export_id
        )
    )
    return result.export_id, LearningDataExportManifest.model_validate_json(data)


async def _assert_base_refused(fixture: _Fixture, base_export_id: str, match: str):
    fixture.add_episode("ep-new", artifact_id="art-new")
    objects = await _objects(fixture)
    context = fixture.make_context()
    with pytest.raises(IncrementalExportUnsupportedBaseError, match=match):
        await ExportLearningDataJobHandler().run(
            fixture.make_request(
                context,
                inputs=[_input_for("ep-new", "art-new")],
                base_export_id=base_export_id,
            )
        )
    # Refused before anything was read for the delta, written or registered.
    context.artifact_record_store.get.assert_not_awaited()
    context.artifact_record_store.register.assert_not_awaited()
    context.commit.assert_not_awaited()
    assert await _objects(fixture) == objects


@pytest.mark.asyncio
async def test_a_v1_base_is_refused_before_any_shard_is_reused(
    fixture: _Fixture,
) -> None:
    """Its steps and signals have the columns of today's, so nothing would fail
    later: its rows would be carried into an export declaring today's schema."""
    entries = list(temporal_entries().values())
    _, data = await write_v1_export(
        fixture.writer, dataset_id="d1", dataset_version="v1", entries=entries
    )
    await _assert_base_refused(fixture, json.loads(data)["exportId"], "'v1'")


@pytest.mark.parametrize("version", [None, "v999", 2])
@pytest.mark.asyncio
async def test_a_base_of_a_missing_or_unknown_version_is_refused(
    fixture: _Fixture, version
) -> None:
    export_id, manifest = await _base_export(fixture)
    document = manifest.to_artifact_dict()
    if version is None:
        del document["schemaVersion"]
    else:
        document["schemaVersion"] = version
    await fixture.writer.artifact_store.write_bytes(
        fixture.writer.learning_export_manifest_uri(
            dataset_id="d1", dataset_version="v1", export_id=export_id
        ),
        json.dumps(document).encode(),
    )
    await _assert_base_refused(fixture, export_id, "not supported")


@pytest.mark.asyncio
async def test_a_base_whose_manifest_lies_about_a_shard_is_refused(
    fixture: _Fixture,
) -> None:
    """The manifest says today's schema and a shard's footer does not."""
    export_id, manifest = await _base_export(fixture)
    shard = manifest.shard_index.learning_signals[0]
    store = fixture.writer.artifact_store
    table = pq.read_table(io.BytesIO(await store.read_bytes(shard.uri)))
    sink = io.BytesIO()
    pq.write_table(table.replace_schema_metadata(None), sink)
    await store.write_bytes(shard.uri, sink.getvalue())
    document = manifest.to_artifact_dict()
    document["shardIndex"]["learningSignals"][0]["sizeBytes"] = len(sink.getvalue())
    await store.write_bytes(
        fixture.writer.learning_export_manifest_uri(
            dataset_id="d1", dataset_version="v1", export_id=export_id
        ),
        json.dumps(document).encode(),
    )
    await _assert_base_refused(fixture, export_id, "footer states")


@pytest.mark.asyncio
async def test_a_base_whose_learning_episodes_footer_is_stale_is_refused(
    fixture: _Fixture,
) -> None:
    export_id, manifest = await _base_export(fixture)
    uri = manifest.table_uris["learning_episodes"]
    store = fixture.writer.artifact_store
    buffer = io.BytesIO()
    pl.read_parquet(io.BytesIO(await store.read_bytes(uri))).write_parquet(buffer)
    await store.write_bytes(uri, buffer.getvalue())
    await _assert_base_refused(fixture, export_id, "footer states")


@pytest.mark.asyncio
async def test_a_base_shard_that_cannot_be_read_is_refused(fixture: _Fixture) -> None:
    export_id, manifest = await _base_export(fixture)
    await fixture.writer.artifact_store.write_bytes(
        manifest.shard_index.learning_steps[0].uri, b"x" * 8
    )
    document = manifest.to_artifact_dict()
    document["shardIndex"]["learningSteps"][0]["sizeBytes"] = 8
    await fixture.writer.artifact_store.write_bytes(
        fixture.writer.learning_export_manifest_uri(
            dataset_id="d1", dataset_version="v1", export_id=export_id
        ),
        json.dumps(document).encode(),
    )
    await _assert_base_refused(fixture, export_id, "cannot be an incremental base")


@pytest.mark.asyncio
async def test_an_incremental_export_reads_back_with_every_episodes_instance(
    fixture: _Fixture,
) -> None:
    export_id, _ = await _base_export(fixture)
    fixture.add_episode("ep-1", artifact_id="art-1")
    context = fixture.make_context()
    result = await ExportLearningDataJobHandler().run(
        fixture.make_request(
            context, inputs=[_input_for("ep-1", "art-1")], base_export_id=export_id
        )
    )
    assert result.reused_shard_counts == {"learning_steps": 1, "learning_signals": 1}

    data = await fixture.writer.read_learning_export_manifest_bytes(result.manifest_uri)
    manifest = LearningDataExportManifest.model_validate_json(data)
    assert manifest.schema_version == "v2"
    dataset = await SceneOpsDataset.open(
        learning_manifest=manifest,
        learning_manifest_checksum=_checksum(data),
        artifact_store=fixture.writer.artifact_store,
    )
    assert len(dataset) == 2
    for ref in dataset.episodes():
        metadata = dataset.get_episode(ref)
        assert (
            metadata.clock_instance
            == load_aligned_episode_artifact(
                fixture.bytes_by_uri[f"mem://episodes/{ref.episode_id}/aligned/x.json"]
            ).aligned_episode.clock_instance
        )
        await dataset.get_step(ref, 0)
