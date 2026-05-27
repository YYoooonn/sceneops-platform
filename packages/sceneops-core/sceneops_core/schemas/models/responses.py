from __future__ import annotations

from sceneops_core.schemas.base import SceneOpsBaseModel
from sceneops_core.schemas.models.records import ModelRecord, ModelVersionRecord


class ModelListResponse(SceneOpsBaseModel):
    models: list[ModelRecord]
    count: int


class ModelDetailResponse(SceneOpsBaseModel):
    model: ModelRecord


class ModelVersionListResponse(SceneOpsBaseModel):
    versions: list[ModelVersionRecord]
    count: int


class ModelVersionDetailResponse(SceneOpsBaseModel):
    version: ModelVersionRecord
