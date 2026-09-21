"""Tests for ALIGN_EPISODE's pre-execution-key source resolution (SceneOps
V2 Request 2.3A).

Uses the same tiny in-memory fake pattern as test_job_service.py, plus a
fake ArtifactRepository that mimics real "latest by created_at" ordering.
Exercises JobService.create_job() directly -- the real Job API entry point
(POST /jobs calls exactly this) -- not a lower-level helper, per Request
2.3A §27's "must be verified through the real Job API creation path."
"""

from __future__ import annotations

import itertools

import pytest

from app.platform.jobs.service import JobService
from sceneops_core.artifacts.schemas import (
    ArtifactKind,
    ArtifactOwnerType,
    ArtifactRecord,
)
from sceneops_core.episodes.alignment import TemporalAlignmentConfig
from sceneops_core.jobs.schemas import (
    CreateJobRequest,
    JobEvent,
    JobManifest,
    JobStatus,
    JobType,
)

DATASET_ID = "d1"
DATASET_VERSION = "v1"
EPISODE_ID = "ep-1"


class FakeJobRepository:
    def __init__(self) -> None:
        self.jobs: dict[str, JobManifest] = {}

    async def create(self, job: JobManifest) -> JobManifest:
        self.jobs[job.job_id] = job
        return job

    async def get(self, job_id: str) -> JobManifest | None:
        return self.jobs.get(job_id)

    async def update(self, job: JobManifest) -> JobManifest:
        self.jobs[job.job_id] = job
        return job

    async def list(self, **kwargs) -> list[JobManifest]:
        return list(self.jobs.values())

    async def count_by_status(self) -> dict[str, int]:
        return {}

    async def find_by_execution_key(
        self, execution_key: str, *, statuses: set[JobStatus]
    ) -> JobManifest | None:
        for job in self.jobs.values():
            if job.execution_key == execution_key and job.status in statuses:
                return job
        return None


class FakeJobEventRepository:
    def __init__(self) -> None:
        self.events: list[JobEvent] = []

    async def append(self, event: JobEvent) -> JobEvent:
        self.events.append(event)
        return event

    async def get(self, event_id: str):
        return None

    async def list_for_job(self, job_id: str, **kwargs) -> list[JobEvent]:
        return [e for e in self.events if e.job_id == job_id]

    async def list_for_pipeline_run(self, pipeline_run_id: str, **kwargs):
        return []


class FakeArtifactRepository:
    """Mimics PostgresArtifactRefRepository.list()'s
    ORDER BY created_at DESC -- records are returned in reverse insertion
    order, matching real "latest wins" behavior."""

    def __init__(self) -> None:
        self.records: list[ArtifactRecord] = []
        self._counter = itertools.count()

    def add(
        self,
        *,
        artifact_id: str,
        checksum: str | None,
        episode_id: str = EPISODE_ID,
        kind: ArtifactKind = ArtifactKind.EPISODE_MANIFEST,
    ) -> None:
        self.records.append(
            ArtifactRecord(
                artifact_id=artifact_id,
                kind=kind.value,
                uri=f"mem://episodes/{episode_id}.json",
                owner_type=ArtifactOwnerType.EPISODE.value,
                owner_id=episode_id,
                dataset_id=DATASET_ID,
                dataset_version=DATASET_VERSION,
                checksum=checksum,
            )
        )

    async def create(self, **kwargs) -> ArtifactRecord:
        raise NotImplementedError

    async def get(self, artifact_id: str) -> ArtifactRecord | None:
        return next((r for r in self.records if r.artifact_id == artifact_id), None)

    async def list(
        self,
        *,
        kind: ArtifactKind | None = None,
        owner_type: str | None = None,
        owner_id: str | None = None,
        limit: int = 100,
        **kwargs,
    ) -> list[ArtifactRecord]:
        matches = [
            r
            for r in self.records
            if (kind is None or r.kind == kind.value)
            and (owner_type is None or r.owner_type == owner_type)
            and (owner_id is None or r.owner_id == owner_id)
        ]
        return list(reversed(matches))[:limit]  # last-added = "latest"


def _service() -> tuple[JobService, FakeJobRepository, FakeArtifactRepository]:
    job_repo = FakeJobRepository()
    artifact_repo = FakeArtifactRepository()
    service = JobService(
        repository=job_repo,
        event_repository=FakeJobEventRepository(),
        artifact_repository=artifact_repo,
        default_dataset_id=DATASET_ID,
        default_dataset_version=DATASET_VERSION,
    )
    return service, job_repo, artifact_repo


def _config_dict(target_frequency_hz: float = 1.0) -> dict:
    return TemporalAlignmentConfig(target_frequency_hz=target_frequency_hz).model_dump(
        mode="json", exclude_none=True
    )


