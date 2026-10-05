from __future__ import annotations

from datetime import datetime

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel


class ScenarioSetRecord(SceneOpsBaseModel):
    """Queryable projection of one ScenarioSet revision.

    ``manifest_artifact_id`` + ``manifest_checksum`` pin the revision (the
    manifest is the only place membership lives); ``scenario_set_uri`` is
    where those immutable bytes are. A set is immutable once written, so
    its record never repoints.
    """

    scenario_set_id: str

    dataset_id: str | None = None
    dataset_version: str | None = None

    name: str | None = None
    description: str | None = None

    scenario_set_uri: str | None = None
    manifest_artifact_id: str | None = None
    manifest_checksum: str | None = None

    scenario_count: int = 0
    tags: list[str] = Field(default_factory=list)

    created_at: datetime | None = None
    updated_at: datetime | None = None

    metadata: JsonDict = Field(default_factory=dict)
