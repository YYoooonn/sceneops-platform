from __future__ import annotations

from sceneops_core.common.schemas import SceneOpsBaseModel

from .manifests import SceneManifest
from .records import SceneRecord, SceneSampleRecord
from .segments import SceneSegment, SceneSegmentIndex


class SceneDetailResponse(SceneOpsBaseModel):
    scene: SceneRecord


class SceneListResponse(SceneOpsBaseModel):
    scenes: list[SceneRecord]
    count: int


class SceneManifestResponse(SceneOpsBaseModel):
    manifest: SceneManifest


class SceneSampleListResponse(SceneOpsBaseModel):
    samples: list[SceneSampleRecord]
    count: int


class SceneSegmentListResponse(SceneOpsBaseModel):
    segments: list[SceneSegment]
    count: int


class SceneSegmentIndexResponse(SceneOpsBaseModel):
    segment_index: SceneSegmentIndex
