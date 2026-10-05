"""AlignEpisodeJobHandler: canonical Episode revision -> AlignedEpisodeArtifact
(derived, L3). The source is exactly the revision the EpisodeRecord points to
(or an explicitly pinned one); its bytes are verified before parsing, and no
aligned artifact is written when anything fails."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from sceneops_core.artifacts.schemas import ArtifactKind, ArtifactOwnerType
from sceneops_core.episodes.alignment import (
    TemporalAlignmentConfig,
    TemporalSourceContext,
)
from sceneops_core.jobs.schemas import AlignEpisodeJobParams, JobType
from sceneops_worker.episodes.artifacts import (
    EpisodeArtifactWriteResult,
    EpisodeManifestIntegrityError,
)
from sceneops_worker.jobs.base import JobHandlerRequest
from sceneops_worker.jobs.dataset.align_episode import (
    AlignEpisodeJobHandler,
    SourceManifestNotFoundError,
    SourceRevisionMismatchError,
)

sys.path.insert(0, str(Path(__file__).parents[1] / "episodes"))
from episode_context import job, make_context, register, sample_manifest  # noqa: E402

_CONFIG = TemporalAlignmentConfig(target_frequency_hz=1.0, tolerance_us=500_000)


def _setup(*, write_fails=False, create_fails=False):
    record, artifact, data = register(sample_manifest())
    context = make_context([(record, artifact, data)])
    if write_fails:
        context.episode_artifact_store.write_aligned_episode = AsyncMock(
            side_effect=RuntimeError("storage backend unavailable")
        )
    else:
        context.episode_artifact_store.write_aligned_episode = AsyncMock(
            return_value=EpisodeArtifactWriteResult(
                uri="mem://aligned.json", checksum="sha256:" + "c" * 64, size_bytes=42
            )
        )
    if create_fails:
        context.artifact_record_store.register = AsyncMock(
            side_effect=RuntimeError("db")
        )
    return record, artifact, data, context


def _request(context, episode_id, **overrides) -> JobHandlerRequest:
    params = AlignEpisodeJobParams(
        episode_id=episode_id,
        dataset_id="d1",
        dataset_version="v1",
        alignment_config=_CONFIG,
        **overrides,
    )
    return JobHandlerRequest(
        job=job(JobType.ALIGN_EPISODE, params.model_dump(mode="json")),
        params=params,
        context=context,
    )


async def test_aligns_the_current_revision_and_records_it() -> None:
    record, artifact, data, context = _setup()
    result = await AlignEpisodeJobHandler().run(_request(context, record.episode_id))

    assert result.source_artifact_id == record.manifest_artifact_id
    assert result.source_manifest_sha256 == hashlib.sha256(data).hexdigest()
    assert result.source_checksum_verified is True
    assert result.step_count == 3  # window [0, 3s) on its own clock, 1 Hz
    context.artifact_record_store.register.assert_awaited_once()
    kwargs = context.artifact_record_store.register.await_args.kwargs
    assert kwargs["ref"].kind == ArtifactKind.ALIGNED_EPISODE_MANIFEST
    assert kwargs["owner_type"] == ArtifactOwnerType.EPISODE
    written = context.episode_artifact_store.write_aligned_episode.await_args.kwargs
    aligned = written["artifact"].aligned_episode
    assert aligned.episode_id == record.episode_id
    assert aligned.source_clock == "sensor.header_stamp"  # the window clock
    context.commit.assert_awaited_once()


async def test_pinned_source_revision_is_used_verbatim() -> None:
    record, artifact, data, context = _setup()
    result = await AlignEpisodeJobHandler().run(
        _request(
            context,
            record.episode_id,
            source_artifact_id=artifact.artifact_id,
            source_manifest_sha256=hashlib.sha256(data).hexdigest(),
        )
    )
    assert result.source_artifact_id == artifact.artifact_id


async def test_pinned_checksum_mismatch_blocks_write() -> None:
    record, artifact, _, context = _setup()
    with pytest.raises(SourceRevisionMismatchError):
        await AlignEpisodeJobHandler().run(
            _request(
                context,
                record.episode_id,
                source_artifact_id=artifact.artifact_id,
                source_manifest_sha256="f" * 64,
            )
        )
    context.artifact_record_store.register.assert_not_called()


async def test_pin_to_an_artifact_of_another_episode_is_rejected() -> None:
    record, artifact, _, context = _setup()
    context.artifact_record_store.get = AsyncMock(
        return_value=artifact.model_copy(update={"owner_id": "episode-other"})
    )
    with pytest.raises(SourceManifestNotFoundError):
        await AlignEpisodeJobHandler().run(_request(context, record.episode_id))
    context.episode_artifact_store.write_aligned_episode.assert_not_called()


async def test_missing_or_changed_bytes_write_nothing() -> None:
    record, artifact, _, context = _setup()
    context.blobs[artifact.uri] = b"{}"
    with pytest.raises(EpisodeManifestIntegrityError):
        await AlignEpisodeJobHandler().run(_request(context, record.episode_id))
    context.artifact_record_store.register.assert_not_called()


async def test_unregistered_episode_raises() -> None:
    _, _, _, context = _setup()
    with pytest.raises(ValueError, match="Episode not found"):
        await AlignEpisodeJobHandler().run(_request(context, "episode-missing"))


async def test_alignment_failure_writes_nothing() -> None:
    record, _, _, context = _setup()
    with pytest.raises(Exception):  # ClockMismatchError: no stream is on this clock
        await AlignEpisodeJobHandler().run(
            _request(
                context,
                record.episode_id,
                source_context=TemporalSourceContext(source_clock="other.clock"),
            )
        )
    context.episode_artifact_store.write_aligned_episode.assert_not_called()
    context.artifact_record_store.register.assert_not_called()


async def test_write_or_record_failure_does_not_commit() -> None:
    record, _, _, context = _setup(write_fails=True)
    with pytest.raises(RuntimeError):
        await AlignEpisodeJobHandler().run(_request(context, record.episode_id))
    context.artifact_record_store.register.assert_not_called()
    context.commit.assert_not_called()

    record, _, _, context = _setup(create_fails=True)
    with pytest.raises(RuntimeError):
        await AlignEpisodeJobHandler().run(_request(context, record.episode_id))
    context.commit.assert_not_called()


# ── identity (ADR-007 §33.6) ──────────────────────────────────────────────────


def _registered_id(context) -> str:
    return context.artifact_record_store.register.await_args.kwargs["artifact_id"]


async def test_the_aligned_artifact_id_is_deterministic_per_revision() -> None:
    from sceneops_core.common.derived_ids import aligned_episode_artifact_id

    record, _, _, first = _setup()
    await AlignEpisodeJobHandler().run(_request(first, record.episode_id))
    _, _, _, second = _setup()
    await AlignEpisodeJobHandler().run(_request(second, record.episode_id))

    # The same inputs reach the same ArtifactRecord id, so a retry registers
    # the same record instead of a duplicate under a random id.
    assert _registered_id(first) == _registered_id(second)
    assert _registered_id(first) == aligned_episode_artifact_id(
        episode_id=record.episode_id, checksum="sha256:" + "c" * 64
    )
    other = aligned_episode_artifact_id(
        episode_id=record.episode_id, checksum="sha256:" + "d" * 64
    )
    assert other != _registered_id(first)


async def test_the_scope_is_the_episodes_dataset_version_never_a_default() -> None:
    record, _, _, context = _setup()
    await AlignEpisodeJobHandler().run(_request(context, record.episode_id))
    written = context.episode_artifact_store.write_aligned_episode.await_args.kwargs
    assert (written["dataset_id"], written["dataset_version"]) == (
        record.dataset_id,
        record.dataset_version,
    )

    # Params that name another DatasetVersion are rejected.
    record, _, _, context = _setup()
    params = AlignEpisodeJobParams(
        episode_id=record.episode_id,
        dataset_id="somewhere-else",
        dataset_version="v9",
        alignment_config=_CONFIG,
    )
    request = JobHandlerRequest(
        job=job(JobType.ALIGN_EPISODE, params.model_dump(mode="json")),
        params=params,
        context=context,
    )
    with pytest.raises(ValueError, match="belongs to"):
        await AlignEpisodeJobHandler().run(request)
    context.episode_artifact_store.write_aligned_episode.assert_not_called()


async def test_params_without_a_dataset_scope_are_accepted() -> None:
    record, _, _, context = _setup()
    params = AlignEpisodeJobParams(
        episode_id=record.episode_id, alignment_config=_CONFIG
    )
    request = JobHandlerRequest(
        job=job(JobType.ALIGN_EPISODE, params.model_dump(mode="json")),
        params=params,
        context=context,
    )
    result = await AlignEpisodeJobHandler().run(request)
    assert result.step_count == 3


async def test_the_alignment_key_includes_the_effective_clock() -> None:
    from sceneops_core.episodes.alignment import alignment_key

    record, _, _, context = _setup()
    await AlignEpisodeJobHandler().run(_request(context, record.episode_id))
    written = context.episode_artifact_store.write_aligned_episode.await_args.kwargs
    aligned = written["artifact"].aligned_episode
    assert written["alignment_key"] == alignment_key(
        _CONFIG, aligned.alignment_semantics_version, aligned.source_clock
    )
    assert written["alignment_key"] != alignment_key(
        _CONFIG, aligned.alignment_semantics_version, "another.clock"
    )
