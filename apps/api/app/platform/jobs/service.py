from __future__ import annotations

from typing import Any

from sceneops_core.artifacts.schemas import ArtifactKind
from sceneops_core.common.ids import generate_job_event_id, generate_job_id
from sceneops_core.common.schemas import JsonDict
from sceneops_core.common.time import utc_now
from sceneops_core.executions import compute_execution_key, params_for_execution_key
from sceneops_core.jobs.schemas import (
    CreateJobRequest,
    JobEvent,
    JobEventLevel,
    JobEventType,
    JobManifest,
    JobStatus,
    JobType,
    create_initial_job_steps,
    parse_job_params,
)
from app.platform.jobs.schemas import JobEventListResponse, JobListResponse
from sceneops_db.queries import resolve_current_episode_manifest_source
from sceneops_db.repositories.artifacts import ArtifactRepository
from sceneops_db.repositories.jobs import JobEventRepository, JobRepository

_DEDUP_STATUSES = {
    JobStatus.PENDING,
    JobStatus.QUEUED,
    JobStatus.RUNNING,
    JobStatus.SUCCEEDED,
}


class JobService:
    def __init__(
        self,
        *,
        repository: JobRepository,
        event_repository: JobEventRepository,
        artifact_repository: ArtifactRepository,
        default_dataset_id: str,
        default_dataset_version: str,
    ) -> None:
        self._repository = repository
        self._event_repository = event_repository
        self._artifact_repository = artifact_repository
        self._default_dataset_id = default_dataset_id
        self._default_dataset_version = default_dataset_version

    async def create_job(self, request: CreateJobRequest) -> JobManifest:
        now = utc_now()

        dataset_id = request.dataset_id or self._default_dataset_id
        dataset_version = request.dataset_version or self._default_dataset_version

        raw_params = {
            **request.params,
            "dataset_id": dataset_id,
            "dataset_version": dataset_version,
        }

        if request.type == JobType.ALIGN_EPISODE:
            raw_params = await self._resolve_align_episode_source(
                raw_params, dataset_id=dataset_id, dataset_version=dataset_version
            )
        elif request.type in (
            JobType.VALIDATE_ALIGNED_EPISODE,
            JobType.PROFILE_ALIGNED_EPISODE,
        ):
            raw_params = await self._resolve_aligned_artifact_checksum(raw_params)

        validated_params = parse_job_params(request.type, raw_params)
        validated_params_dump = validated_params.model_dump()

        execution_key = compute_execution_key(
            kind="job",
            type=request.type.value,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            params=params_for_execution_key(request.type, validated_params_dump),
        )

        if not request.force:
            existing = await self._repository.find_by_execution_key(
                execution_key, statuses=_DEDUP_STATUSES
            )
            if existing is not None:
                return existing

        job = JobManifest(
            job_id=generate_job_id(),
            type=request.type,
            status=JobStatus.PENDING,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            pipeline_run_id=request.pipeline_run_id,
            pipeline_task_run_id=request.pipeline_task_run_id,
            pipeline_task_id=request.pipeline_task_id,
            params=validated_params_dump,
            steps=create_initial_job_steps(request.type),
            retry_count=0,
            max_retries=request.max_retries,
            execution_key=execution_key,
            queued_at=now,
            created_at=now,
            updated_at=now,
        )

        created = await self._repository.create(job)

        await self._event_repository.append(
            JobEvent(
                event_id=generate_job_event_id(),
                job_id=created.job_id,
                type=JobEventType.CREATED,
                level=JobEventLevel.INFO,
                job_type=created.type,
                pipeline_run_id=created.pipeline_run_id,
                pipeline_task_run_id=created.pipeline_task_run_id,
                pipeline_task_id=created.pipeline_task_id,
                message="Job created",
                data={
                    "dataset_id": created.dataset_id,
                    "dataset_version": created.dataset_version,
                },
                created_at=now,
            )
        )

        return created

    async def _resolve_align_episode_source(
        self,
        raw_params: dict[str, Any],
        *,
        dataset_id: str,
        dataset_version: str,
    ) -> JsonDict:
        """SceneOps V2 Request 2.3A: resolve the current EPISODE_MANIFEST
        source revision *before* execution-key computation, so an unpinned
        ALIGN_EPISODE request dedups on source content, not just
        (episode_id, config, semantics_version).

        A caller-supplied pin (source_artifact_id set) is left untouched —
        an explicit pin always wins (§7). Legacy sources with no populated
        checksum are explicitly out of scope (§0/§18): fail clearly rather
        than silently falling back to a weaker dedup rule.
        """
        if raw_params.get("source_artifact_id") is not None:
            return raw_params

        episode_id = raw_params.get("episode_id")
        if not episode_id:
            # Let normal Pydantic param validation raise its own clear
            # "episode_id required" error rather than duplicating that check
            # here.
            return raw_params

        record = await resolve_current_episode_manifest_source(
            self._artifact_repository,
            episode_id=episode_id,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
        )
        if record is None:
            raise ValueError(
                f"No EPISODE_MANIFEST artifact found for episode_id={episode_id!r} "
                f"in {dataset_id}/{dataset_version} — build_episodes must run "
                "before align_episode can be dispatched."
            )
        if record.checksum is None:
            raise ValueError(
                f"Current EpisodeManifest source for episode_id={episode_id!r} "
                f"(artifact_id={record.artifact_id}) has no checksum and must be "
                "rebuilt via build_episodes, or pin an explicit "
                "source_artifact_id/source_manifest_sha256 if the correct hash "
                "is already known."
            )

        return {
            **raw_params,
            "source_artifact_id": record.artifact_id,
            "source_manifest_sha256": record.checksum.removeprefix("sha256:"),
        }

    async def _resolve_aligned_artifact_checksum(
        self, raw_params: dict[str, Any]
    ) -> JsonDict:
        """SceneOps V2 Request 2.4 §33/§34: shared by
        VALIDATE_ALIGNED_EPISODE/PROFILE_ALIGNED_EPISODE.

        Unlike ALIGN_EPISODE's source resolution, aligned_artifact_id is
        always required and caller-pinned (there is no sensible "current
        aligned artifact" for one episode, which can legitimately have many).
        Resolving its checksum is therefore a simple 1:1 ArtifactRecord.get()
        lookup, not a "latest" selection -- no ambiguity, no ordering
        assumption. A caller-supplied aligned_artifact_checksum is left
        untouched, matching ALIGN_EPISODE's pin-always-wins behavior.
        """
        if raw_params.get("aligned_artifact_checksum") is not None:
            return raw_params

        aligned_artifact_id = raw_params.get("aligned_artifact_id")
        if not aligned_artifact_id:
            # Let normal Pydantic param validation raise its own clear
            # "aligned_artifact_id required" error.
            return raw_params

        record = await self._artifact_repository.get(aligned_artifact_id)
        if record is None or record.kind != ArtifactKind.ALIGNED_EPISODE_MANIFEST.value:
            raise ValueError(
                f"aligned_artifact_id={aligned_artifact_id!r} is not a valid "
                "ALIGNED_EPISODE_MANIFEST artifact — align_episode must run "
                "before validation/profiling can be dispatched."
            )
        if record.checksum is None:
            raise ValueError(
                f"ALIGNED_EPISODE_MANIFEST artifact {aligned_artifact_id!r} has "
                "no checksum -- this should not happen for any artifact "
                "written by align_episode; re-run align_episode to produce a "
                "checksummed artifact."
            )

        return {
            **raw_params,
            "aligned_artifact_checksum": record.checksum.removeprefix("sha256:"),
        }

    async def list_jobs(
        self,
        *,
        status: JobStatus | None = None,
        job_type: str | None = None,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> JobListResponse:
        jobs = await self._repository.list(
            type=job_type,
            status=status,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            limit=limit,
            offset=offset,
        )
        return JobListResponse(jobs=jobs, count=len(jobs))

    async def get_job(self, job_id: str) -> JobManifest | None:
        return await self._repository.get(job_id)

    async def list_job_events(self, job_id: str) -> JobEventListResponse | None:
        job = await self._repository.get(job_id)
        if job is None:
            return None
        events = await self._event_repository.list_for_job(job_id)
        return JobEventListResponse(events=events, count=len(events))

    async def validate_executable(self, job_id: str) -> JobManifest:
        job = await self._repository.get(job_id)
        if job is None:
            raise FileNotFoundError(f"Job not found: {job_id}")
        blocked = {JobStatus.RUNNING, JobStatus.SUCCEEDED, JobStatus.CANCELLED}
        if job.status in blocked:
            raise ValueError(
                f"Job is not executable: job_id={job_id}, status={job.status}"
            )
        return job

    async def mark_queued(self, job_id: str) -> JobManifest:
        job = await self.validate_executable(job_id)

        update: dict[str, object] = {}
        if job.status == JobStatus.FAILED:
            if job.retry_count >= job.max_retries:
                raise ValueError(
                    f"Job has exhausted retries: job_id={job_id}, "
                    f"retry_count={job.retry_count}, max_retries={job.max_retries}"
                )
            update["retry_count"] = job.retry_count + 1

        now = utc_now()
        job = job.model_copy(
            update={
                **update,
                "status": JobStatus.QUEUED,
                "queued_at": now,
                "updated_at": now,
            }
        )
        await self._repository.update(job)

        await self._event_repository.append(
            JobEvent(
                event_id=generate_job_event_id(),
                job_id=job.job_id,
                type=JobEventType.QUEUED,
                level=JobEventLevel.INFO,
                status=JobStatus.QUEUED,
                job_type=job.type,
                pipeline_run_id=job.pipeline_run_id,
                pipeline_task_run_id=job.pipeline_task_run_id,
                pipeline_task_id=job.pipeline_task_id,
                message="Job queued",
                created_at=now,
            )
        )

        return job
