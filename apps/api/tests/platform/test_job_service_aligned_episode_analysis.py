"""Tests for VALIDATE_ALIGNED_EPISODE/PROFILE_ALIGNED_EPISODE's
pre-execution-key checksum resolution (SceneOps V2 Request 2.4 §51).

Unlike ALIGN_EPISODE's source resolution, aligned_artifact_id is always
required/pinned -- resolving its checksum is a simple 1:1
ArtifactRecord.get() lookup, not a "latest" selection. Reuses the same fake
pattern as test_job_service_align_episode.py.
"""

from __future__ import annotations

import pytest

from app.platform.jobs.service import JobService
from sceneops_core.artifacts.schemas import (
    ArtifactKind,
    ArtifactOwnerType,
    ArtifactRecord,
)
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
    def __init__(self) -> None:
        self.records: dict[str, ArtifactRecord] = {}

    def add(self, *, artifact_id: str, checksum: str | None) -> None:
        self.records[artifact_id] = ArtifactRecord(
            artifact_id=artifact_id,
            kind=ArtifactKind.ALIGNED_EPISODE_MANIFEST.value,
            uri=f"mem://aligned/{artifact_id}.json",
            owner_type=ArtifactOwnerType.EPISODE.value,
            owner_id=EPISODE_ID,
            checksum=checksum,
        )

    async def create(self, **kwargs):
        raise NotImplementedError

    async def get(self, artifact_id: str) -> ArtifactRecord | None:
        return self.records.get(artifact_id)

    async def list(self, **kwargs) -> list[ArtifactRecord]:
        return list(self.records.values())


def _service() -> tuple[JobService, FakeArtifactRepository]:
    artifact_repo = FakeArtifactRepository()
    service = JobService(
        repository=FakeJobRepository(),
        event_repository=FakeJobEventRepository(),
        artifact_repository=artifact_repo,
        default_dataset_id=DATASET_ID,
        default_dataset_version=DATASET_VERSION,
    )
    return service, artifact_repo


def _request(job_type: JobType, **params_overrides) -> CreateJobRequest:
    params = {"episode_id": EPISODE_ID, "aligned_artifact_id": "art-aligned-A"}
    params.update(params_overrides)
    return CreateJobRequest(
        type=job_type,
        dataset_id=DATASET_ID,
        dataset_version=DATASET_VERSION,
        params=params,
    )


@pytest.mark.parametrize(
    "job_type", [JobType.VALIDATE_ALIGNED_EPISODE, JobType.PROFILE_ALIGNED_EPISODE]
)
class TestChecksumAutoResolution:
    @pytest.mark.asyncio
    async def test_checksum_is_resolved_from_pinned_artifact_id(self, job_type) -> None:
        service, artifacts = _service()
        artifacts.add(artifact_id="art-aligned-A", checksum="sha256:" + "a" * 64)

        job = await service.create_job(_request(job_type))

        assert job.params["aligned_artifact_checksum"] == "a" * 64

    @pytest.mark.asyncio
    async def test_same_artifact_id_dedups(self, job_type) -> None:
        service, artifacts = _service()
        artifacts.add(artifact_id="art-aligned-A", checksum="sha256:" + "a" * 64)

        job1 = await service.create_job(_request(job_type))
        job2 = await service.create_job(_request(job_type))

        assert job1.job_id == job2.job_id

    @pytest.mark.asyncio
    async def test_different_aligned_artifact_content_different_execution(
        self, job_type
    ) -> None:
        """Two different ALIGNED_EPISODE_MANIFEST artifact_ids that happen to
        share the same checksum (a re-run at the same deterministic aligned
        URI, Request 2.3's overwrite behavior) must dedup on checksum, not
        artifact_id -- the same lesson from Request 2.3A, reapplied here."""
        service, artifacts = _service()
        artifacts.add(artifact_id="art-aligned-A", checksum="sha256:" + "x" * 64)
        artifacts.add(artifact_id="art-aligned-B", checksum="sha256:" + "x" * 64)

        job_a = await service.create_job(
            _request(job_type, aligned_artifact_id="art-aligned-A")
        )
        job_b = await service.create_job(
            _request(job_type, aligned_artifact_id="art-aligned-B")
        )

        assert job_a.job_id == job_b.job_id  # same content -> same execution

    @pytest.mark.asyncio
    async def test_genuinely_different_content_is_a_different_execution(
        self, job_type
    ) -> None:
        service, artifacts = _service()
        artifacts.add(artifact_id="art-aligned-A", checksum="sha256:" + "a" * 64)
        artifacts.add(artifact_id="art-aligned-B", checksum="sha256:" + "b" * 64)

        job_a = await service.create_job(
            _request(job_type, aligned_artifact_id="art-aligned-A")
        )
        job_b = await service.create_job(
            _request(job_type, aligned_artifact_id="art-aligned-B")
        )

        assert job_a.job_id != job_b.job_id

    @pytest.mark.asyncio
    async def test_caller_pinned_checksum_is_not_overridden(self, job_type) -> None:
        service, artifacts = _service()
        artifacts.add(artifact_id="art-aligned-A", checksum="sha256:" + "a" * 64)

        job = await service.create_job(
            _request(job_type, aligned_artifact_checksum="z" * 64)
        )

        assert job.params["aligned_artifact_checksum"] == "z" * 64

    @pytest.mark.asyncio
    async def test_unknown_artifact_id_fails_clearly(self, job_type) -> None:
        service, _artifacts = _service()

        with pytest.raises(ValueError, match="not a valid ALIGNED_EPISODE_MANIFEST"):
            await service.create_job(
                _request(job_type, aligned_artifact_id="does-not-exist")
            )

    @pytest.mark.asyncio
    async def test_no_checksum_on_record_fails_clearly(self, job_type) -> None:
        service, artifacts = _service()
        artifacts.add(artifact_id="art-aligned-A", checksum=None)

        with pytest.raises(ValueError, match="no checksum"):
            await service.create_job(_request(job_type))


class TestUnrelatedJobTypeRegression:
    @pytest.mark.asyncio
    async def test_align_episode_dedup_still_works_unaffected(self) -> None:
        # Sanity: adding two more JobType-gated resolution branches to
        # create_job() must not disturb ALIGN_EPISODE's own branch.
        from sceneops_core.episodes.alignment import TemporalAlignmentConfig

        service, artifacts = _service()
        artifacts.records["art-src-1"] = ArtifactRecord(
            artifact_id="art-src-1",
            kind=ArtifactKind.EPISODE_MANIFEST.value,
            uri="mem://episodes/ep-1.json",
            owner_type=ArtifactOwnerType.EPISODE.value,
            owner_id=EPISODE_ID,
            checksum="sha256:" + "a" * 64,
        )
        request = CreateJobRequest(
            type=JobType.ALIGN_EPISODE,
            dataset_id=DATASET_ID,
            dataset_version=DATASET_VERSION,
            params={
                "episode_id": EPISODE_ID,
                "alignment_config": TemporalAlignmentConfig(
                    target_frequency_hz=1.0
                ).model_dump(mode="json", exclude_none=True),
            },
        )
        job1 = await service.create_job(request)
        job2 = await service.create_job(request)
        assert job1.job_id == job2.job_id
        assert job1.params["source_artifact_id"] == "art-src-1"
