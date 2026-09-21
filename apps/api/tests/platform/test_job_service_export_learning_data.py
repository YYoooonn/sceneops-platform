"""Tests for EXPORT_LEARNING_DATA's pre-execution-key input resolution
(SceneOps V2 Request 2.5 §7/§12).

Unlike ALIGN_EPISODE/VALIDATE_ALIGNED_EPISODE/PROFILE_ALIGNED_EPISODE's
single-field resolution, EXPORT_LEARNING_DATA pins a LIST of inputs -- each
item's checksum is resolved independently, and the resulting execution key
must be independent of the caller's input order (Request 2.5 §12). Reuses
the same fake pattern as test_job_service_aligned_episode_analysis.py.
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

    def add(
        self,
        *,
        artifact_id: str,
        episode_id: str,
        checksum: str | None,
        kind: ArtifactKind = ArtifactKind.ALIGNED_EPISODE_MANIFEST,
    ) -> None:
        self.records[artifact_id] = ArtifactRecord(
            artifact_id=artifact_id,
            kind=kind.value,
            uri=f"mem://aligned/{artifact_id}.json",
            owner_type=ArtifactOwnerType.EPISODE.value,
            owner_id=episode_id,
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


def _request(inputs: list[dict]) -> CreateJobRequest:
    return CreateJobRequest(
        type=JobType.EXPORT_LEARNING_DATA,
        dataset_id=DATASET_ID,
        dataset_version=DATASET_VERSION,
        params={"inputs": inputs},
    )


class TestChecksumAutoResolution:
    @pytest.mark.asyncio
    async def test_each_input_checksum_is_resolved_independently(self) -> None:
        service, artifacts = _service()
        artifacts.add(
            artifact_id="art-A", episode_id="ep-1", checksum="sha256:" + "a" * 64
        )
        artifacts.add(
            artifact_id="art-B", episode_id="ep-2", checksum="sha256:" + "b" * 64
        )

        job = await service.create_job(
            _request(
                [
                    {"episode_id": "ep-1", "aligned_artifact_id": "art-A"},
                    {"episode_id": "ep-2", "aligned_artifact_id": "art-B"},
                ]
            )
        )

        checksums = {
            item["episode_id"]: item["aligned_artifact_checksum"]
            for item in job.params["inputs"]
        }
        assert checksums == {"ep-1": "a" * 64, "ep-2": "b" * 64}

    @pytest.mark.asyncio
    async def test_caller_supplied_checksum_is_left_untouched(self) -> None:
        service, artifacts = _service()
        artifacts.add(
            artifact_id="art-A", episode_id="ep-1", checksum="sha256:" + "a" * 64
        )

        job = await service.create_job(
            _request(
                [
                    {
                        "episode_id": "ep-1",
                        "aligned_artifact_id": "art-A",
                        "aligned_artifact_checksum": "deadbeef" * 8,
                    }
                ]
            )
        )

        assert job.params["inputs"][0]["aligned_artifact_checksum"] == "deadbeef" * 8

    @pytest.mark.asyncio
    async def test_invalid_artifact_kind_raises(self) -> None:
        service, artifacts = _service()
        artifacts.add(
            artifact_id="art-wrong-kind",
            episode_id="ep-1",
            checksum="sha256:" + "a" * 64,
            kind=ArtifactKind.EPISODE_MANIFEST,
        )

        with pytest.raises(ValueError, match="not a valid"):
            await service.create_job(
                _request(
                    [{"episode_id": "ep-1", "aligned_artifact_id": "art-wrong-kind"}]
                )
            )

    @pytest.mark.asyncio
    async def test_missing_checksum_on_artifact_record_raises(self) -> None:
        service, artifacts = _service()
        artifacts.add(artifact_id="art-A", episode_id="ep-1", checksum=None)

        with pytest.raises(ValueError, match="no checksum"):
            await service.create_job(
                _request([{"episode_id": "ep-1", "aligned_artifact_id": "art-A"}])
            )


class TestOrderIndependentDedup:
    @pytest.mark.asyncio
    async def test_same_inputs_different_order_dedup_to_one_job(self) -> None:
        service, artifacts = _service()
        artifacts.add(
            artifact_id="art-A", episode_id="ep-1", checksum="sha256:" + "a" * 64
        )
        artifacts.add(
            artifact_id="art-B", episode_id="ep-2", checksum="sha256:" + "b" * 64
        )

        job1 = await service.create_job(
            _request(
                [
                    {"episode_id": "ep-1", "aligned_artifact_id": "art-A"},
                    {"episode_id": "ep-2", "aligned_artifact_id": "art-B"},
                ]
            )
        )
        job2 = await service.create_job(
            _request(
                [
                    {"episode_id": "ep-2", "aligned_artifact_id": "art-B"},
                    {"episode_id": "ep-1", "aligned_artifact_id": "art-A"},
                ]
            )
        )

        assert job1.job_id == job2.job_id
        assert job1.execution_key == job2.execution_key

    @pytest.mark.asyncio
    async def test_different_aligned_artifact_id_same_checksum_dedups(self) -> None:
        """Two different producer executions writing byte-identical content
        (different aligned_artifact_id, same checksum) must still resolve
        to one execution -- aligned_artifact_id is lineage, not identity."""
        service, artifacts = _service()
        artifacts.add(
            artifact_id="art-A1", episode_id="ep-1", checksum="sha256:" + "a" * 64
        )
        artifacts.add(
            artifact_id="art-A2", episode_id="ep-1", checksum="sha256:" + "a" * 64
        )

        job1 = await service.create_job(
            _request([{"episode_id": "ep-1", "aligned_artifact_id": "art-A1"}])
        )
        job2 = await service.create_job(
            _request([{"episode_id": "ep-1", "aligned_artifact_id": "art-A2"}])
        )

        assert job1.job_id == job2.job_id

    @pytest.mark.asyncio
    async def test_different_checksum_does_not_dedup(self) -> None:
        service, artifacts = _service()
        artifacts.add(
            artifact_id="art-A", episode_id="ep-1", checksum="sha256:" + "a" * 64
        )
        artifacts.add(
            artifact_id="art-A2", episode_id="ep-1", checksum="sha256:" + "c" * 64
        )

        job1 = await service.create_job(
            _request([{"episode_id": "ep-1", "aligned_artifact_id": "art-A"}])
        )
        job2 = await service.create_job(
            _request([{"episode_id": "ep-1", "aligned_artifact_id": "art-A2"}])
        )

        assert job1.job_id != job2.job_id

    @pytest.mark.asyncio
    async def test_different_tables_selection_does_not_dedup(self) -> None:
        service, artifacts = _service()
        artifacts.add(
            artifact_id="art-A", episode_id="ep-1", checksum="sha256:" + "a" * 64
        )

        job1 = await service.create_job(
            CreateJobRequest(
                type=JobType.EXPORT_LEARNING_DATA,
                dataset_id=DATASET_ID,
                dataset_version=DATASET_VERSION,
                params={
                    "inputs": [{"episode_id": "ep-1", "aligned_artifact_id": "art-A"}],
                    "tables": ["learning_steps"],
                },
            )
        )
        job2 = await service.create_job(
            CreateJobRequest(
                type=JobType.EXPORT_LEARNING_DATA,
                dataset_id=DATASET_ID,
                dataset_version=DATASET_VERSION,
                params={
                    "inputs": [{"episode_id": "ep-1", "aligned_artifact_id": "art-A"}],
                    "tables": ["learning_steps", "learning_signals"],
                },
            )
        )

        assert job1.job_id != job2.job_id

    @pytest.mark.asyncio
    async def test_force_bypasses_dedup(self) -> None:
        service, artifacts = _service()
        artifacts.add(
            artifact_id="art-A", episode_id="ep-1", checksum="sha256:" + "a" * 64
        )

        job1 = await service.create_job(
            _request([{"episode_id": "ep-1", "aligned_artifact_id": "art-A"}])
        )
        job2 = await service.create_job(
            CreateJobRequest(
                type=JobType.EXPORT_LEARNING_DATA,
                dataset_id=DATASET_ID,
                dataset_version=DATASET_VERSION,
                params={
                    "inputs": [{"episode_id": "ep-1", "aligned_artifact_id": "art-A"}]
                },
                force=True,
            )
        )

        assert job1.job_id != job2.job_id
        assert job1.execution_key == job2.execution_key


class TestDuplicateSemanticInputRejection:
    """SceneOps V2 Request 2.5A §2: one export snapshot must not reference
    the same aligned revision twice. Duplication is determined by
    aligned_artifact_checksum, not by the (random) aligned_artifact_id --
    two different ArtifactRecords with byte-identical content are still one
    semantic revision."""

    @pytest.mark.asyncio
    async def test_exact_duplicate_input_is_rejected(self) -> None:
        service, artifacts = _service()
        artifacts.add(
            artifact_id="art-A", episode_id="ep-1", checksum="sha256:" + "a" * 64
        )

        with pytest.raises(ValueError, match="duplicate aligned_artifact_checksum"):
            await service.create_job(
                _request(
                    [
                        {"episode_id": "ep-1", "aligned_artifact_id": "art-A"},
                        {"episode_id": "ep-1", "aligned_artifact_id": "art-A"},
                    ]
                )
            )

    @pytest.mark.asyncio
    async def test_different_artifact_id_same_checksum_is_rejected(self) -> None:
        service, artifacts = _service()
        artifacts.add(
            artifact_id="art-A1", episode_id="ep-1", checksum="sha256:" + "a" * 64
        )
        artifacts.add(
            artifact_id="art-A2", episode_id="ep-1", checksum="sha256:" + "a" * 64
        )

        with pytest.raises(ValueError, match="duplicate aligned_artifact_checksum"):
            await service.create_job(
                _request(
                    [
                        {"episode_id": "ep-1", "aligned_artifact_id": "art-A1"},
                        {"episode_id": "ep-1", "aligned_artifact_id": "art-A2"},
                    ]
                )
            )

    @pytest.mark.asyncio
    async def test_caller_pinned_duplicate_checksums_are_rejected(self) -> None:
        """Duplicate detection also applies when the caller pins
        aligned_artifact_checksum explicitly (no repository lookup needed
        to discover the collision)."""
        service, _artifacts = _service()

        with pytest.raises(ValueError, match="duplicate aligned_artifact_checksum"):
            await service.create_job(
                _request(
                    [
                        {
                            "episode_id": "ep-1",
                            "aligned_artifact_id": "art-A1",
                            "aligned_artifact_checksum": "a" * 64,
                        },
                        {
                            "episode_id": "ep-1",
                            "aligned_artifact_id": "art-A2",
                            "aligned_artifact_checksum": "a" * 64,
                        },
                    ]
                )
            )

    @pytest.mark.asyncio
    async def test_distinct_checksums_same_episode_id_are_allowed(self) -> None:
        """Two distinct aligned revisions of the same Episode are not
        duplicates -- duplication is checksum-only, never episode_id-based
        (Request 2.5A §3)."""
        service, artifacts = _service()
        artifacts.add(
            artifact_id="art-A1", episode_id="ep-1", checksum="sha256:" + "a" * 64
        )
        artifacts.add(
            artifact_id="art-A2", episode_id="ep-1", checksum="sha256:" + "b" * 64
        )

        job = await service.create_job(
            _request(
                [
                    {"episode_id": "ep-1", "aligned_artifact_id": "art-A1"},
                    {"episode_id": "ep-1", "aligned_artifact_id": "art-A2"},
                ]
            )
        )

        checksums = {item["aligned_artifact_checksum"] for item in job.params["inputs"]}
        assert checksums == {"a" * 64, "b" * 64}
