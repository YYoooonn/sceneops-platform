from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from sceneops_core.jobs.schemas import JobManifest


@dataclass
class JobExecution:
    job: JobManifest
    worker_id: str | None
    running_step_id: str | None = None
    running_step_name: str | None = None
    handler_result: BaseModel | dict[str, Any] | None = None
    result_payload: dict[str, Any] | None = None

    def update_job(self, job: JobManifest) -> None:
        self.job = job

    def update_running_step(
        self,
        *,
        step_id: str | None,
        step_name: str | None,
    ) -> None:
        self.running_step_id = step_id
        self.running_step_name = step_name

    def update_handler_result(
        self,
        result: BaseModel | dict[str, Any],
        payload: dict[str, Any],
    ) -> None:
        self.handler_result = result
        self.result_payload = payload
