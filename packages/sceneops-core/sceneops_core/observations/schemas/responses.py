from __future__ import annotations

from sceneops_core.common.schemas import SceneOpsBaseModel

from .raw_logs import RawLogFrameIndex, RawLogManifest


class RawLogDetailResponse(SceneOpsBaseModel):
    raw_log: RawLogManifest


class RawLogListResponse(SceneOpsBaseModel):
    raw_logs: list[RawLogManifest]
    count: int


class RawLogFrameIndexResponse(SceneOpsBaseModel):
    frame_index: RawLogFrameIndex
