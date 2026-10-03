from __future__ import annotations

from datetime import datetime

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel

from .enums import DatasetManifestStatus, DatasetSplit


class DatasetSceneIndexEntry(SceneOpsBaseModel):
    """One registered Scene as a derived dataset index sees it.

    The entry pins the exact manifest revision it indexed
    (``manifest_artifact_id`` + ``manifest_checksum``); ``manifest_uri`` is
    the location of those immutable bytes, and a reader verifies the
    checksum before using them. A consumer of this index must not assume the
    Scene still points at the same revision (ADR-007 §18.5).
    """

    scene_id: str

    manifest_artifact_id: str
    manifest_checksum: str
    manifest_uri: str

    split: DatasetSplit = DatasetSplit.UNASSIGNED

    keyframe_count: int = 0
    observation_count: int = 0
    annotation_count: int = 0
    observed_channels: list[str] = Field(default_factory=list)

    # The Scene's declared source window, where it has one (see SceneRecord).
    window_clock: str | None = None
    window_start_timestamp_ns: int | None = None
    window_end_timestamp_ns: int | None = None

    tags: list[str] = Field(default_factory=list)

    metadata: JsonDict = Field(default_factory=dict)


class DatasetManifest(SceneOpsBaseModel):
    """Derived snapshot of a DatasetVersion's registered Scenes. Never the
    source of membership."""

    dataset_id: str
    dataset_version: str

    status: DatasetManifestStatus = DatasetManifestStatus.READY

    scene_count: int = 0
    keyframe_count: int = 0
    observation_count: int = 0

    observed_channels: list[str] = Field(default_factory=list)

    scenes: list[DatasetSceneIndexEntry] = Field(default_factory=list)

    validation_summary: JsonDict = Field(default_factory=dict)
    profile_summary: JsonDict = Field(default_factory=dict)

    created_at: datetime | None = None

    metadata: JsonDict = Field(default_factory=dict)
