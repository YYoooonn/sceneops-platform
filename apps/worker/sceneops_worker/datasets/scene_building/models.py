from __future__ import annotations

from dataclasses import dataclass

from sceneops_core.datasets.schemas import SensorModality, SensorFrameRole


@dataclass(frozen=True)
class IndexedRawFrame:
    frame_id: str
    timestamp_us: int

    channel: str
    modality: SensorModality
    role: SensorFrameRole

    uri: str

    source_sample_id: str | None = None
    source_scene_id: str | None = None
    ego_pose_ref: str | None = None
    calibration_ref: str | None = None
    annotation_refs: tuple[str, ...] = ()
