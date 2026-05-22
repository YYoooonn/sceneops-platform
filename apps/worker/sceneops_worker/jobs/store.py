import json
from pathlib import Path
from typing import Any

from sceneops_core.paths.runs import job_manifest_path, jobs_root
from sceneops_core.schemas.jobs import JobManifest


class JobStore:
    def __init__(self, runs_root: Path) -> None:
        self.runs_root = runs_root
        self.jobs_root = jobs_root(runs_root=runs_root)

    def get_job(self, job_id: str) -> JobManifest | None:
        path = self._job_path(job_id)

        if not path.exists():
            return None

        data = self._read_json(path)
        return JobManifest.model_validate(data)

    def save_job(self, job: JobManifest) -> JobManifest:
        self.jobs_root.mkdir(parents=True, exist_ok=True)

        path = self._job_path(job.jobId)

        with path.open("w", encoding="utf-8") as file:
            json.dump(
                job.model_dump(mode="json"),
                file,
                ensure_ascii=False,
                indent=2,
            )
            file.write("\n")

        return job

    def _job_path(self, job_id: str) -> Path:
        return job_manifest_path(
            runs_root=self.runs_root,
            job_id=job_id,
        )

    def _read_json(self, path: Path) -> Any:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)
