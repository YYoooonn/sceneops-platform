from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict

from .base import BaseJobResult


class ComposeDatasetVersionJobResult(BaseJobResult):
    """The committed DatasetVersion. ``created`` is False when the same
    composition was already committed under this name (a duplicate request,
    or a re-run after the composition committed but the Job did not record
    success); nothing was written then."""

    dataset_id: str
    dataset_version: str
    composition_checksum: str
    created: bool

    member_count: int
    scene_count: int = 0
    episode_count: int = 0

    metadata: JsonDict = Field(default_factory=dict)


class ExportAnalyticsSnapshotJobResult(BaseJobResult):
    dataset_id: str | None = None
    dataset_version: str | None = None

    table_uris: dict[str, str] = Field(default_factory=dict)
    row_counts: dict[str, int] = Field(default_factory=dict)

    scene_count: int = 0

    metadata: JsonDict = Field(default_factory=dict)
