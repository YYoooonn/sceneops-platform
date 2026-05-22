import json
from pathlib import Path
from typing import Any

from sceneops_core.schemas.jobs import JobManifest, JobStatus
from sceneops_core.paths.runs import job_manifest_path, jobs_root


class LocalJobRepository:
    def __init__(self, runs_root: Path) -> None:
        self.runs_root = runs_root
        self.jobs_root = jobs_root(runs_root=runs_root)

    def create_job(self, job: JobManifest) -> JobManifest:
        self.jobs_root.mkdir(parents=True, exist_ok=True)
        self._write_job(job)
        return job

    def list_jobs(
        self,
        *,
        status: JobStatus | None = None,
        job_type: str | None = None,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
    ) -> list[JobManifest]:
        if not self.jobs_root.exists():
            return []

        jobs: list[JobManifest] = []

        for job_file in sorted(self.jobs_root.glob("*.json")):
            data = self._read_json_or_none(job_file)
            if data is None:
                continue

            job = JobManifest.model_validate(data)

            if status is not None and job.status != status:
                continue

            if job_type is not None and job.type != job_type:
                continue

            if dataset_id is not None and job.datasetId != dataset_id:
                continue

            if dataset_version is not None and job.datasetVersion != dataset_version:
                continue

            jobs.append(job)

        # 최신 생성순으로 보고 싶으면 reverse
        jobs.sort(key=lambda job: job.createdAt, reverse=True)
        return jobs

    def get_job(self, job_id: str) -> JobManifest | None:
        path = self._job_path(job_id)
        data = self._read_json_or_none(path)

        if data is None:
            return None

        return JobManifest.model_validate(data)

    def update_job(self, job: JobManifest) -> JobManifest:
        self._write_job(job)
        return job

    def _job_path(self, job_id: str) -> Path:
        return job_manifest_path(runs_root=self.runs_root, job_id=job_id)

    def _write_job(self, job: JobManifest) -> None:
        path = self._job_path(job.jobId)
        path.parent.mkdir(parents=True, exist_ok=True)

        with path.open("w", encoding="utf-8") as file:
            json.dump(
                job.model_dump(mode="json"),
                file,
                ensure_ascii=False,
                indent=2,
            )
            file.write("\n")

    def _read_json_or_none(self, path: Path) -> Any | None:
        if not path.exists():
            return None

        with path.open("r", encoding="utf-8") as file:
            return json.load(file)
