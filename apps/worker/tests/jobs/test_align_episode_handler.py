"""Tests for AlignEpisodeJobHandler (SceneOps V2 Request 2.3).

Mocks WorkerContext the same way test_register_episode_handler.py and
test_validate_episode_handler.py do. Covers: successful alignment, source
artifact not found, checksum mismatch (both pinned and ArtifactRecord-
sourced), legacy no-checksum records, manifest parse failure, pure
alignment failure, and that a failure before the write leaves no
ArtifactRecord while success creates exactly one.
"""

from __future__ import annotations

import hashlib
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from sceneops_core.artifacts.schemas import (
    ArtifactKind,
    ArtifactOwnerType,
    ArtifactRecord,
)
from sceneops_core.episodes.alignment import TemporalAlignmentConfig
from sceneops_core.episodes.schemas import EpisodeManifest, EpisodeRecord, EpisodeStatus
from sceneops_core.jobs.schemas import (
    AlignEpisodeJobParams,
    JobManifest,
    JobStatus,
    JobType,
)
from sceneops_worker.episodes.artifacts import EpisodeArtifactWriteResult
from sceneops_worker.jobs.base import JobHandlerRequest
from sceneops_worker.jobs.dataset.align_episode import (
    AlignEpisodeJobHandler,
    SourceManifestNotFoundError,
    SourceRevisionMismatchError,
)

_SOURCE_URI = "mem://episodes/ep-1.json"


