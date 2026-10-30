from __future__ import annotations

from sceneops_core.common.schemas import SceneOpsBaseModel
from sceneops_core.sources import SourceContentRecord, SourceSnapshotRecord


class SourceSnapshotDetailResponse(SceneOpsBaseModel):
    source_snapshot: SourceSnapshotRecord


class SourceSnapshotListResponse(SceneOpsBaseModel):
    source_snapshots: list[SourceSnapshotRecord]
    count: int


class SourceContentDetailResponse(SceneOpsBaseModel):
    source_content: SourceContentRecord
