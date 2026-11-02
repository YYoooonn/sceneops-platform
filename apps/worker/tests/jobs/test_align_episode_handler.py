"""AlignEpisodeJobHandler: pinned canonical Episodes -> one AlignedEpisodeArtifact
per Episode (derived, L3). The source is exactly the revision the EpisodeRecord
points to (or an explicitly pinned one); its bytes are verified before parsing,
and nothing is committed when anything fails."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from sceneops_core.artifacts.schemas import (
    ArtifactKind,
    ArtifactOwnerType,
    ArtifactRecord,
)
from sceneops_core.episodes.alignment import (
    TemporalAlignmentConfig,
    TemporalSourceContext,
)
from sceneops_core.jobs.schemas import AlignEpisodeJobParams, JobType
from sceneops_core.jobs.schemas.params import EpisodeAlignmentInput
from sceneops_core.common.checksums import sha256_checksum
from sceneops_episodes.artifacts import (
    EpisodeArtifactWriteResult,
    EpisodeManifestIntegrityError,
    canonical_document_bytes,
)
from sceneops_worker.jobs.base import JobHandlerRequest
from sceneops_worker.jobs.dataset._aligned_episode_resolution import (
    RegisteredArtifactIntegrityError,
)
from sceneops_worker.jobs.dataset.align_episode import (
    AlignEpisodeJobHandler,
    SourceManifestNotFoundError,
    SourceRevisionMismatchError,
    SourceRevisionNotMemberError,
)
from sceneops_worker.stores.artifacts import ArtifactRecordConflictError

sys.path.insert(0, str(Path(__file__).parents[1] / "episodes"))
from episode_context import job, make_context, register, sample_manifest  # noqa: E402

_CONFIG = TemporalAlignmentConfig(target_frequency_hz=1.0, tolerance_us=500_000)


def _write_result(**kwargs) -> EpisodeArtifactWriteResult:
    data = canonical_document_bytes(kwargs["artifact"])
    return EpisodeArtifactWriteResult(
        uri="mem://aligned.json", checksum=sha256_checksum(data), size_bytes=len(data)
    )


def _setup(*, write_fails=False, create_fails=False):
    record, artifact, data = register(sample_manifest())
    context = make_context([(record, artifact, data)])
    if write_fails:
        context.episode_artifact_store.write_aligned_episode = AsyncMock(
            side_effect=RuntimeError("storage backend unavailable")
        )
    else:
        context.episode_artifact_store.write_aligned_episode = AsyncMock(
            side_effect=_write_result
        )
    if create_fails:
        context.artifact_record_store.register = AsyncMock(
            side_effect=RuntimeError("db")
        )
    return record, artifact, data, context


def _request(
    context,
    episode_id,
    *,
    source_artifact_id=None,
    source_manifest_sha256=None,
    **overrides,
) -> JobHandlerRequest:
    params = AlignEpisodeJobParams(
        episodes=[
            EpisodeAlignmentInput(
                episode_id=episode_id,
                source_artifact_id=source_artifact_id,
                source_manifest_sha256=source_manifest_sha256,
            )
        ],
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

    [aligned_ref] = result.aligned
    assert aligned_ref.source_artifact_id == record.manifest_artifact_id
    assert aligned_ref.source_manifest_sha256 == hashlib.sha256(data).hexdigest()
    assert aligned_ref.source_checksum_verified is True
    written = context.artifact_record_store.register.await_args.kwargs["ref"]
    assert aligned_ref.aligned_artifact_checksum == written.checksum
    assert aligned_ref.step_count == 3  # window [0, 3s) on its own clock, 1 Hz
    assert result.step_count == 3 and result.episode_count == 1
    # The hand-off to EXPORT_LEARNING_DATA is exactly its input shape.
    assert result.export_inputs == [
        {
            "episode_id": record.episode_id,
            "aligned_artifact_id": aligned_ref.aligned_artifact_id,
            "aligned_artifact_checksum": aligned_ref.aligned_artifact_checksum,
        }
    ]
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
    assert result.aligned[0].source_artifact_id == artifact.artifact_id


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


async def test_pin_to_an_artifact_that_is_no_revision_of_the_episode_is_rejected() -> (
    None
):
    record, artifact, _, context = _setup()
    other = artifact.model_copy(
        update={"artifact_id": "art-other", "checksum": "sha256:" + "e" * 64}
    )
    context.artifact_record_store.get = AsyncMock(return_value=other)
    with pytest.raises(SourceManifestNotFoundError, match="not a registered revision"):
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
    with pytest.raises(ValueError, match="is not a member of"):
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
    registered = first.artifact_record_store.register.await_args.kwargs["ref"]
    assert _registered_id(first) == aligned_episode_artifact_id(
        episode_id=record.episode_id, checksum=registered.checksum
    )
    other = aligned_episode_artifact_id(
        episode_id=record.episode_id, checksum="sha256:" + "d" * 64
    )
    assert other != _registered_id(first)


async def test_the_artifact_names_no_dataset_version_and_the_named_one_is_checked() -> (
    None
):
    """The record is the Episode's, as its source manifest's is. Which
    DatasetVersion asked is a precondition and leaves no trace on the result."""
    record, _, _, context = _setup()
    await AlignEpisodeJobHandler().run(_request(context, record.episode_id))
    written = context.episode_artifact_store.write_aligned_episode.await_args.kwargs
    assert "dataset_id" not in written and "dataset_version" not in written
    registered = context.artifact_record_store.register.await_args.kwargs
    assert "dataset_id" not in registered and "dataset_version" not in registered
    assert registered["owner_id"] == record.episode_id

    # A DatasetVersion the Episode is not a member of is rejected.
    record, _, _, context = _setup()
    params = AlignEpisodeJobParams(
        episodes=[EpisodeAlignmentInput(episode_id=record.episode_id)],
        dataset_id="somewhere-else",
        dataset_version="v9",
        alignment_config=_CONFIG,
    )
    request = JobHandlerRequest(
        job=job(JobType.ALIGN_EPISODE, params.model_dump(mode="json")),
        params=params,
        context=context,
    )
    with pytest.raises(ValueError, match="is not a member of"):
        await AlignEpisodeJobHandler().run(request)
    context.episode_artifact_store.write_aligned_episode.assert_not_called()


async def test_a_pin_of_a_revision_the_version_does_not_pin_is_rejected() -> None:
    """A registered revision of the Episode, and not this DatasetVersion's: the
    shared result must not be reachable by naming another version's revision."""
    record, artifact, data, context = _setup()
    other = artifact.model_copy(update={"artifact_id": "art-other-revision"})
    context.artifact_record_store.get = AsyncMock(return_value=other)
    with pytest.raises(SourceRevisionNotMemberError, match="this DatasetVersion pins"):
        await AlignEpisodeJobHandler().run(
            _request(
                context,
                record.episode_id,
                source_artifact_id="art-other-revision",
                source_manifest_sha256=hashlib.sha256(data).hexdigest(),
            )
        )
    context.episode_artifact_store.write_aligned_episode.assert_not_called()
    context.artifact_record_store.register.assert_not_called()


