from __future__ import annotations

from datetime import datetime

from pydantic import ConfigDict, Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel, to_camel

from .enums import DatasetVersionStatus
from .summaries import EpisodeVersionSummary, SceneVersionSummary


class DatasetRecord(SceneOpsBaseModel):
    dataset_id: str
    name: str | None = None
    description: str | None = None

    default_version: str | None = None

    created_at: datetime | None = None
    updated_at: datetime | None = None

    metadata: JsonDict = Field(default_factory=dict)


class DatasetVersionRecord(SceneOpsBaseModel):
    """Platform-generic DatasetVersion identity/state, plus per-domain summaries.

    Scene-owned and Episode-owned fields (counts, channels, manifest_uri)
    live only under ``scene``/``episode``. A DatasetVersion relates to
    RobotRuns and sources only through its units' provenance; it carries
    no source location or source format (ADR-007 §16, §29.16). The
    underlying ``dataset_versions`` SQL row is flat; converters in
    ``sceneops_db.converters.datasets`` map between the two shapes.

    ``extra="forbid"`` is deliberate here: this record used to expose
    ``scene_count``, ``manifest_uri``, etc. directly, and silently accepting
    those as unknown kwargs (pydantic's default) would hide exactly the kind
    of stale call site this cutover needs to surface loudly instead.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        alias_generator=to_camel,
        extra="forbid",
    )

    dataset_id: str
    version: str

    status: DatasetVersionStatus = DatasetVersionStatus.REGISTERED

    created_at: datetime | None = None
    updated_at: datetime | None = None

    metadata: JsonDict = Field(default_factory=dict)

    # ── Domain summaries ────────────────────────────────────────────────────
    # None means "this domain has no declared config or build activity on
    # this version" — it is NOT a claim that the domain's data was actually
    # built. See SceneVersionSummary/EpisodeVersionSummary docstrings.
    scene: SceneVersionSummary | None = None
    episode: EpisodeVersionSummary | None = None
