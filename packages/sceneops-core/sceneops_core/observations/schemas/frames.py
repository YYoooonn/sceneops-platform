from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel
from sceneops_core.sensors import SensorModality


class RawSensorFrameManifest(SceneOpsBaseModel):
    """One sensor message as the Episode recording read sees it
    (``EpisodeSource.frames``). Not a canonical Scene observation."""

    frame_id: str
    timestamp_us: int

    channel: str
    modality: SensorModality
    uri: str

    sequence_id: str | None = None
    sensor_id: str | None = None

    metadata: JsonDict = Field(default_factory=dict)
