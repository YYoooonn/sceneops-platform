from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict

from .base import BaseJobParams


class ExportAnalyticsSnapshotJobParams(BaseJobParams):
    dataset_id: str
    dataset_version: str

    # None → export all known tables (scenes, observations, keyframes, annotations)
    tables: list[str] | None = None

    metadata: JsonDict = Field(default_factory=dict)