def _stored_record(context, *, uri: str, **overrides):
    """What a first alignment leaves: ask for the same result and capture it."""
    ref = context.artifact_record_store.register.await_args.kwargs["ref"]
    return ArtifactRecord(
        **{
            "artifact_id": context.artifact_record_store.register.await_args.kwargs[
                "artifact_id"
            ],
            "kind": ref.kind.value,
            "uri": uri,
            "checksum": ref.checksum,
            "size_bytes": ref.size_bytes,
            "owner_type": ArtifactOwnerType.EPISODE.value,
            "owner_id": ref.metadata["episode_id"],
            **overrides,
        }
    )


async def _aligned_once():
    """The context of a second alignment, knowing the first one's record and bytes."""
    record, artifact, data, first = _setup()
    await AlignEpisodeJobHandler().run(_request(first, record.episode_id))
    written = first.episode_artifact_store.write_aligned_episode.await_args.kwargs
    bytes_ = canonical_document_bytes(written["artifact"])
    _, _, _, second = _setup()
    return record, artifact, first, second, bytes_


def _knows(context, artifact, aligned_record) -> None:
    context.artifact_record_store.get = AsyncMock(
        side_effect=lambda aid: {
            artifact.artifact_id: artifact,
            aligned_record.artifact_id: aligned_record,
        }.get(aid)
    )


async def test_a_registered_result_is_reused_at_its_own_location_without_a_write() -> (
    None
):
    """Its location is the record's, so one written where an earlier layout put it
    stays where it is: no second object, no conflict and no change to the record."""
    record, artifact, first, second, stored = await _aligned_once()
    old_uri = "mem://datasets/d/versions/v/episodes/e/aligned/a/b.json"
    existing = _stored_record(first, uri=old_uri)
    _knows(second, artifact, existing)
    second.episode_artifact_store.read_aligned_episode_bytes = AsyncMock(
        return_value=stored
    )

    result = await AlignEpisodeJobHandler().run(_request(second, record.episode_id))

    [ref] = result.aligned
    assert ref.aligned_artifact_id == existing.artifact_id
    assert ref.aligned_artifact_uri == old_uri
    assert ref.aligned_artifact_checksum == existing.checksum
    second.episode_artifact_store.write_aligned_episode.assert_not_called()
    second.artifact_record_store.register.assert_not_called()
    second.commit.assert_awaited_once()


