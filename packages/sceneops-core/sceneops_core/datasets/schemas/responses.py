from __future__ import annotations

from sceneops_core.common.schemas import SceneOpsBaseModel
from .records import DatasetRecord, DatasetVersionRecord


class DatasetListResponse(SceneOpsBaseModel):
    datasets: list[DatasetRecord]
    count: int


class DatasetDetailResponse(SceneOpsBaseModel):
    dataset: DatasetRecord


class DatasetVersionListResponse(SceneOpsBaseModel):
    versions: list[DatasetVersionRecord]
    count: int


class DatasetVersionDetailResponse(SceneOpsBaseModel):
    version: DatasetVersionRecord
