from __future__ import annotations

from datetime import datetime

from pydantic import ConfigDict, Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel, to_camel

from .enums import DatasetType, DatasetVersionStatus
from .summaries import EpisodeVersionSummary, SceneVersionSummary


class DatasetRecord(SceneOpsBaseModel):
    dataset_id: str
    name: str | None = None
    description: str | None = None

    type: DatasetType = DatasetType.CUSTOM

    default_version: str | None = None

    created_at: datetime | None = None
    updated_at: datetime | None = None

    metadata: JsonDict = Field(default_factory=dict)


class DatasetVersionRecord(SceneOpsBaseModel):
    """Platform-generic DatasetVersion identity/state, plus per-domain summaries.

    SceneOps V2 Request 03 cutover: Scene-owned and Episode-owned fields
    (counts, channels, manifest_uri, quality-cache pointers) live exclusively
    under ``scene``/``episode`` now — there is no flat top-level duplicate.
    Request 04 additionally moved ``raw_source_root_uri`` into
    ``scene`` (it was only ever used by the Scene raw-log build path —
    Episode sources come from ``RobotRun.mcap_uri`` instead) and dropped the
    dead ``latest_distribution_run_id``/``distribution_report_uri`` fields
    entirely (no writer/reader ever existed; see the Request 01/04 audits).
    The underlying ``dataset_versions`` SQL row is still flat; converters in
    ``sceneops_db.converters.datasets`` are the compatibility boundary (see
    ``dataset_version_model_to_record`` / ``dataset_version_record_to_values``).

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

    source_dataset_id: str | None = None
    source_dataset_version: str | None = None

    created_at: datetime | None = None
    updated_at: datetime | None = None

    metadata: JsonDict = Field(default_factory=dict)

    # ── Domain summaries ────────────────────────────────────────────────────
    # None means "this domain has no declared config or build activity on
    # this version" — it is NOT a claim that the domain's data was actually
    # built. See SceneVersionSummary/EpisodeVersionSummary docstrings.
    scene: SceneVersionSummary | None = None
    episode: EpisodeVersionSummary | None = None
