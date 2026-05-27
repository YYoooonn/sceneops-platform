from __future__ import annotations

from sceneops_core.schemas.base import SceneOpsBaseModel
from sceneops_core.schemas.datasets.records import DatasetRecord, DatasetVersionRecord


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
