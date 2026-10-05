from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict

from .base import BaseJobResult


class ExportAnalyticsSnapshotJobResult(BaseJobResult):
    dataset_id: str | None = None
    dataset_version: str | None = None

    table_uris: dict[str, str] = Field(default_factory=dict)
    row_counts: dict[str, int] = Field(default_factory=dict)

    scene_count: int = 0

    metadata: JsonDict = Field(default_factory=dict)
