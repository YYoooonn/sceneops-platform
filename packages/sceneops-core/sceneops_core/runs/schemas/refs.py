from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel

from .enums import RunStatus, RunType


class RunRef(SceneOpsBaseModel):
    run_id: str
    type: RunType
    status: RunStatus | None = None

    uri: str | None = None

    dataset_id: str | None = None
    dataset_version: str | None = None
    scene_id: str | None = None
    scenario_set_id: str | None = None
    model_id: str | None = None
    model_version: str | None = None

    metadata: JsonDict = Field(default_factory=dict)