def _manifest_bytes(episode_id: str = "ep-1") -> bytes:
    manifest = EpisodeManifest(
        episode_id=episode_id,
        dataset_id="d1",
        dataset_version="v1",
        start_timestamp_us=0,
        end_timestamp_us=0,
        frame_count=0,
        task="park",
    )
    return json.dumps(
        manifest.to_artifact_dict(), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _source_record(
    *, artifact_id: str = "art-1", checksum: str | None, uri: str = _SOURCE_URI
) -> ArtifactRecord:
    return ArtifactRecord(
        artifact_id=artifact_id,
        kind=ArtifactKind.EPISODE_MANIFEST.value,
        uri=uri,
        owner_type=ArtifactOwnerType.EPISODE.value,
        owner_id="ep-1",
        checksum=checksum,
    )


def _make_context(
    *,
    episode_record: EpisodeRecord | None,
    source_records: list[ArtifactRecord],
    manifest_bytes: bytes | None,
    write_should_fail: bool = False,
    create_should_fail: bool = False,
) -> MagicMock:
    context = MagicMock()
    context.default_dataset_id = "d1"
    context.default_dataset_version = "v1"
    context.episode_store.get = AsyncMock(return_value=episode_record)
    context.artifact_record_store.list = AsyncMock(return_value=source_records)
    context.artifact_record_store.get = AsyncMock(
        return_value=source_records[0] if source_records else None
    )
    context.episode_artifact_store.read_episode_manifest_bytes = AsyncMock(
        return_value=manifest_bytes
    )

    if write_should_fail:
        context.episode_artifact_store.write_aligned_episode = AsyncMock(
            side_effect=RuntimeError("storage backend unavailable")
        )
    else:
        context.episode_artifact_store.write_aligned_episode = AsyncMock(
            return_value=EpisodeArtifactWriteResult(
                uri="mem://episodes/ep-1/aligned/abc/def.json",
                checksum="sha256:aligned-checksum",
                size_bytes=42,
            )
        )

    if create_should_fail:
        context.artifact_record_store.create = AsyncMock(
            side_effect=RuntimeError("db write failed")
        )
    else:
        context.artifact_record_store.create = AsyncMock()

    context.commit = AsyncMock()
    return context


def _make_request(context: MagicMock, **param_overrides) -> JobHandlerRequest:
    job = JobManifest(
        job_id="job-align-1",
        type=JobType.ALIGN_EPISODE,
        status=JobStatus.RUNNING,
    )
    defaults = dict(
        episode_id="ep-1",
        dataset_id="d1",
        dataset_version="v1",
        alignment_config=TemporalAlignmentConfig(target_frequency_hz=1.0),
    )
    defaults.update(param_overrides)
    params = AlignEpisodeJobParams(**defaults)
    return JobHandlerRequest(job=job, params=params, context=context)


def _episode_record() -> EpisodeRecord:
    return EpisodeRecord(
        episode_id="ep-1",
        status=EpisodeStatus.REGISTERED,
        episode_manifest_uri=_SOURCE_URI,
    )


class TestSuccessfulAlignment:
    @pytest.mark.asyncio
    async def test_creates_exactly_one_aligned_artifact_record(self) -> None:
        data = _manifest_bytes()
        checksum = f"sha256:{hashlib.sha256(data).hexdigest()}"
        context = _make_context(
            episode_record=_episode_record(),
            source_records=[_source_record(checksum=checksum)],
            manifest_bytes=data,
        )
        request = _make_request(context)

        result = await AlignEpisodeJobHandler().run(request)

        assert result.episode_id == "ep-1"
        assert result.aligned_artifact_uri == "mem://episodes/ep-1/aligned/abc/def.json"
        assert result.source_checksum_verified is True
        assert result.step_count == 1  # zero-duration episode -> single step
        context.artifact_record_store.create.assert_awaited_once()
        create_kwargs = context.artifact_record_store.create.await_args.kwargs
        assert create_kwargs["ref"].kind == ArtifactKind.ALIGNED_EPISODE_MANIFEST
        assert create_kwargs["owner_type"] == ArtifactOwnerType.EPISODE
        assert create_kwargs["owner_id"] == "ep-1"
        context.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_legacy_record_without_checksum_proceeds_unverified(self) -> None:
        data = _manifest_bytes()
        context = _make_context(
            episode_record=_episode_record(),
            source_records=[_source_record(checksum=None)],
            manifest_bytes=data,
        )
        request = _make_request(context)

        result = await AlignEpisodeJobHandler().run(request)

        assert result.source_checksum_verified is False
        assert result.source_manifest_sha256 == hashlib.sha256(data).hexdigest()
        context.artifact_record_store.create.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_pinned_source_revision_is_resolved_via_get_not_list(self) -> None:
        data = _manifest_bytes()
        checksum_hex = hashlib.sha256(data).hexdigest()
        context = _make_context(
            episode_record=_episode_record(),
            source_records=[_source_record(checksum=f"sha256:{checksum_hex}")],
            manifest_bytes=data,
        )
        request = _make_request(
            context,
            source_artifact_id="art-1",
            source_manifest_sha256=checksum_hex,
        )

        result = await AlignEpisodeJobHandler().run(request)

        context.artifact_record_store.get.assert_awaited_once_with("art-1")
        context.artifact_record_store.list.assert_not_called()
        assert result.source_checksum_verified is True


class TestSourceNotFound:
    @pytest.mark.asyncio
    async def test_no_source_artifact_record_raises_and_writes_nothing(self) -> None:
        context = _make_context(
            episode_record=_episode_record(), source_records=[], manifest_bytes=None
        )
        request = _make_request(context)

        with pytest.raises(SourceManifestNotFoundError):
            await AlignEpisodeJobHandler().run(request)

        context.episode_artifact_store.write_aligned_episode.assert_not_called()
        context.artifact_record_store.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_bytes_raises_and_writes_nothing(self) -> None:
        context = _make_context(
            episode_record=_episode_record(),
            source_records=[_source_record(checksum=None)],
            manifest_bytes=None,
        )
        request = _make_request(context)

        with pytest.raises(SourceManifestNotFoundError):
            await AlignEpisodeJobHandler().run(request)

        context.artifact_record_store.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_episode_record_not_found_raises(self) -> None:
        context = _make_context(
            episode_record=None, source_records=[], manifest_bytes=None
        )
        request = _make_request(context)

        with pytest.raises(ValueError, match="Episode not found"):
            await AlignEpisodeJobHandler().run(request)


class TestChecksumMismatch:
    @pytest.mark.asyncio
    async def test_artifact_record_checksum_mismatch_blocks_write(self) -> None:
        data = _manifest_bytes()
        context = _make_context(
            episode_record=_episode_record(),
            source_records=[_source_record(checksum="sha256:" + "0" * 64)],
            manifest_bytes=data,
        )
        request = _make_request(context)

        with pytest.raises(SourceRevisionMismatchError):
            await AlignEpisodeJobHandler().run(request)

        context.episode_artifact_store.write_aligned_episode.assert_not_called()
        context.artifact_record_store.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_pinned_checksum_mismatch_blocks_write(self) -> None:
        data = _manifest_bytes()
        context = _make_context(
            episode_record=_episode_record(),
            source_records=[_source_record(checksum=None)],
            manifest_bytes=data,
        )
        request = _make_request(
            context,
            source_artifact_id="art-1",
            source_manifest_sha256="0" * 64,
        )

        with pytest.raises(SourceRevisionMismatchError):
            await AlignEpisodeJobHandler().run(request)

        context.artifact_record_store.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_never_pairs_artifact_id_from_one_revision_with_bytes_from_another(
        self,
    ) -> None:
        """Direct regression for Request 2.3 §8/§10's core invariant."""
        stale_data = _manifest_bytes()
        stale_checksum = f"sha256:{hashlib.sha256(stale_data).hexdigest()}"
        # ArtifactRecord claims `stale_checksum`, but the bytes actually
        # read back (simulating a concurrent overwrite) are different.
        overwritten_data = _manifest_bytes() + b" "
        context = _make_context(
            episode_record=_episode_record(),
            source_records=[_source_record(checksum=stale_checksum)],
            manifest_bytes=overwritten_data,
        )
        request = _make_request(context)

        with pytest.raises(SourceRevisionMismatchError):
            await AlignEpisodeJobHandler().run(request)


class TestAlignmentFailure:
    @pytest.mark.asyncio
    async def test_pure_alignment_failure_propagates_and_writes_nothing(self) -> None:
        # frame_count > 0 requires start/end bounds; give a manifest with
        # no bounds at all so the pure engine raises InvalidEpisodeBoundsError.
        manifest = EpisodeManifest(episode_id="ep-1", frame_count=0)
        data = json.dumps(
            manifest.to_artifact_dict(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        checksum = f"sha256:{hashlib.sha256(data).hexdigest()}"
        context = _make_context(
            episode_record=_episode_record(),
            source_records=[_source_record(checksum=checksum)],
            manifest_bytes=data,
        )
        request = _make_request(context)

        with pytest.raises(Exception):  # InvalidEpisodeBoundsError
            await AlignEpisodeJobHandler().run(request)

        context.episode_artifact_store.write_aligned_episode.assert_not_called()
        context.artifact_record_store.create.assert_not_called()


class TestWriteAndCreateFailures:
    @pytest.mark.asyncio
    async def test_artifact_write_failure_leaves_no_artifact_record(self) -> None:
        data = _manifest_bytes()
        checksum = f"sha256:{hashlib.sha256(data).hexdigest()}"
        context = _make_context(
            episode_record=_episode_record(),
            source_records=[_source_record(checksum=checksum)],
            manifest_bytes=data,
            write_should_fail=True,
        )
        request = _make_request(context)

        with pytest.raises(RuntimeError):
            await AlignEpisodeJobHandler().run(request)

        context.artifact_record_store.create.assert_not_called()
        context.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_artifact_record_create_failure_propagates(self) -> None:
        data = _manifest_bytes()
        checksum = f"sha256:{hashlib.sha256(data).hexdigest()}"
        context = _make_context(
            episode_record=_episode_record(),
            source_records=[_source_record(checksum=checksum)],
            manifest_bytes=data,
            create_should_fail=True,
        )
        request = _make_request(context)

        with pytest.raises(RuntimeError):
            await AlignEpisodeJobHandler().run(request)

        context.commit.assert_not_called()
