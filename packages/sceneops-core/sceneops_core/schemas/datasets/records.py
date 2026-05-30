from __future__ import annotations

from datetime import datetime

from pydantic import Field

from sceneops_core.schemas.base import SceneOpsBaseModel
from sceneops_core.schemas.common import JsonDict
from sceneops_core.schemas.datasets.enums import DatasetType, DatasetVersionStatus


class DatasetRecord(SceneOpsBaseModel):
    id: str
    name: str | None = None
    dataset_type: DatasetType | str = DatasetType.CUSTOM
    description: str | None = None
    metadata: JsonDict = Field(default_factory=dict)

    created_at: datetime | None = None
    updated_at: datetime | None = None


class DatasetVersionRecord(SceneOpsBaseModel):
    id: str

    dataset_id: str
    version: str

    dataset_type: DatasetType | str = DatasetType.CUSTOM
    status: DatasetVersionStatus = DatasetVersionStatus.REGISTERED

    source_uri: str | None = None
    manifest_uri: str | None = None

    scene_count: int | None = None
    sample_count: int | None = None
    annotation_count: int | None = None

    metadata: JsonDict = Field(default_factory=dict)

    created_at: datetime | None = None
    updated_at: datetime | None = None
