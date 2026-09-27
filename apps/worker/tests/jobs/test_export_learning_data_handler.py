"""Tests for ExportLearningDataJobHandler (SceneOps V2 Request 2.5).

Mocks WorkerContext the same way test_validate_aligned_episode_handler.py
does. Covers: successful multi-input export (creating one ArtifactRecord per
written table plus one for the manifest), checksum mismatch on one of
several pinned inputs blocking the whole export, and structural-validation
failure on one input blocking the whole export (no partial writes).
"""

from __future__ import annotations

import hashlib
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from sceneops_analytics import AnalyticsTableWriteResult
from sceneops_core.artifacts.schemas import (
    ArtifactKind,
    ArtifactOwnerType,
    ArtifactRecord,
)
from sceneops_core.episodes.alignment import (
    MCAP_LOG_TIME_CLOCK,
    AlignedEpisodeArtifact,
    EpisodeSourceRevision,
    TemporalAlignmentConfig,
    TemporalSourceContext,
    align_episode,
)
from sceneops_core.episodes.schemas import (
    EpisodeActionFrame,
    EpisodeManifest,
    EpisodeObservationFrame,
)
from sceneops_core.jobs.schemas import (
    ExportLearningDataJobParams,
    JobManifest,
    JobStatus,
    JobType,
)
from sceneops_worker.jobs.base import JobHandlerRequest
from sceneops_worker.jobs.dataset._aligned_episode_resolution import (
    AlignedArtifactChecksumMismatchError,
    AlignedArtifactNotFoundError,
)
from sceneops_worker.jobs.dataset.export_learning_data import (
    ExportLearningDataJobHandler,
)


def _artifact_bytes(episode_id: str, *, valid: bool = True) -> bytes:
    manifest = EpisodeManifest(
        episode_id=episode_id,
        observation_frames=[
            EpisodeObservationFrame(
                timestamp_us=0, channel="state.position", values=[0.0]
            ),
            EpisodeObservationFrame(
                timestamp_us=1_000_000, channel="state.position", values=[1.0]
            ),
        ],
        action_frames=[
            EpisodeActionFrame(timestamp_us=0, channel="steering", value=0.1)
        ],
        observation_channels=["state.position"],
        action_channels=["steering"],
        start_timestamp_us=0,
        end_timestamp_us=1_000_000,
        frame_count=3,
    )
    config = TemporalAlignmentConfig(target_frequency_hz=1.0, tolerance_us=500_000)
    ctx = TemporalSourceContext(source_clock=MCAP_LOG_TIME_CLOCK)
    aligned = align_episode(manifest, config, ctx)
    if not valid:
        aligned = aligned.model_copy(update={"step_count": 999})

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
    *, artifact_id: str, episode_id: str, checksum: str | None, uri: str
) -> ArtifactRecord:
    return ArtifactRecord(
        artifact_id=artifact_id,
        kind=ArtifactKind.ALIGNED_EPISODE_MANIFEST.value,
        uri=uri,
        owner_type=ArtifactOwnerType.EPISODE.value,
        owner_id=episode_id,
        checksum=checksum,
    )


def _make_context(
    *,
    records_by_artifact_id: dict[str, ArtifactRecord | None],
    bytes_by_uri: dict[str, bytes | None],
) -> MagicMock:
    context = MagicMock()
    context.default_dataset_id = "d1"
    context.default_dataset_version = "v1"

    async def _get(artifact_id: str):
        return records_by_artifact_id.get(artifact_id)

    async def _read_bytes(uri: str):
        return bytes_by_uri.get(uri)

    context.artifact_record_store.get = AsyncMock(side_effect=_get)
    context.episode_artifact_store.read_aligned_episode_bytes = AsyncMock(
        side_effect=_read_bytes
    )

    write_calls: list[str] = []

    async def _write_learning_table(table_name, df, **kwargs):
        write_calls.append(table_name)
        return AnalyticsTableWriteResult(
            uri=f"mem://learning/{kwargs['export_id']}/{table_name}.parquet",
            checksum=f"sha256:table-{table_name}",
            size_bytes=123,
        )

    async def _write_learning_table_shard(table_name, df, row_group_sizes, **kwargs):
        write_calls.append(f"{table_name}:shard{kwargs['shard_index']}")
        return AnalyticsTableWriteResult(
            uri=(
                f"mem://learning/{kwargs['export_id']}/{table_name}/"
                f"shard-{kwargs['shard_index']:05d}.parquet"
            ),
            checksum=f"sha256:shard-{table_name}-{kwargs['shard_index']}",
            size_bytes=123,
        )

    async def _write_manifest(manifest, **kwargs):
        write_calls.append("manifest")
        return AnalyticsTableWriteResult(
            uri=f"mem://learning/{kwargs['export_id']}/manifest.json",
            checksum="sha256:manifest-checksum",
            size_bytes=45,
        )

    context.analytics_writer.write_learning_table = AsyncMock(
        side_effect=_write_learning_table
    )
    context.analytics_writer.write_learning_table_shard = AsyncMock(
        side_effect=_write_learning_table_shard
    )
    context.analytics_writer.write_learning_export_manifest = AsyncMock(
        side_effect=_write_manifest
    )
    context._write_calls = write_calls

    context.artifact_record_store.create = AsyncMock()
    context.commit = AsyncMock()
    return context


