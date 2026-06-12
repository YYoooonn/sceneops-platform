from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel

from .enums import DatasetType, DatasetVersionStatus


class CreateDatasetRequest(SceneOpsBaseModel):
    dataset_id: str
    name: str | None = None
    description: str | None = None

    type: DatasetType = DatasetType.CUSTOM

    metadata: JsonDict = Field(default_factory=dict)


class CreateDatasetVersionRequest(SceneOpsBaseModel):
    dataset_id: str
    version: str

    status: DatasetVersionStatus = DatasetVersionStatus.REGISTERED

    manifest_uri: str | None = None

    source_dataset_id: str | None = None
    source_dataset_version: str | None = None

    metadata: JsonDict = Field(default_factory=dict)


class RegisterDatasetManifestRequest(SceneOpsBaseModel):
    dataset_id: str
    dataset_version: str

    manifest_uri: str

    metadata: JsonDict = Field(default_factory=dict)


class GetDatasetRequest(SceneOpsBaseModel):
    dataset_id: str


class GetDatasetVersionRequest(SceneOpsBaseModel):
    dataset_id: str
    version: str
