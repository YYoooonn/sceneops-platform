"""Tests for ValidateAlignedEpisodeJobHandler (SceneOps V2 Request 2.4).

Mocks WorkerContext the same way test_align_episode_handler.py does. Covers:
successful validation (of both a structurally valid and an invalid aligned
artifact), artifact not found, checksum mismatch (pinned and
ArtifactRecord-sourced), and that a failure before the write leaves no
report ArtifactRecord while success creates exactly one.
"""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

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
    UnsupportedAlignedArtifactSchemaError,
    align_episode,
)
from sceneops_core.episodes.testing import (
    DEFAULT_CLOCK,
    action,
    episode_manifest,
    state,
)
from sceneops_core.jobs.schemas import (
    JobManifest,
    JobStatus,
    JobType,
    ValidateAlignedEpisodeJobParams,
)
from sceneops_episodes.artifacts import EpisodeArtifactWriteResult
from sceneops_worker.jobs.base import JobHandlerRequest
from sceneops_episodes.artifacts import canonical_document_bytes
from sceneops_worker.jobs.dataset._aligned_episode_resolution import (
    AlignedArtifactNotEligibleError,
    AlignedArtifactChecksumMismatchError,
    AlignedArtifactNotFoundError,
    resolve_and_verify_aligned_artifact,
)
from sceneops_worker.jobs.dataset.validate_aligned_episode import (
    ValidateAlignedEpisodeJobHandler,
)

_ALIGNED_URI = "mem://episodes/ep-1/aligned/x.json"


