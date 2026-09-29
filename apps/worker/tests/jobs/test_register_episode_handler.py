"""Tests for RegisterEpisodeJobHandler (SceneOps V2 Request 15).

Confirms register_episode no longer registers an ArtifactRecord for the
manifest — that's build_episodes' job now, since it's the one that actually
writes the manifest file and holds the producing job_id/pipeline_run_id.
register_episode stays a pure EpisodeRecord consumer: read manifest, upsert
record, respect replace_existing.
"""

from __future__ import annotations

from datetime import datetime, timezone
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
from sceneops_worker.jobs.dataset.register_episode import (
    RegisterEpisodeJobHandler,
    _us_to_datetime,
)

_MANIFEST_URI = "mem://episodes/ep-1.json"


def _manifest(episode_id: str = "ep-1", **overrides) -> EpisodeManifest:
    defaults = dict(
        episode_id=episode_id,
        dataset_id="d1",
        dataset_version="v1",
        lineage=EpisodeLineage(raw_log_id="rl1", mission_id="m1"),
        outcome=EpisodeOutcome.SUCCESS,
        frame_count=3,
    )
    defaults.update(overrides)
    return EpisodeManifest(**defaults)


def _make_context(
    *,
    manifest: EpisodeManifest | None,
    existing: EpisodeRecord | None,
    episode_count: int = 1,
) -> MagicMock:
    context = MagicMock()
    context.episode_artifact_store.load_episode_manifest = AsyncMock(
        return_value=manifest
    )
    context.episode_store.get = AsyncMock(return_value=existing)
    context.episode_store.upsert = AsyncMock()
    context.episode_store.count = AsyncMock(return_value=episode_count)
    context.dataset_store.lock_version_for_update = AsyncMock()
    context.dataset_store.update_episode_summary = AsyncMock()
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


class TestRegisterEpisodeUpdatesDatasetVersionSummary:
    """register_episode, not build_episodes, is the sole writer of
    DatasetVersionRecord.episode.episode_count (see register_episode.py's
    own docstring) -- and it must always write a freshly recomputed live
    count, never this dispatch's own registered_episode_count."""

    @pytest.mark.asyncio
    async def test_writes_live_recomputed_count_not_registered_count(self) -> None:
        # This dispatch only registers 1 episode, but the live repository
        # already has 3 (as if 2 prior independent dispatches had already
        # registered their own episodes for the same DatasetVersion) --
        # the summary must reflect the live 3, not this call's own 1.
        context = _make_context(manifest=_manifest(), existing=None, episode_count=3)
        request = _make_request(context)

        await RegisterEpisodeJobHandler().run(request)

        context.dataset_store.lock_version_for_update.assert_awaited_once_with(
            dataset_id="d1", version="v1"
        )
        context.episode_store.count.assert_awaited_once_with(
            dataset_id="d1", dataset_version="v1"
        )
        context.dataset_store.update_episode_summary.assert_awaited_once_with(
            dataset_id="d1", version="v1", episode_count=3
        )

    @pytest.mark.asyncio
    async def test_locks_before_counting_and_writing(self) -> None:
        """The row lock must be acquired before the count query and before
        the summary write -- that ordering (not just "all three calls
        happened") is the actual serialization boundary against a
        concurrent dispatch."""
        context = _make_context(manifest=_manifest(), existing=None, episode_count=1)
        request = _make_request(context)

        call_order: list[str] = []
        context.dataset_store.lock_version_for_update = AsyncMock(
            side_effect=lambda **_: call_order.append("lock")
        )
        context.episode_store.count = AsyncMock(
            side_effect=lambda **_: call_order.append("count") or 1
        )
        context.dataset_store.update_episode_summary = AsyncMock(
            side_effect=lambda **_: call_order.append("update")
        )

        await RegisterEpisodeJobHandler().run(request)

        assert call_order == ["lock", "count", "update"]

    @pytest.mark.asyncio
    async def test_retry_of_already_registered_episode_still_recomputes(self) -> None:
        """Retrying/upserting an already-registered episode (existing is not
        None, replace_existing=True) must still refresh the summary from a
        live count -- not skip the write just because upsert() ran again on
        the same row."""
        existing = EpisodeRecord(episode_id="ep-1", status=EpisodeStatus.REGISTERED)
        context = _make_context(
            manifest=_manifest(), existing=existing, episode_count=3
        )
        request = _make_request(context, replace_existing=True)

        await RegisterEpisodeJobHandler().run(request)

        context.dataset_store.update_episode_summary.assert_awaited_once_with(
            dataset_id="d1", version="v1", episode_count=3
        )

    @pytest.mark.asyncio
    async def test_skipped_existing_without_replace_still_recomputes(self) -> None:
        """Even when replace_existing=False skips the upsert entirely, the
        summary must still be refreshed from a live count -- an earlier,
        unrelated dispatch may have left it stale."""
        existing = EpisodeRecord(episode_id="ep-1", status=EpisodeStatus.REGISTERED)
        context = _make_context(
            manifest=_manifest(), existing=existing, episode_count=5
        )
        request = _make_request(context, replace_existing=False)

        await RegisterEpisodeJobHandler().run(request)

        context.episode_store.upsert.assert_not_called()
        context.dataset_store.update_episode_summary.assert_awaited_once_with(
            dataset_id="d1", version="v1", episode_count=5
        )

    @pytest.mark.asyncio
    async def test_missing_manifest_never_touches_summary(self) -> None:
        """No manifest resolved -> nothing was registered -> no
        (dataset_id, dataset_version) pair was touched -> no summary write."""
        context = _make_context(manifest=None, existing=None)
        request = _make_request(context)

        await RegisterEpisodeJobHandler().run(request)

        context.dataset_store.lock_version_for_update.assert_not_called()
        context.episode_store.count.assert_not_called()
        context.dataset_store.update_episode_summary.assert_not_called()

    @pytest.mark.asyncio
    async def test_never_touches_scene_summary(self) -> None:
        """Scene/Episode summary independence (SceneVersionSummary is
        completely untouched by an Episode-domain write)."""
        context = _make_context(manifest=_manifest(), existing=None)
        request = _make_request(context)

        await RegisterEpisodeJobHandler().run(request)

        assert not [
            call
            for call in context.dataset_store.method_calls
            if call[0] == "update_scene_summary"
        ]