def _make_request(context: MagicMock, **param_overrides) -> JobHandlerRequest:
    job = JobManifest(
        job_id="job-export-1",
        type=JobType.EXPORT_LEARNING_DATA,
        status=JobStatus.RUNNING,
    )
    defaults = dict(
        dataset_id="d1",
        dataset_version="v1",
        inputs=[
            {
                "episode_id": "ep-1",
                "aligned_artifact_id": "art-aligned-1",
            }
        ],
    )
    defaults.update(param_overrides)
    params = ExportLearningDataJobParams(**defaults)
    return JobHandlerRequest(job=job, params=params, context=context)


class TestSuccessfulExport:
    @pytest.mark.asyncio
    async def test_multi_input_export_writes_three_tables_and_manifest(self) -> None:
        data1 = _artifact_bytes("ep-1")
        data2 = _artifact_bytes("ep-2")
        uri1 = "mem://episodes/ep-1/aligned/x.json"
        uri2 = "mem://episodes/ep-2/aligned/x.json"

        context = _make_context(
            records_by_artifact_id={
                "art-aligned-1": _aligned_record(
                    artifact_id="art-aligned-1",
                    episode_id="ep-1",
                    checksum=_checksum(data1),
                    uri=uri1,
                ),
                "art-aligned-2": _aligned_record(
                    artifact_id="art-aligned-2",
                    episode_id="ep-2",
                    checksum=_checksum(data2),
                    uri=uri2,
                ),
            },
            bytes_by_uri={uri1: data1, uri2: data2},
        )
        request = _make_request(
            context,
            inputs=[
                {"episode_id": "ep-1", "aligned_artifact_id": "art-aligned-1"},
                {"episode_id": "ep-2", "aligned_artifact_id": "art-aligned-2"},
            ],
        )

        result = await ExportLearningDataJobHandler().run(request)

        assert result.episode_count == 2
        # learning_episodes stays single-file; learning_steps/
        # learning_signals are sharded (SceneOps V2 Request 5.2) -- both
        # episodes fit in one shard each under the default policy.
        assert set(result.table_uris) == {"learning_episodes"}
        assert result.shard_counts == {"learning_steps": 1, "learning_signals": 1}
        assert set(result.row_counts) == {
            "learning_episodes",
            "learning_steps",
            "learning_signals",
        }
        assert result.manifest_artifact_id is not None
        assert result.manifest_uri is not None
        assert set(context._write_calls) == {
            "learning_episodes",
            "learning_steps:shard0",
            "learning_signals:shard0",
            "manifest",
        }
        # 1 episodes table record + 1 steps shard + 1 signals shard + 1 manifest
        assert context.artifact_record_store.create.await_count == 4
        create_kinds = {
            call.kwargs["ref"].kind
            for call in context.artifact_record_store.create.await_args_list
        }
        assert create_kinds == {
            ArtifactKind.ANALYTICS_TABLE,
            ArtifactKind.LEARNING_DATA_EXPORT_MANIFEST,
        }
        owner_ids = {
            call.kwargs["owner_id"]
            for call in context.artifact_record_store.create.await_args_list
        }
        assert owner_ids == {"d1:v1"}
        context.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_requested_tables_subset_is_respected(self) -> None:
        data1 = _artifact_bytes("ep-1")
        uri1 = "mem://episodes/ep-1/aligned/x.json"
        context = _make_context(
            records_by_artifact_id={
                "art-aligned-1": _aligned_record(
                    artifact_id="art-aligned-1",
                    episode_id="ep-1",
                    checksum=_checksum(data1),
                    uri=uri1,
                ),
            },
            bytes_by_uri={uri1: data1},
        )
        request = _make_request(context, tables=["learning_steps"])

        result = await ExportLearningDataJobHandler().run(request)

        assert result.table_uris == {}
        assert result.shard_counts == {"learning_steps": 1}
        # 1 shard record + 1 manifest record
        assert context.artifact_record_store.create.await_count == 2