def _artifact_bytes(*, valid: bool = True) -> bytes:
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
    aligned = align_episode(manifest, config, ctx, episode_id="ep-1")
    if not valid:
        aligned = aligned.model_copy(
            update={"step_count": 999}
        )  # structural contradiction

    artifact = AlignedEpisodeArtifact(
        source_revision=EpisodeSourceRevision(
            episode_id="ep-1",
            episode_manifest_uri="mem://episodes/ep-1.json",
            source_artifact_id="art-src-1",
            source_manifest_sha256="a" * 64,
        ),
        aligned_episode=aligned,
    )
    return json.dumps(
        artifact.to_artifact_dict(), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


_MEMBER = SimpleNamespace(
    manifest_artifact_id="art-src-1", manifest_checksum="sha256:" + "a" * 64
)


def _aligned_record(
    *, artifact_id: str = "art-aligned-1", checksum: str | None, uri: str = _ALIGNED_URI
) -> ArtifactRecord:
    return ArtifactRecord(
        artifact_id=artifact_id,
        kind=ArtifactKind.ALIGNED_EPISODE_MANIFEST.value,
        uri=uri,
        owner_type=ArtifactOwnerType.EPISODE.value,
        owner_id="ep-1",
        checksum=checksum,
    )


def _make_context(
    *,
    aligned_record: ArtifactRecord | None,
    artifact_bytes: bytes | None,
    write_should_fail: bool = False,
) -> MagicMock:
    context = MagicMock()
    # Only the aligned artifact is registered, so a report is not yet.
    context.artifact_record_store.get = AsyncMock(
        side_effect=lambda artifact_id: aligned_record
        if aligned_record is not None and artifact_id == aligned_record.artifact_id
        else None
    )
    context.episode_store.get_member_revision = AsyncMock(
        side_effect=lambda **scope: _MEMBER
        if (scope["dataset_id"], scope["dataset_version"]) == ("d1", "v1")
        else None
    )
    context.episode_artifact_store.read_aligned_episode_bytes = AsyncMock(
        return_value=artifact_bytes
    )
    if write_should_fail:
        context.episode_artifact_store.write_aligned_episode_report = AsyncMock(
            side_effect=RuntimeError("storage backend unavailable")
        )
    else:
        context.episode_artifact_store.write_aligned_episode_report = AsyncMock(
            return_value=EpisodeArtifactWriteResult(
                uri="mem://episodes/ep-1/aligned/x.validation.json",
                checksum="sha256:report-checksum",
                size_bytes=99,
            )
        )
    context.artifact_record_store.register = AsyncMock()
    context.commit = AsyncMock()
    return context


def _make_request(context: MagicMock, **overrides) -> JobHandlerRequest:
    job = JobManifest(
        job_id="job-validate-1",
        type=JobType.VALIDATE_ALIGNED_EPISODE,
        status=JobStatus.RUNNING,
    )
    defaults = dict(
        episode_id="ep-1",
        dataset_id="d1",
        dataset_version="v1",
        aligned_artifact_id="art-aligned-1",
    )
    defaults.update(overrides)
    params = ValidateAlignedEpisodeJobParams(**defaults)
    return JobHandlerRequest(job=job, params=params, context=context)


class TestSuccessfulValidation:
    @pytest.mark.asyncio
    async def test_valid_artifact_creates_exactly_one_report_record(self) -> None:
        data = _artifact_bytes(valid=True)
        checksum = f"sha256:{hashlib.sha256(data).hexdigest()}"
        context = _make_context(
            aligned_record=_aligned_record(checksum=checksum), artifact_bytes=data
        )
        request = _make_request(context)

        result = await ValidateAlignedEpisodeJobHandler().run(request)

        assert result.valid is True
        assert result.issue_count == 0
        context.artifact_record_store.register.assert_awaited_once()
        create_kwargs = context.artifact_record_store.register.await_args.kwargs
        assert (
            create_kwargs["ref"].kind == ArtifactKind.ALIGNED_EPISODE_VALIDATION_REPORT
        )
        context.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_invalid_artifact_still_persists_a_report_with_issues(self) -> None:
        data = _artifact_bytes(valid=False)
        checksum = f"sha256:{hashlib.sha256(data).hexdigest()}"
        context = _make_context(
            aligned_record=_aligned_record(checksum=checksum), artifact_bytes=data
        )
        request = _make_request(context)

        result = await ValidateAlignedEpisodeJobHandler().run(request)

        assert result.valid is False
        assert result.issue_count > 0
        # An invalid *aligned artifact* still produces a successful *Job* --
        # validation reporting "invalid" is not itself a job failure.
        context.artifact_record_store.register.assert_awaited_once()
        context.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_pinned_checksum_is_used_when_record_has_none(self) -> None:
        data = _artifact_bytes(valid=True)
        checksum_hex = hashlib.sha256(data).hexdigest()
        context = _make_context(
            aligned_record=_aligned_record(checksum=None), artifact_bytes=data
        )
        request = _make_request(context, aligned_artifact_checksum=checksum_hex)

        result = await ValidateAlignedEpisodeJobHandler().run(request)

        assert result.valid is True


class TestArtifactNotFound:
    @pytest.mark.asyncio
    async def test_no_record_raises_and_writes_nothing(self) -> None:
        context = _make_context(aligned_record=None, artifact_bytes=None)
        request = _make_request(context)

        with pytest.raises(AlignedArtifactNotFoundError):
            await ValidateAlignedEpisodeJobHandler().run(request)

        context.artifact_record_store.register.assert_not_called()

    @pytest.mark.asyncio
    async def test_wrong_owner_raises(self) -> None:
        record = _aligned_record(checksum="sha256:" + "a" * 64)
        record = record.model_copy(update={"owner_id": "different-episode"})
        context = _make_context(aligned_record=record, artifact_bytes=b"{}")
        request = _make_request(context)

        with pytest.raises(AlignedArtifactNotFoundError):
            await ValidateAlignedEpisodeJobHandler().run(request)


class TestChecksumMismatch:
    @pytest.mark.asyncio
    async def test_record_checksum_mismatch_blocks_write(self) -> None:
        data = _artifact_bytes(valid=True)
        context = _make_context(
            aligned_record=_aligned_record(checksum="sha256:" + "0" * 64),
            artifact_bytes=data,
        )
        request = _make_request(context)

        with pytest.raises(AlignedArtifactChecksumMismatchError):
            await ValidateAlignedEpisodeJobHandler().run(request)

        context.artifact_record_store.register.assert_not_called()

    @pytest.mark.asyncio
    async def test_pinned_checksum_mismatch_blocks_write(self) -> None:
        data = _artifact_bytes(valid=True)
        context = _make_context(
            aligned_record=_aligned_record(checksum=None), artifact_bytes=data
        )
        request = _make_request(context, aligned_artifact_checksum="0" * 64)

        with pytest.raises(AlignedArtifactChecksumMismatchError):
            await ValidateAlignedEpisodeJobHandler().run(request)

        context.artifact_record_store.register.assert_not_called()


class TestWriteFailureLeavesNoRecord:
    @pytest.mark.asyncio
    async def test_report_write_failure_leaves_no_artifact_record(self) -> None:
        data = _artifact_bytes(valid=True)
        checksum = f"sha256:{hashlib.sha256(data).hexdigest()}"
        context = _make_context(
            aligned_record=_aligned_record(checksum=checksum),
            artifact_bytes=data,
            write_should_fail=True,
        )
        request = _make_request(context)

        with pytest.raises(RuntimeError):
            await ValidateAlignedEpisodeJobHandler().run(request)

        context.artifact_record_store.register.assert_not_called()
        context.commit.assert_not_called()


async def test_a_stored_artifact_of_an_earlier_schema_is_refused_and_writes_nothing() -> (
    None
):
    document = json.loads(_artifact_bytes())
    document["schemaVersion"] = "v1"
    del document["alignedEpisode"]["clockOrigin"]
    del document["alignedEpisode"]["clockScope"]
    data = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
    context = _make_context(
        aligned_record=_aligned_record(
            checksum="sha256:" + hashlib.sha256(data).hexdigest()
        ),
        artifact_bytes=data,
    )
    with pytest.raises(UnsupportedAlignedArtifactSchemaError, match="'v1'"):
        await resolve_and_verify_aligned_artifact(
            context,
            episode_id="ep-1",
            aligned_artifact_id="art-aligned-1",
            expected_checksum=None,
        )
    context.episode_artifact_store.write_aligned_episode_report.assert_not_awaited()


class TestDatasetVersionScope:
    """The aligned record names no DatasetVersion; the one a request names is
    checked against the revision the artifact's bytes say it was computed from."""

    def _context(self) -> MagicMock:
        data = _artifact_bytes()
        return _make_context(
            aligned_record=_aligned_record(
                checksum=f"sha256:{hashlib.sha256(data).hexdigest()}"
            ),
            artifact_bytes=data,
        )

    @pytest.mark.asyncio
    async def test_a_version_not_containing_the_episode_is_refused(self) -> None:
        context = self._context()
        with pytest.raises(AlignedArtifactNotEligibleError, match="not a member"):
            await ValidateAlignedEpisodeJobHandler().run(
                _make_request(context, dataset_id="d2", dataset_version="v9")
            )
        context.episode_artifact_store.write_aligned_episode_report.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_version_pinning_another_revision_is_refused(self) -> None:
        context = self._context()
        context.episode_store.get_member_revision = AsyncMock(
            return_value=SimpleNamespace(
                manifest_artifact_id="art-src-2",
                manifest_checksum="sha256:" + "b" * 64,
            )
        )
        with pytest.raises(AlignedArtifactNotEligibleError, match="computed from"):
            await ValidateAlignedEpisodeJobHandler().run(_make_request(context))
        context.episode_artifact_store.write_aligned_episode_report.assert_not_called()

    @pytest.mark.asyncio
    async def test_half_a_scope_is_refused(self) -> None:
        context = self._context()
        with pytest.raises(ValueError, match="both or neither"):
            await ValidateAlignedEpisodeJobHandler().run(
                _make_request(context, dataset_version=None)
            )

    @pytest.mark.asyncio
    async def test_no_scope_asks_about_the_artifact_alone(self) -> None:
        context = self._context()
        await ValidateAlignedEpisodeJobHandler().run(
            _make_request(context, dataset_id=None, dataset_version=None)
        )
        context.episode_store.get_member_revision.assert_not_called()
        registered = context.artifact_record_store.register.await_args.kwargs
        assert "dataset_id" not in registered and "dataset_version" not in registered

    @pytest.mark.asyncio
    async def test_a_registered_report_is_reused_without_a_write(self) -> None:
        context = self._context()
        await ValidateAlignedEpisodeJobHandler().run(_make_request(context))
        registered = context.artifact_record_store.register.await_args.kwargs
        report = registered["ref"]
        data = _artifact_bytes()
        aligned = _aligned_record(checksum=f"sha256:{hashlib.sha256(data).hexdigest()}")
        existing = ArtifactRecord(
            artifact_id=registered["artifact_id"],
            kind=report.kind.value,
            uri="mem://old/location.json",
            checksum=report.checksum,
            size_bytes=report.size_bytes,
        )
        stored = canonical_document_bytes(
            context.episode_artifact_store.write_aligned_episode_report.await_args.kwargs[
                "report"
            ]
        )

        second = self._context()
        second.artifact_record_store.get = AsyncMock(
            side_effect=lambda artifact_id: {
                aligned.artifact_id: aligned,
                existing.artifact_id: existing,
            }.get(artifact_id)
        )
        second.episode_artifact_store.read_aligned_episode_bytes = AsyncMock(
            side_effect=lambda uri: stored if uri == existing.uri else data
        )
        result = await ValidateAlignedEpisodeJobHandler().run(_make_request(second))

        assert result.report_uri == existing.uri
        second.episode_artifact_store.write_aligned_episode_report.assert_not_called()
        second.artifact_record_store.register.assert_not_called()