class TestUsToDatetimeConversion:
    """SceneOps V2 Phase 2 Request 2.1A: timestamp_us -> UTC datetime contract."""

    def test_none_maps_to_none(self) -> None:
        assert _us_to_datetime(None) is None

    def test_epoch_zero_maps_to_epoch(self) -> None:
        assert _us_to_datetime(0) == datetime(1970, 1, 1, tzinfo=timezone.utc)

    def test_preserves_microsecond_precision(self) -> None:
        # 2026-01-01T00:00:01.234567Z, chosen to be far enough past the epoch
        # that a naive float (timestamp_us / 1e6) division could plausibly
        # lose sub-microsecond precision -- integer arithmetic must not.
        timestamp_us = 1_767_225_601_234_567
        result = _us_to_datetime(timestamp_us)
        assert result is not None
        assert result.tzinfo is timezone.utc
        assert result.microsecond == 234_567
        # Round-trip check: converting back to microseconds-since-epoch must
        # exactly reproduce the input.
        delta = result - datetime(1970, 1, 1, tzinfo=timezone.utc)
        assert (
            delta.days * 86_400_000_000
            + delta.seconds * 1_000_000
            + (delta.microseconds)
            == timestamp_us
        )


class TestRegisterEpisodePopulatesTimeBounds:
    """SceneOps V2 Phase 2 Request 2.1A.

    register_episode previously dropped EpisodeManifest.start_timestamp_us/
    end_timestamp_us on the floor -- every persisted EpisodeRecord had
    started_at/ended_at = NULL regardless of manifest content. These tests
    prove the manifest -> record projection now happens.
    """

    @pytest.mark.asyncio
    async def test_manifest_bounds_convert_to_utc_datetime(self) -> None:
        manifest = _manifest(
            start_timestamp_us=1_767_225_600_000_000,
            end_timestamp_us=1_767_225_610_500_000,
        )
        context = _make_context(manifest=manifest, existing=None)
        request = _make_request(context)

        await RegisterEpisodeJobHandler().run(request)

        upserted: EpisodeRecord = context.episode_store.upsert.await_args.args[0]
        assert upserted.started_at == _us_to_datetime(manifest.start_timestamp_us)
        assert upserted.ended_at == _us_to_datetime(manifest.end_timestamp_us)
        assert upserted.started_at.tzinfo is timezone.utc
        assert upserted.ended_at.tzinfo is timezone.utc
        # Sub-second precision survives the round trip.
        assert upserted.ended_at.microsecond == 500_000

    @pytest.mark.asyncio
    async def test_missing_manifest_bounds_leave_record_fields_none(self) -> None:
        manifest = _manifest(start_timestamp_us=None, end_timestamp_us=None)
        context = _make_context(manifest=manifest, existing=None)
        request = _make_request(context)

        await RegisterEpisodeJobHandler().run(request)

        upserted: EpisodeRecord = context.episode_store.upsert.await_args.args[0]
        assert upserted.started_at is None
        assert upserted.ended_at is None

    @pytest.mark.asyncio
    async def test_mixed_bounds_are_preserved_independently(self) -> None:
        """Schema permits start/end to be independently optional -- confirm
        registration doesn't fabricate the missing side from the present one."""
        manifest = _manifest(
            start_timestamp_us=1_767_225_600_000_000, end_timestamp_us=None
        )
        context = _make_context(manifest=manifest, existing=None)
        request = _make_request(context)

        await RegisterEpisodeJobHandler().run(request)

        upserted: EpisodeRecord = context.episode_store.upsert.await_args.args[0]
        assert upserted.started_at is not None
        assert upserted.ended_at is None

    @pytest.mark.asyncio
    async def test_re_registration_updates_time_bounds(self) -> None:
        """replace_existing=True upserts again, so a previously-NULL row
        (registered before this fix, or with a stale manifest) becomes
        consistent with the manifest's current bounds without a backfill job."""
        existing = EpisodeRecord(
            episode_id="ep-1", status=EpisodeStatus.REGISTERED, started_at=None
        )
        manifest = _manifest(
            start_timestamp_us=1_767_225_600_000_000,
            end_timestamp_us=1_767_225_610_000_000,
        )
        context = _make_context(manifest=manifest, existing=existing)
        request = _make_request(context, replace_existing=True)

        await RegisterEpisodeJobHandler().run(request)

        context.episode_store.upsert.assert_awaited_once()
        upserted: EpisodeRecord = context.episode_store.upsert.await_args.args[0]
        assert upserted.started_at is not None
        assert upserted.ended_at is not None
