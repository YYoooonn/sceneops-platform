from __future__ import annotations

from datetime import datetime

from pydantic import Field

from sceneops_core.common.schemas import SceneOpsBaseModel, JsonDict
from .enums import ModelBackend, ModelTaskType, ModelVersionStatus


class ModelRecord(SceneOpsBaseModel):
    model_id: str
    name: str | None = None
    description: str | None = None
    task_type: ModelTaskType = ModelTaskType.DETECTION
    metadata: JsonDict = Field(default_factory=dict)

    created_at: datetime | None = None
    updated_at: datetime | None = None


class ModelVersionRecord(SceneOpsBaseModel):
    id: str

    model_id: str
    version: str

    task_type: ModelTaskType = ModelTaskType.DETECTION

    backend: ModelBackend = ModelBackend.MOCK
    status: ModelVersionStatus = ModelVersionStatus.REGISTERED

    model_uri: str | None = None
    endpoint_url: str | None = None
    artifact_manifest_uri: str | None = None

    runtime: JsonDict = Field(default_factory=dict)
    metadata: JsonDict = Field(default_factory=dict)

    created_at: datetime | None = None
    updated_at: datetime | None = None
