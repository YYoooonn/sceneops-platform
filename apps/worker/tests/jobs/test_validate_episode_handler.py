"""ValidateEpisodeJobHandler: registered Episodes validated at the revision
their record points to; each per-episode run record pins it."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from sceneops_core.artifacts.schemas.owner import ArtifactOwnerType
from sceneops_core.episodes.schemas import EpisodeValidationRunRecord
from sceneops_core.jobs.schemas import JobType, ValidateEpisodeJobParams
from sceneops_worker.episodes.resolver import (
    EpisodeNotRegisteredError,
    InconsistentEpisodeStateError,
)
from sceneops_worker.jobs.base import JobHandlerRequest
from sceneops_worker.jobs.dataset.validate_episode import ValidateEpisodeJobHandler

sys.path.insert(0, str(Path(__file__).parents[1] / "episodes"))
from episode_context import job, make_context, register, sample_manifest  # noqa: E402


def _request(episode_ids, context) -> JobHandlerRequest:
    params = ValidateEpisodeJobParams(
        dataset_id="d1", dataset_version="v1", episode_ids=episode_ids
    )
    return JobHandlerRequest(
        job=job(JobType.VALIDATE_EPISODE, params.model_dump()),
        params=params,
        context=context,
    )


async def test_registered_episode_is_validated_at_its_pinned_revision() -> None:
    record, artifact, data = register(sample_manifest())
    context = make_context([(record, artifact, data)])

    result = await ValidateEpisodeJobHandler().run(
        _request([record.episode_id], context)
    )

    assert result.status == "ready"
    assert result.should_block_pipeline is False
    assert result.checked_episode_count == 1
    per_episode = [
        call.args[0]
        for call in context.runs.episode_runs.upsert.await_args_list
        if isinstance(call.args[0], EpisodeValidationRunRecord)
        and call.args[0].episode_id is not None
    ]
    assert len(per_episode) == 1
    assert per_episode[0].assessed(
        manifest_artifact_id=record.manifest_artifact_id,
        manifest_checksum=record.manifest_checksum,
    )
    registered = context.artifact_record_store.register.await_args_list
    assert registered  # the per-episode report and the run report
    for call in registered:
        assert call.kwargs["owner_type"] == ArtifactOwnerType.EPISODE_VALIDATION_RUN
    context.scene_store.get.assert_not_called()


async def test_record_that_disagrees_with_its_manifest_blocks() -> None:
    record, artifact, data = register(sample_manifest())
    tampered = record.model_copy(update={"action_count": 99})
    context = make_context([(tampered, artifact, data)])
    result = await ValidateEpisodeJobHandler().run(
        _request([record.episode_id], context)
    )
    assert result.status == "failed" and result.should_block_pipeline


async def test_unregistered_episode_fails_the_job() -> None:
    context = make_context([])
    with pytest.raises(EpisodeNotRegisteredError):
        await ValidateEpisodeJobHandler().run(_request(["episode-missing"], context))


async def test_record_pointing_at_another_revision_is_reported_not_repaired() -> None:
    record, artifact, data = register(sample_manifest())
    stale = record.model_copy(update={"manifest_checksum": "sha256:" + "9" * 64})
    context = make_context([(stale, artifact, data)])
    with pytest.raises(InconsistentEpisodeStateError):
        await ValidateEpisodeJobHandler().run(_request([record.episode_id], context))


async def test_no_episode_ids_blocks() -> None:
    result = await ValidateEpisodeJobHandler().run(_request([], make_context([])))
    assert result.should_block_pipeline is True
    assert result.checked_episode_count == 0
