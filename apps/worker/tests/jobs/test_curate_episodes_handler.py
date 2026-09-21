"""Tests for CurateEpisodesJobHandler (SceneOps V2 Request 2.6).

Mocks WorkerContext the same way test_export_learning_data_handler.py does.
The module-level AlignedEpisodeValidator/AlignedEpisodeProfiler singletons
are monkeypatched with canned per-episode results so each scenario's
profile metrics are exact and deterministic -- alignment-engine/profiler/
validator behavior itself is already covered by
test_alignment_{validation,profiling}.py; this file only needs to prove
CurateEpisodesJobHandler's own resolution/evaluation/persistence
orchestration.
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
    AlignedEpisodeProfile,
    AlignedEpisodeValidationReport,
    EpisodeSourceRevision,
    TemporalAlignmentConfig,
    TemporalSourceContext,
    align_episode,
)
from sceneops_core.episodes.curation import CurationPolicy
from sceneops_core.episodes.learning_export import (
    AlignedArtifactRevision,
    LearningDataExportManifest,
)
from sceneops_core.episodes.schemas import (
    EpisodeActionFrame,
    EpisodeManifest,
    EpisodeObservationFrame,
)
from sceneops_core.episodes.schemas.enums import EpisodeOutcome
from sceneops_core.jobs.schemas import (
    CurateEpisodesJobParams,
    JobManifest,
    JobStatus,
    JobType,
)
from sceneops_worker.jobs.base import JobHandlerRequest
from sceneops_worker.jobs.dataset import curate_episodes as curate_episodes_module
from sceneops_worker.jobs.dataset._learning_export_resolution import (
    LearningExportManifestChecksumMismatchError,
)
from sceneops_worker.jobs.dataset.curate_episodes import CurateEpisodesJobHandler


def _checksum(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _artifact_bytes(episode_id: str, *, task: str = "pick") -> bytes:
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
        task=task,
        outcome=EpisodeOutcome.SUCCESS,
    )
    config = TemporalAlignmentConfig(target_frequency_hz=1.0, tolerance_us=500_000)
    ctx = TemporalSourceContext(source_clock=MCAP_LOG_TIME_CLOCK)
    aligned = align_episode(manifest, config, ctx)

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


def _export_manifest_bytes(inputs: list[AlignedArtifactRevision]) -> bytes:
    manifest = LearningDataExportManifest(
        export_id="export-1",
        dataset_id="d1",
        dataset_version="v1",
        inputs=inputs,
    )
    return json.dumps(
        manifest.to_artifact_dict(), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _export_manifest_record(
    *, artifact_id: str, checksum: str | None, uri: str
) -> ArtifactRecord:
    return ArtifactRecord(
        artifact_id=artifact_id,
        kind=ArtifactKind.LEARNING_DATA_EXPORT_MANIFEST.value,
        uri=uri,
        owner_type=ArtifactOwnerType.DATASET_VERSION.value,
        owner_id="d1:v1",
        checksum=checksum,
    )


def _profile(
    *, episode_id: str, overall_missing_ratio: float = 0.0
) -> AlignedEpisodeProfile:
    return AlignedEpisodeProfile(
        episode_id=episode_id,
        step_count=2,
        duration_us=1_000_000,
        target_frequency_hz=1.0,
        achieved_frequency_hz=1.0,
        frequency_error_hz=0.0,
        frequency_error_ratio=0.0,
        observation_channel_count=1,
        action_channel_count=1,
        duplicate_discarded_count=0,
        channel_profiles=[],
        overall_missing_ratio=overall_missing_ratio,
        max_channel_missing_ratio=overall_missing_ratio,
        max_abs_sync_delta_us=0,
    )


def _make_context(
    *,
    records_by_artifact_id: dict[str, ArtifactRecord | None],
    bytes_by_uri: dict[str, bytes | None],
    profiles_by_episode: dict[str, AlignedEpisodeProfile] | None = None,
) -> MagicMock:
    context = MagicMock()

    async def _get(artifact_id: str):
        return records_by_artifact_id.get(artifact_id)

    async def _read_aligned_bytes(uri: str):
        return bytes_by_uri.get(uri)

    async def _read_export_bytes(uri: str):
        return bytes_by_uri.get(uri)

    context.artifact_record_store.get = AsyncMock(side_effect=_get)
    context.episode_artifact_store.read_aligned_episode_bytes = AsyncMock(
        side_effect=_read_aligned_bytes
    )
    context.analytics_writer.read_learning_export_manifest_bytes = AsyncMock(
        side_effect=_read_export_bytes
    )

    async def _write_curation_manifest(manifest, **kwargs):
        return AnalyticsTableWriteResult(
            uri=f"mem://curation/{kwargs['curation_id']}/manifest.json",
            checksum="sha256:" + "9" * 64,
            size_bytes=99,
        )

    context.analytics_writer.write_curation_manifest = AsyncMock(
        side_effect=_write_curation_manifest
    )
    context.artifact_record_store.create = AsyncMock()
    context.commit = AsyncMock()

    if profiles_by_episode is not None:
        context._profiles_by_episode = profiles_by_episode

    return context


def _make_request(context: MagicMock, **param_overrides) -> JobHandlerRequest:
    job = JobManifest(
        job_id="job-curate-1",
        type=JobType.CURATE_EPISODES,
        status=JobStatus.RUNNING,
    )
    defaults = dict(
        dataset_id="d1",
        dataset_version="v1",
        learning_data_export_manifest_artifact_id="art-export-1",
        policy=CurationPolicy(max_overall_missing_ratio=0.1),
    )
    defaults.update(param_overrides)
    params = CurateEpisodesJobParams(**defaults)
    return JobHandlerRequest(job=job, params=params, context=context)


@pytest.fixture
def patched_validator_and_profiler(monkeypatch):
    """Stubs validate()/profile() to key purely off episode_id, keeping
    profile metrics exact and independent of the real alignment engine's
    output for the scenarios below."""

    profiles: dict[str, AlignedEpisodeProfile] = {}

    def _validate(artifact):
        return AlignedEpisodeValidationReport(
            episode_id=artifact.aligned_episode.episode_id, valid=True
        )

    def _profile_fn(artifact):
        return profiles[artifact.aligned_episode.episode_id]

    fake_validator = MagicMock()
    fake_validator.validate.side_effect = _validate
    fake_profiler = MagicMock()
    fake_profiler.profile.side_effect = _profile_fn

    monkeypatch.setattr(curate_episodes_module, "_validator", fake_validator)
    monkeypatch.setattr(curate_episodes_module, "_profiler", fake_profiler)

    return profiles


class TestSelectedAndRejected:
    @pytest.mark.asyncio
    async def test_one_selected_one_rejected(
        self, patched_validator_and_profiler
    ) -> None:
        profiles = patched_validator_and_profiler
        profiles["ep-1"] = _profile(episode_id="ep-1", overall_missing_ratio=0.05)
        profiles["ep-2"] = _profile(episode_id="ep-2", overall_missing_ratio=0.5)

        data1 = _artifact_bytes("ep-1")
        data2 = _artifact_bytes("ep-2")
        uri1 = "mem://episodes/ep-1/aligned/x.json"
        uri2 = "mem://episodes/ep-2/aligned/x.json"

        inputs = [
            AlignedArtifactRevision(
                episode_id="ep-1",
                aligned_artifact_id="art-aligned-1",
                aligned_artifact_checksum=_checksum(data1).removeprefix("sha256:"),
            ),
            AlignedArtifactRevision(
                episode_id="ep-2",
                aligned_artifact_id="art-aligned-2",
                aligned_artifact_checksum=_checksum(data2).removeprefix("sha256:"),
            ),
        ]
        export_bytes = _export_manifest_bytes(inputs)
        export_uri = "mem://learning/export-1/manifest.json"

        context = _make_context(
            records_by_artifact_id={
                "art-export-1": _export_manifest_record(
                    artifact_id="art-export-1",
                    checksum=_checksum(export_bytes),
                    uri=export_uri,
                ),
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
            bytes_by_uri={export_uri: export_bytes, uri1: data1, uri2: data2},
        )
        request = _make_request(context)

        result = await CurateEpisodesJobHandler().run(request)

        assert result.candidate_count == 2
        assert result.selected_count == 1
        assert result.rejected_count == 1
        assert result.manifest_artifact_id is not None
        context.commit.assert_awaited_once()

        create_call = context.artifact_record_store.create.await_args
        assert create_call.kwargs["ref"].kind == ArtifactKind.EPISODE_CURATION_MANIFEST
        assert create_call.kwargs["owner_type"] == ArtifactOwnerType.DATASET_VERSION
        assert create_call.kwargs["owner_id"] == "d1:v1"

        written_manifest = context.analytics_writer.write_curation_manifest.await_args[
            0
        ][0]
        assert written_manifest.candidates.total == 2
        assert written_manifest.candidates.selected == 1
        assert written_manifest.candidates.rejected == 1
        decisions = written_manifest.decisions
        # deterministic ordering by (episode_id, checksum)
        assert [d.episode_id for d in decisions] == ["ep-1", "ep-2"]
        assert decisions[0].selected is True
        assert decisions[1].selected is False
        assert decisions[1].reasons[0].code.value == (
            "max_overall_missing_ratio_exceeded"
        )
        assert written_manifest.selected_aligned_artifact_checksums == [
            inputs[0].aligned_artifact_checksum
        ]


class TestStructurallyInvalidCandidateIsRejectedNotFatal:
    @pytest.mark.asyncio
    async def test_invalid_candidate_rejected_valid_candidate_still_selected(
        self, monkeypatch
    ) -> None:
        def _validate(artifact):
            episode_id = artifact.aligned_episode.episode_id
            return AlignedEpisodeValidationReport(
                episode_id=episode_id, valid=episode_id != "ep-2"
            )

        fake_validator = MagicMock()
        fake_validator.validate.side_effect = _validate
        fake_profiler = MagicMock()
        fake_profiler.profile.side_effect = lambda artifact: _profile(
            episode_id=artifact.aligned_episode.episode_id, overall_missing_ratio=0.0
        )
        monkeypatch.setattr(curate_episodes_module, "_validator", fake_validator)
        monkeypatch.setattr(curate_episodes_module, "_profiler", fake_profiler)

        data1 = _artifact_bytes("ep-1")
        data2 = _artifact_bytes("ep-2")
        uri1 = "mem://episodes/ep-1/aligned/x.json"
        uri2 = "mem://episodes/ep-2/aligned/x.json"

        inputs = [
            AlignedArtifactRevision(
                episode_id="ep-1",
                aligned_artifact_id="art-aligned-1",
                aligned_artifact_checksum=_checksum(data1).removeprefix("sha256:"),
            ),
            AlignedArtifactRevision(
                episode_id="ep-2",
                aligned_artifact_id="art-aligned-2",
                aligned_artifact_checksum=_checksum(data2).removeprefix("sha256:"),
            ),
        ]
        export_bytes = _export_manifest_bytes(inputs)
        export_uri = "mem://learning/export-1/manifest.json"

        context = _make_context(
            records_by_artifact_id={
                "art-export-1": _export_manifest_record(
                    artifact_id="art-export-1",
                    checksum=_checksum(export_bytes),
                    uri=export_uri,
                ),
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
            bytes_by_uri={export_uri: export_bytes, uri1: data1, uri2: data2},
        )
        request = _make_request(context)

        result = await CurateEpisodesJobHandler().run(request)

        assert result.selected_count == 1
        assert result.rejected_count == 1
        written_manifest = context.analytics_writer.write_curation_manifest.await_args[
            0
        ][0]
        rejected = next(d for d in written_manifest.decisions if not d.selected)
        assert rejected.episode_id == "ep-2"
        assert rejected.reasons[0].code.value == "structurally_invalid"
        context.commit.assert_awaited_once()


class TestChecksumRaceProtection:
    @pytest.mark.asyncio
    async def test_pinned_export_manifest_checksum_mismatch_fails_no_writes(
        self,
    ) -> None:
        export_bytes = _export_manifest_bytes([])
        export_uri = "mem://learning/export-1/manifest.json"

        context = _make_context(
            records_by_artifact_id={
                "art-export-1": _export_manifest_record(
                    artifact_id="art-export-1",
                    checksum=_checksum(export_bytes),
                    uri=export_uri,
                ),
            },
            bytes_by_uri={export_uri: export_bytes},
        )
        request = _make_request(
            context,
            learning_data_export_manifest_checksum="0" * 64,  # deliberately wrong
        )

        with pytest.raises(LearningExportManifestChecksumMismatchError):
            await CurateEpisodesJobHandler().run(request)

        context.analytics_writer.write_curation_manifest.assert_not_awaited()
        context.artifact_record_store.create.assert_not_called()
        context.commit.assert_not_called()


class TestDeterminism:
    @pytest.mark.asyncio
    async def test_decision_ordering_independent_of_export_inputs_order(
        self, patched_validator_and_profiler
    ) -> None:
        """The pinned LearningDataExportManifest's own ``inputs`` list order
        is fixed once written (Request 2.6 §15 notes CURATE_EPISODES never
        infers a "same candidate set, different order" scenario across two
        *distinct* export manifests -- EXPORT_LEARNING_DATA's own execution-
        key already sorts+dedups on that, per key.py's
        _export_learning_data_transform). What CURATE_EPISODES itself must
        guarantee is that its *own* decisions/manifest ordering never
        depends on the iteration order of one given manifest's inputs list
        -- proven here by pinning the same two revisions in reverse order
        and asserting the persisted manifest still comes out
        (episode_id, checksum)-sorted.
        """
        profiles = patched_validator_and_profiler
        profiles["ep-1"] = _profile(episode_id="ep-1", overall_missing_ratio=0.05)
        profiles["ep-2"] = _profile(episode_id="ep-2", overall_missing_ratio=0.02)

        data1 = _artifact_bytes("ep-1")
        data2 = _artifact_bytes("ep-2")
        uri1 = "mem://episodes/ep-1/aligned/x.json"
        uri2 = "mem://episodes/ep-2/aligned/x.json"

        rev1 = AlignedArtifactRevision(
            episode_id="ep-1",
            aligned_artifact_id="art-aligned-1",
            aligned_artifact_checksum=_checksum(data1).removeprefix("sha256:"),
        )
        rev2 = AlignedArtifactRevision(
            episode_id="ep-2",
            aligned_artifact_id="art-aligned-2",
            aligned_artifact_checksum=_checksum(data2).removeprefix("sha256:"),
        )

        # Reversed input order within one single manifest.
        export_bytes = _export_manifest_bytes([rev2, rev1])
        export_uri = "mem://learning/export-1/manifest.json"
        context = _make_context(
            records_by_artifact_id={
                "art-export-1": _export_manifest_record(
                    artifact_id="art-export-1",
                    checksum=_checksum(export_bytes),
                    uri=export_uri,
                ),
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
            bytes_by_uri={export_uri: export_bytes, uri1: data1, uri2: data2},
        )
        request = _make_request(context)
        await CurateEpisodesJobHandler().run(request)

        written_manifest = context.analytics_writer.write_curation_manifest.await_args[
            0
        ][0]
        assert [d.episode_id for d in written_manifest.decisions] == ["ep-1", "ep-2"]

    @pytest.mark.asyncio
    async def test_repeated_run_over_same_pinned_revision_is_byte_identical(
        self, patched_validator_and_profiler
    ) -> None:
        profiles = patched_validator_and_profiler
        profiles["ep-1"] = _profile(episode_id="ep-1", overall_missing_ratio=0.05)

        data1 = _artifact_bytes("ep-1")
        uri1 = "mem://episodes/ep-1/aligned/x.json"
        rev1 = AlignedArtifactRevision(
            episode_id="ep-1",
            aligned_artifact_id="art-aligned-1",
            aligned_artifact_checksum=_checksum(data1).removeprefix("sha256:"),
        )
        export_bytes = _export_manifest_bytes([rev1])
        export_uri = "mem://learning/export-1/manifest.json"

        def _new_context() -> MagicMock:
            return _make_context(
                records_by_artifact_id={
                    "art-export-1": _export_manifest_record(
                        artifact_id="art-export-1",
                        checksum=_checksum(export_bytes),
                        uri=export_uri,
                    ),
                    "art-aligned-1": _aligned_record(
                        artifact_id="art-aligned-1",
                        episode_id="ep-1",
                        checksum=_checksum(data1),
                        uri=uri1,
                    ),
                },
                bytes_by_uri={export_uri: export_bytes, uri1: data1},
            )

        result1 = await CurateEpisodesJobHandler().run(_make_request(_new_context()))
        result2 = await CurateEpisodesJobHandler().run(_make_request(_new_context()))

        assert result1.curation_id == result2.curation_id


class TestManifestProvenance:
    """SceneOps V2 Request 2.6A §3/§9: the persisted manifest must record
    exactly the analysis semantics versions actually used to produce this
    run's decisions -- readers must never have to infer them from
    currently-installed package state."""

    @pytest.mark.asyncio
    async def test_manifest_records_actual_semantics_versions(
        self, patched_validator_and_profiler
    ) -> None:
        from sceneops_core.episodes.alignment import (
            ALIGNED_EPISODE_PROFILE_SEMANTICS_VERSION,
            ALIGNED_EPISODE_VALIDATION_SEMANTICS_VERSION,
        )
        from sceneops_core.episodes.curation import CURATION_SEMANTICS_VERSION

        profiles = patched_validator_and_profiler
        profiles["ep-1"] = _profile(episode_id="ep-1", overall_missing_ratio=0.05)

        data1 = _artifact_bytes("ep-1")
        uri1 = "mem://episodes/ep-1/aligned/x.json"
        rev1 = AlignedArtifactRevision(
            episode_id="ep-1",
            aligned_artifact_id="art-aligned-1",
            aligned_artifact_checksum=_checksum(data1).removeprefix("sha256:"),
        )
        export_bytes = _export_manifest_bytes([rev1])
        export_uri = "mem://learning/export-1/manifest.json"
        context = _make_context(
            records_by_artifact_id={
                "art-export-1": _export_manifest_record(
                    artifact_id="art-export-1",
                    checksum=_checksum(export_bytes),
                    uri=export_uri,
                ),
                "art-aligned-1": _aligned_record(
                    artifact_id="art-aligned-1",
                    episode_id="ep-1",
                    checksum=_checksum(data1),
                    uri=uri1,
                ),
            },
            bytes_by_uri={export_uri: export_bytes, uri1: data1},
        )

        await CurateEpisodesJobHandler().run(_make_request(context))

        written_manifest = context.analytics_writer.write_curation_manifest.await_args[
            0
        ][0]
        assert written_manifest.curation_semantics_version == CURATION_SEMANTICS_VERSION
        assert (
            written_manifest.validation_semantics_version
            == ALIGNED_EPISODE_VALIDATION_SEMANTICS_VERSION
        )
        assert (
            written_manifest.profile_semantics_version
            == ALIGNED_EPISODE_PROFILE_SEMANTICS_VERSION
        )
