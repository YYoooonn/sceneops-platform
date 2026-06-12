from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel

from .enums import RawLogSourceFormat


class RegisterRawLogRequest(SceneOpsBaseModel):
    raw_log_id: str | None = None

    dataset_id: str
    dataset_version: str
    dataset_type: str

    source_format: RawLogSourceFormat
    root_uri: str

    channels: list[str] = Field(default_factory=list)
    metadata: JsonDict = Field(default_factory=dict)
