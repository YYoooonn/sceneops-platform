from __future__ import annotations

from typing import Protocol, runtime_checkable

from sceneops_core.common.types import JobId, JsonDict, Metadata


@runtime_checkable
class JobExecutor(Protocol):
    """Contract for executing a single SceneOps job."""

    def execute_job(
        self,
        *,
        job_id: JobId,
        params: Metadata,
    ) -> JsonDict:
        ...


@runtime_checkable
class JobDispatcher(Protocol):
    """Contract for dispatching a job to an execution backend."""

    def dispatch_job(
        self,
        *,
        job_id: JobId,
        params: Metadata | None = None,
    ) -> JobId:
        ...