def _request(**params_overrides) -> CreateJobRequest:
    params = {"episode_id": EPISODE_ID, "alignment_config": _config_dict()}
    params.update(params_overrides)
    return CreateJobRequest(
        type=JobType.ALIGN_EPISODE,
        dataset_id=DATASET_ID,
        dataset_version=DATASET_VERSION,
        params=params,
    )


class TestChangedSourceRevision:
    """SceneOps V2 Request 2.3A §19 -- the primary regression test."""

    @pytest.mark.asyncio
    async def test_rebuild_produces_a_different_job_not_a_stale_reuse(self) -> None:
        service, _, artifacts = _service()
        artifacts.add(artifact_id="art-A", checksum="sha256:" + "a" * 64)

        job_a = await service.create_job(_request())

        # Episode rebuilt: new ArtifactRecord, genuinely different content.
        artifacts.add(artifact_id="art-B", checksum="sha256:" + "b" * 64)

        job_b = await service.create_job(_request())

        assert job_a.job_id != job_b.job_id
        assert job_a.execution_key != job_b.execution_key
        assert job_a.params["source_manifest_sha256"] == "a" * 64
        assert job_b.params["source_manifest_sha256"] == "b" * 64


class TestIdenticalContentRebuild:
    """SceneOps V2 Request 2.3A §20/§28."""

    @pytest.mark.asyncio
    async def test_same_checksum_different_artifact_id_dedups(self) -> None:
        service, _, artifacts = _service()
        artifacts.add(artifact_id="art-A", checksum="sha256:" + "x" * 64)

        job_1 = await service.create_job(_request())

        # A second production execution wrote byte-identical content under a
        # brand new random artifact_id.
        artifacts.add(artifact_id="art-B", checksum="sha256:" + "x" * 64)

        job_2 = await service.create_job(_request())

        assert job_1.job_id == job_2.job_id
        assert job_1.execution_key == job_2.execution_key
        # The *stored* params still reflect the newly-resolved lineage row
        # only on the request that actually created a job -- job_2 here is
        # the reused job_1, so provenance naturally still points at art-A,
        # which is correct: nothing new was created.
        assert job_1.params["source_artifact_id"] == "art-A"


class TestConfigChange:
    @pytest.mark.asyncio
    async def test_different_config_different_key(self) -> None:
        service, _, artifacts = _service()
        artifacts.add(artifact_id="art-A", checksum="sha256:" + "a" * 64)

        job_x = await service.create_job(_request(alignment_config=_config_dict(1.0)))
        job_y = await service.create_job(_request(alignment_config=_config_dict(2.0)))

        assert job_x.job_id != job_y.job_id
        assert job_x.execution_key != job_y.execution_key


class TestSemanticsVersionChange:
    @pytest.mark.asyncio
    async def test_execution_key_builder_is_sensitive_to_semantics_version(
        self,
    ) -> None:
        # ALIGNMENT_SEMANTICS_VERSION itself is a frozen production constant
        # (not caller-overridable, per Request 2.2 §48) -- this exercises the
        # identity builder (compute_execution_key + params_for_execution_key)
        # directly rather than smuggling an alternate version through the API.
        from sceneops_core.executions import (
            compute_execution_key,
            params_for_execution_key,
        )

        def key(semantics_version: str) -> str:
            params = {
                "episode_id": EPISODE_ID,
                "source_manifest_sha256": "a" * 64,
                "alignment_semantics_version": semantics_version,
                "alignment_config": _config_dict(),
            }
            return compute_execution_key(
                kind="job",
                type="align_episode",
                dataset_id=DATASET_ID,
                dataset_version=DATASET_VERSION,
                params=params_for_execution_key(JobType.ALIGN_EPISODE, params),
            )

        assert key("v1") != key("v2")


class TestPinnedRevision:
    """SceneOps V2 Request 2.3A §7/§23."""

    @pytest.mark.asyncio
    async def test_explicit_pin_is_not_overridden_by_a_newer_artifact(self) -> None:
        service, _, artifacts = _service()
        artifacts.add(artifact_id="art-old", checksum="sha256:" + "o" * 64)
        artifacts.add(artifact_id="art-new", checksum="sha256:" + "n" * 64)

        job = await service.create_job(
            _request(source_artifact_id="art-old", source_manifest_sha256="o" * 64)
        )

        assert job.params["source_artifact_id"] == "art-old"
        assert job.params["source_manifest_sha256"] == "o" * 64

    @pytest.mark.asyncio
    async def test_pinned_old_revision_and_unpinned_current_are_different_executions(
        self,
    ) -> None:
        service, _, artifacts = _service()
        artifacts.add(artifact_id="art-old", checksum="sha256:" + "o" * 64)
        artifacts.add(artifact_id="art-new", checksum="sha256:" + "n" * 64)

        pinned = await service.create_job(
            _request(source_artifact_id="art-old", source_manifest_sha256="o" * 64)
        )
        unpinned = await service.create_job(_request())

        assert pinned.job_id != unpinned.job_id
        assert unpinned.params["source_artifact_id"] == "art-new"


