"""Tests for RegisterEpisodeJobHandler (SceneOps V2 Request 15).

Confirms register_episode no longer registers an ArtifactRecord for the
manifest — that's build_episodes' job now, since it's the one that actually
writes the manifest file and holds the producing job_id/pipeline_run_id.
register_episode stays a pure EpisodeRecord consumer: read manifest, upsert
record, respect replace_existing.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from sceneops_core.episodes.schemas import (
    EpisodeLineage,
    EpisodeManifest,
    EpisodeOutcome,
    EpisodeRecord,
    EpisodeStatus,
)
from sceneops_core.jobs.schemas import (
    JobManifest,
    JobStatus,
    JobType,
    RegisterEpisodeJobParams,
)
from sceneops_worker.jobs.base import JobHandlerRequest
from sceneops_worker.jobs.dataset.register_episode import RegisterEpisodeJobHandler

_MANIFEST_URI = "mem://episodes/ep-1.json"


def _manifest(episode_id: str = "ep-1") -> EpisodeManifest:
    return EpisodeManifest(
        episode_id=episode_id,
        dataset_id="d1",
        dataset_version="v1",
        lineage=EpisodeLineage(raw_log_id="rl1", mission_id="m1"),
        outcome=EpisodeOutcome.SUCCESS,
        frame_count=3,
    )


def _make_context(
    *, manifest: EpisodeManifest | None, existing: EpisodeRecord | None
) -> MagicMock:
    context = MagicMock()
    context.episode_artifact_store.load_episode_manifest = AsyncMock(
        return_value=manifest
    )
    context.episode_store.get = AsyncMock(return_value=existing)
    context.episode_store.upsert = AsyncMock()
    context.artifact_record_store.create = AsyncMock()
    context.commit = AsyncMock()
    return context


def _make_request(
    context: MagicMock, *, replace_existing: bool = False
) -> JobHandlerRequest:
    job = JobManifest(
        job_id="job-register-1",
        type=JobType.REGISTER_EPISODE,
        status=JobStatus.RUNNING,
        pipeline_run_id="pipe-1",
    )
    params = RegisterEpisodeJobParams(
        dataset_id="d1",
        dataset_version="v1",
        episode_manifest_uris=[_MANIFEST_URI],
        replace_existing=replace_existing,
    )
    return JobHandlerRequest(job=job, params=params, context=context)


class TestRegisterEpisodeDoesNotRegisterArtifacts:
    @pytest.mark.asyncio
    async def test_fresh_registration_never_creates_artifact_record(self) -> None:
        context = _make_context(manifest=_manifest(), existing=None)
        request = _make_request(context)

        result = await RegisterEpisodeJobHandler().run(request)

        assert result.registered_episode_count == 1
        assert result.episode_ids == ["ep-1"]
        context.episode_store.upsert.assert_awaited_once()
        context.artifact_record_store.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_existing_without_replace_existing_skips_upsert_and_artifact(
        self,
    ) -> None:
        existing = EpisodeRecord(episode_id="ep-1", status=EpisodeStatus.REGISTERED)
        context = _make_context(manifest=_manifest(), existing=existing)
        request = _make_request(context, replace_existing=False)

        result = await RegisterEpisodeJobHandler().run(request)

        assert (
            result.registered_episode_count == 1
        )  # still counted, per existing semantics
        context.episode_store.upsert.assert_not_called()
        context.artifact_record_store.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_replace_existing_upserts_again_without_new_artifact(self) -> None:
        existing = EpisodeRecord(episode_id="ep-1", status=EpisodeStatus.REGISTERED)
        context = _make_context(manifest=_manifest(), existing=existing)
        request = _make_request(context, replace_existing=True)

        result = await RegisterEpisodeJobHandler().run(request)

        assert result.registered_episode_count == 1
        context.episode_store.upsert.assert_awaited_once()
        context.artifact_record_store.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_repeated_registration_is_idempotent_on_artifact_count(self) -> None:
        """Registering the same manifest URI twice (replace_existing=True both
        times) must never touch artifact_record_store at all — verifies the
        'register once / register again' idempotency scenario end to end."""
        context = _make_context(manifest=_manifest(), existing=None)

        first = await RegisterEpisodeJobHandler().run(
            _make_request(context, replace_existing=True)
        )
        # Second call sees what the first one just upserted.
        context.episode_store.get = AsyncMock(
            return_value=EpisodeRecord(
                episode_id="ep-1", status=EpisodeStatus.REGISTERED
            )
        )
        second = await RegisterEpisodeJobHandler().run(
            _make_request(context, replace_existing=True)
        )

        assert first.registered_episode_count == 1
        assert second.registered_episode_count == 1
        context.artifact_record_store.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_manifest_is_skipped(self) -> None:
        context = _make_context(manifest=None, existing=None)
        request = _make_request(context)

        result = await RegisterEpisodeJobHandler().run(request)

        assert result.registered_episode_count == 0
        context.episode_store.upsert.assert_not_called()
        context.artifact_record_store.create.assert_not_called()
