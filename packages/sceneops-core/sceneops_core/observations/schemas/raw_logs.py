from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel

from .enums import RawLogSourceFormat, RawLogSourceType
from .frames import RawSensorFrameManifest, TimeRange


class RawLogManifest(SceneOpsBaseModel):
    raw_log_id: str

    dataset_id: str
    dataset_version: str
    dataset_type: str

    source_format: RawLogSourceFormat
    root_uri: str

    time_range: TimeRange | None = None
    channels: list[str] = Field(default_factory=list)
    frame_count: int = 0

    frame_index_uri: str | None = None

    # Extended for raw-log source classification
    source_type: RawLogSourceType | None = None
    records_uri: str | None = None
    observation_count: int = 0
    source_sequence_count: int = 0

    metadata: JsonDict = Field(default_factory=dict)


class RawLogFrameIndex(SceneOpsBaseModel):
    raw_log_id: str
    dataset_id: str
    dataset_version: str

    frames: list[RawSensorFrameManifest] = Field(default_factory=list)

    metadata: JsonDict = Field(default_factory=dict)
