from __future__ import annotations

from pydantic import Field

from sceneops_core.schemas.base import SceneOpsBaseModel
from sceneops_core.schemas.common import JsonDict
from sceneops_core.schemas.models.enums import ModelBackend, ModelVersionStatus


class CreateModelRequest(SceneOpsBaseModel):
    id: str
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
