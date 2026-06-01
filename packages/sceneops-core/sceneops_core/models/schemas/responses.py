from __future__ import annotations

from sceneops_core.common.schemas import SceneOpsBaseModel
from .records import ModelRecord, ModelVersionRecord


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
