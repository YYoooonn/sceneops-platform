from __future__ import annotations

from sceneops_core.common.schemas import SceneOpsBaseModel

from .enums import RunStatus, RunType


class ListRunsRequest(SceneOpsBaseModel):
    type: RunType | None = None
    status: RunStatus | None = None

    dataset_id: str | None = None
    dataset_version: str | None = None

    scene_id: str | None = None
    scenario_set_id: str | None = None

    model_id: str | None = None
    model_version: str | None = None

    pipeline_run_id: str | None = None
    pipeline_step_run_id: str | None = None
    job_id: str | None = None
