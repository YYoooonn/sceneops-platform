from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import SceneOpsBaseModel, JsonDict
from .enums import ModelBackend, ModelVersionStatus


class CreateModelRequest(SceneOpsBaseModel):
    model_id: str
    # task_type: ModelTaskType = Field(default=ModelTaskType.DETECTION)
    name: str | None = None
    description: str | None = None
    metadata: JsonDict = Field(default_factory=dict)


class UpsertModelRequest(SceneOpsBaseModel):
    name: str | None = None
    description: str | None = None
    metadata: JsonDict = Field(default_factory=dict)


class CreateModelVersionRequest(SceneOpsBaseModel):
    version: str
    backend: ModelBackend = ModelBackend.MOCK
    status: ModelVersionStatus = ModelVersionStatus.REGISTERED
    model_uri: str | None = None
    endpoint_url: str | None = None
    runtime: JsonDict = Field(default_factory=dict)
    metadata: JsonDict = Field(default_factory=dict)


class UpsertModelVersionRequest(SceneOpsBaseModel):
    backend: ModelBackend = ModelBackend.MOCK
    status: ModelVersionStatus = ModelVersionStatus.REGISTERED
    model_uri: str | None = None
    endpoint_url: str | None = None
    runtime: JsonDict = Field(default_factory=dict)
    metadata: JsonDict = Field(default_factory=dict)
