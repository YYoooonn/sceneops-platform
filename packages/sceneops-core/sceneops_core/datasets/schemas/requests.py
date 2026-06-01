from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import SceneOpsBaseModel, JsonDict
from .enums import DatasetType, DatasetVersionStatus


class CreateDatasetRequest(SceneOpsBaseModel):
    id: str
    name: str | None = None
    dataset_type: DatasetType | str = DatasetType.CUSTOM
    description: str | None = None
    metadata: JsonDict = Field(default_factory=dict)


class UpsertDatasetRequest(SceneOpsBaseModel):
    name: str | None = None
    dataset_type: DatasetType | str = DatasetType.CUSTOM
    description: str | None = None
    metadata: JsonDict = Field(default_factory=dict)


class CreateDatasetVersionRequest(SceneOpsBaseModel):
    version: str
    dataset_type: DatasetType | str | None = None

    source_uri: str | None = None

    metadata: JsonDict = Field(default_factory=dict)


class UpsertDatasetVersionRequest(SceneOpsBaseModel):
    dataset_type: DatasetType | str | None = None

    source_uri: str | None = None
    manifest_uri: str | None = None

    scene_count: int | None = None
    sample_count: int | None = None
    annotation_count: int | None = None

    status: DatasetVersionStatus = DatasetVersionStatus.REGISTERED
    metadata: JsonDict = Field(default_factory=dict)
