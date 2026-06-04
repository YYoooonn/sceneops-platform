from __future__ import annotations

from datetime import datetime

from pydantic import Field

from sceneops_core.common.schemas import ErrorInfo, JsonDict, SceneOpsBaseModel

from .enums import JobStepStatus


class JobStepDefinition(SceneOpsBaseModel):
    step_id: str
    name: str

    description: str | None = None

    optional: bool = False

    metadata: JsonDict = Field(default_factory=dict)


class JobStep(SceneOpsBaseModel):
    step_id: str
    name: str

    status: JobStepStatus = JobStepStatus.PENDING

    started_at: datetime | None = None
    finished_at: datetime | None = None

    input: JsonDict | None = None
    output: JsonDict | None = None
    error: ErrorInfo | None = None

    produced_artifacts: dict[str, str] = Field(default_factory=dict)
    consumed_artifacts: dict[str, str] = Field(default_factory=dict)

    metadata: JsonDict = Field(default_factory=dict)