@pytest.mark.parametrize("found", [None, b"other bytes"])
async def test_a_registered_result_that_lost_its_bytes_fails_and_writes_nothing(
    found,
) -> None:
    """A record whose object is gone or changed is corruption: reported, never
    repaired by writing over it and never answered with success."""
    record, artifact, first, second, _ = await _aligned_once()
    _knows(second, artifact, _stored_record(first, uri="mem://aligned.json"))
    second.episode_artifact_store.read_aligned_episode_bytes = AsyncMock(
        return_value=found
    )
    with pytest.raises(RegisteredArtifactIntegrityError):
        await AlignEpisodeJobHandler().run(_request(second, record.episode_id))
    second.episode_artifact_store.write_aligned_episode.assert_not_called()
    second.commit.assert_not_called()


@pytest.mark.parametrize(
    "overrides", [{"checksum": "sha256:" + "e" * 64}, {"size_bytes": 1}, {"kind": "x"}]
)
async def test_a_record_of_this_id_with_other_content_conflicts_before_any_write(
    overrides,
) -> None:
    record, artifact, first, second, _ = await _aligned_once()
    _knows(
        second, artifact, _stored_record(first, uri="mem://aligned.json", **overrides)
    )
    with pytest.raises(ArtifactRecordConflictError, match="refusing to reuse"):
        await AlignEpisodeJobHandler().run(_request(second, record.episode_id))
    second.episode_artifact_store.write_aligned_episode.assert_not_called()
    second.artifact_record_store.register.assert_not_called()


async def test_params_without_a_dataset_scope_are_rejected() -> None:
    """An Episode may be pinned by many DatasetVersions; which one is
    aligned is never inferred."""
    record, _, _, context = _setup()
    params = AlignEpisodeJobParams(
        episodes=[EpisodeAlignmentInput(episode_id=record.episode_id)],
        alignment_config=_CONFIG,
    )
    request = JobHandlerRequest(
        job=job(JobType.ALIGN_EPISODE, params.model_dump(mode="json")),
        params=params,
        context=context,
    )
    with pytest.raises(ValueError, match="dataset_id and dataset_version"):
        await AlignEpisodeJobHandler().run(request)


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


# ── several Episodes in one job ───────────────────────────────────────────────


def _setup_two():
    first = register(sample_manifest("first"))
    second = register(sample_manifest("second"))
    context = make_context([first, second])
    context.episode_artifact_store.write_aligned_episode = AsyncMock(
        side_effect=lambda **kw: EpisodeArtifactWriteResult(
            uri=f"mem://aligned/{kw['episode_id']}.json",
            checksum=sha256_checksum(canonical_document_bytes(kw["artifact"])),
            size_bytes=len(canonical_document_bytes(kw["artifact"])),
        )
    )
    return first[0], second[0], context


def _two_request(context, *episode_ids) -> JobHandlerRequest:
    params = AlignEpisodeJobParams(
        episodes=[EpisodeAlignmentInput(episode_id=e) for e in episode_ids],
        dataset_id="d1",
        dataset_version="v1",
        alignment_config=_CONFIG,
    )
    return JobHandlerRequest(
        job=job(JobType.ALIGN_EPISODE, params.model_dump(mode="json")),
        params=params,
        context=context,
    )


async def test_every_episode_is_aligned_and_committed_once() -> None:
    first, second, context = _setup_two()
    result = await AlignEpisodeJobHandler().run(
        _two_request(context, first.episode_id, second.episode_id)
    )

    assert [r.episode_id for r in result.aligned] == [
        first.episode_id,
        second.episode_id,
    ]
    assert [i["episode_id"] for i in result.export_inputs] == [
        first.episode_id,
        second.episode_id,
    ]
    assert result.episode_count == 2 and result.step_count == 6
    assert context.artifact_record_store.register.await_count == 2
    context.commit.assert_awaited_once()


async def test_records_are_taken_in_one_order_and_results_keep_the_requested_one() -> (
    None
):
    """Two Jobs sharing Episodes hold each record until they commit, so the order
    is what keeps them from deadlocking."""
    first, second, context = _setup_two()
    ordered = sorted([first.episode_id, second.episode_id])
    result = await AlignEpisodeJobHandler().run(
        _two_request(context, *reversed(ordered))
    )
    assert [r.episode_id for r in result.aligned] == list(reversed(ordered))
    assert [
        call.kwargs["owner_id"]
        for call in context.artifact_record_store.register.await_args_list
    ] == ordered


async def test_a_failing_episode_commits_nothing() -> None:
    first, _, context = _setup_two()
    with pytest.raises(ValueError, match="is not a member of"):
        await AlignEpisodeJobHandler().run(
            _two_request(context, first.episode_id, "episode-missing")
        )
    context.commit.assert_not_called()


def test_an_episode_may_be_named_only_once() -> None:
    with pytest.raises(ValueError, match="at most once"):
        AlignEpisodeJobParams(
            episodes=[
                EpisodeAlignmentInput(episode_id="e"),
                EpisodeAlignmentInput(episode_id="e"),
            ],
            alignment_config=_CONFIG,
        )


def test_a_pin_needs_both_the_artifact_and_the_checksum() -> None:
    with pytest.raises(ValueError, match="both"):
        EpisodeAlignmentInput(episode_id="e", source_artifact_id="a")
