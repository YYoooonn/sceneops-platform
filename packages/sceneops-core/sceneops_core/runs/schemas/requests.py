from __future__ import annotations

from sceneops_core.common.schemas import SceneOpsBaseModel

from .enums import RunStatus


class ListInferenceRunsRequest(SceneOpsBaseModel):
    dataset_id: str | None = None
    dataset_version: str | None = None
    model_id: str | None = None
    model_version: str | None = None
    status: RunStatus | None = None


class ListEvaluationRunsRequest(SceneOpsBaseModel):
    dataset_id: str | None = None
    dataset_version: str | None = None
    model_id: str | None = None
    model_version: str | None = None
    inference_run_id: str | None = None
    status: RunStatus | None = None