class TestArtifactNotFound:
    @pytest.mark.asyncio
    async def test_unresolvable_input_raises_and_writes_nothing(self) -> None:
        context = _make_context(records_by_artifact_id={}, bytes_by_uri={})
        request = _make_request(context)

        with pytest.raises(AlignedArtifactNotFoundError):
            await ExportLearningDataJobHandler().run(request)

        context.analytics_writer.write_learning_table.assert_not_awaited()
        context.analytics_writer.write_learning_table_shard.assert_not_awaited()
        context.artifact_record_store.create.assert_not_called()
        context.commit.assert_not_called()


class TestChecksumMismatchBlocksWholeExport:
    @pytest.mark.asyncio
    async def test_one_bad_checksum_among_several_inputs_blocks_export(self) -> None:
        data1 = _artifact_bytes("ep-1")
        data2 = _artifact_bytes("ep-2")
        uri1 = "mem://episodes/ep-1/aligned/x.json"
        uri2 = "mem://episodes/ep-2/aligned/x.json"

        context = _make_context(
            records_by_artifact_id={
                "art-aligned-1": _aligned_record(
                    artifact_id="art-aligned-1",
                    episode_id="ep-1",
                    checksum=_checksum(data1),
                    uri=uri1,
                ),
                "art-aligned-2": _aligned_record(
                    artifact_id="art-aligned-2",
                    episode_id="ep-2",
                    checksum="sha256:" + "0" * 64,  # deliberately wrong
                    uri=uri2,
                ),
            },
            bytes_by_uri={uri1: data1, uri2: data2},
        )
        request = _make_request(
            context,
            inputs=[
                {"episode_id": "ep-1", "aligned_artifact_id": "art-aligned-1"},
                {"episode_id": "ep-2", "aligned_artifact_id": "art-aligned-2"},
            ],
        )

        with pytest.raises(AlignedArtifactChecksumMismatchError):
            await ExportLearningDataJobHandler().run(request)

        context.analytics_writer.write_learning_table.assert_not_awaited()
        context.analytics_writer.write_learning_table_shard.assert_not_awaited()
        context.artifact_record_store.create.assert_not_called()
        context.commit.assert_not_called()


class TestStructuralValidationFailureBlocksWholeExport:
    @pytest.mark.asyncio
    async def test_invalid_aligned_artifact_blocks_export(self) -> None:
        data1 = _artifact_bytes("ep-1")
        data2_invalid = _artifact_bytes("ep-2", valid=False)
        uri1 = "mem://episodes/ep-1/aligned/x.json"
        uri2 = "mem://episodes/ep-2/aligned/x.json"

        context = _make_context(
            records_by_artifact_id={
                "art-aligned-1": _aligned_record(
                    artifact_id="art-aligned-1",
                    episode_id="ep-1",
                    checksum=_checksum(data1),
                    uri=uri1,
                ),
                "art-aligned-2": _aligned_record(
                    artifact_id="art-aligned-2",
                    episode_id="ep-2",
                    checksum=_checksum(data2_invalid),
                    uri=uri2,
                ),
            },
            bytes_by_uri={uri1: data1, uri2: data2_invalid},
        )
        request = _make_request(
            context,
            inputs=[
                {"episode_id": "ep-1", "aligned_artifact_id": "art-aligned-1"},
                {"episode_id": "ep-2", "aligned_artifact_id": "art-aligned-2"},
            ],
        )

        with pytest.raises(ValueError, match="structural validation"):
            await ExportLearningDataJobHandler().run(request)

        context.analytics_writer.write_learning_table.assert_not_awaited()
        context.analytics_writer.write_learning_table_shard.assert_not_awaited()
        context.artifact_record_store.create.assert_not_called()
        context.commit.assert_not_called()