class TestCurrentSourceSelection:
    """SceneOps V2 Request 2.3A §24."""

    @pytest.mark.asyncio
    async def test_unpinned_resolves_the_latest_of_multiple_records(self) -> None:
        service, _, artifacts = _service()
        artifacts.add(artifact_id="art-1", checksum="sha256:" + "1" * 64)
        artifacts.add(artifact_id="art-2", checksum="sha256:" + "2" * 64)
        artifacts.add(artifact_id="art-3", checksum="sha256:" + "3" * 64)

        job = await service.create_job(_request())

        assert job.params["source_artifact_id"] == "art-3"
        assert job.params["source_manifest_sha256"] == "3" * 64


class TestChecksumMissing:
    """SceneOps V2 Request 2.3A §0/§18/§25 -- legacy sources explicitly out
    of scope for automatic resolution."""

    @pytest.mark.asyncio
    async def test_unpinned_request_against_a_legacy_no_checksum_source_fails_clearly(
        self,
    ) -> None:
        service, _, artifacts = _service()
        artifacts.add(artifact_id="art-legacy", checksum=None)

        with pytest.raises(ValueError, match="no checksum"):
            await service.create_job(_request())

    @pytest.mark.asyncio
    async def test_no_source_artifact_at_all_fails_clearly(self) -> None:
        service, _, _artifacts = _service()

        with pytest.raises(ValueError, match="No EPISODE_MANIFEST artifact found"):
            await service.create_job(_request())

    @pytest.mark.asyncio
    async def test_explicit_pin_bypasses_the_legacy_checksum_check_entirely(
        self,
    ) -> None:
        # The escape hatch: a caller who already knows the correct hash out
        # of band can still pin even a legacy (checksum=None) record's
        # artifact_id, since pinning skips auto-resolution altogether.
        service, _, artifacts = _service()
        artifacts.add(artifact_id="art-legacy", checksum=None)

        job = await service.create_job(
            _request(source_artifact_id="art-legacy", source_manifest_sha256="z" * 64)
        )
        assert job.params["source_artifact_id"] == "art-legacy"


class TestUnrelatedJobTypeRegression:
    """SceneOps V2 Request 2.3A §18/§32 -- no unrelated JobType's identity
    changes."""

    @pytest.mark.asyncio
    async def test_build_episodes_params_are_hashed_unchanged(self) -> None:
        from sceneops_core.executions import (
            compute_execution_key,
            params_for_execution_key,
        )

        params = {"episode_id": EPISODE_ID, "source_artifact_id": "should-stay"}
        assert params_for_execution_key(JobType.BUILD_EPISODES, params) == params

        key = compute_execution_key(
            kind="job",
            type="build_episodes",
            dataset_id=DATASET_ID,
            dataset_version=DATASET_VERSION,
            params=params_for_execution_key(JobType.BUILD_EPISODES, params),
        )
        direct_key = compute_execution_key(
            kind="job",
            type="build_episodes",
            dataset_id=DATASET_ID,
            dataset_version=DATASET_VERSION,
            params=params,
        )
        assert key == direct_key


class TestAcceptanceTable:
    """SceneOps V2 Request 2.3A §34's full acceptance table in one test."""

    @pytest.mark.asyncio
    async def test_full_acceptance_matrix(self) -> None:
        service, _, artifacts = _service()
        artifacts.add(artifact_id="art-A1", checksum="sha256:" + "a" * 64)

        # SHA_A, config X, v1 -> execution K1
        k1 = await service.create_job(_request(alignment_config=_config_dict(1.0)))

        # SHA_A, config X, v1 -> reuse K1
        k1_again = await service.create_job(
            _request(alignment_config=_config_dict(1.0))
        )
        assert k1_again.job_id == k1.job_id

        # SHA_B, config X, v1 -> new K2
        artifacts.add(artifact_id="art-B1", checksum="sha256:" + "b" * 64)
        k2 = await service.create_job(_request(alignment_config=_config_dict(1.0)))
        assert k2.job_id != k1.job_id

        # SHA_A pinned, config Y, v1 -> new K3
        k3 = await service.create_job(
            _request(
                source_artifact_id="art-A1",
                source_manifest_sha256="a" * 64,
                alignment_config=_config_dict(2.0),
            )
        )
        assert k3.job_id not in {k1.job_id, k2.job_id}

        # same bytes/new artifact_id, config X, v1 -> reuse K2 (current source is now SHA_B)
        artifacts.add(artifact_id="art-B2", checksum="sha256:" + "b" * 64)
        reuse_k2 = await service.create_job(
            _request(alignment_config=_config_dict(1.0))
        )
        assert reuse_k2.job_id == k2.job_id
