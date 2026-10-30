from __future__ import annotations

from datetime import datetime

from pydantic import ConfigDict, Field

from sceneops_core.common.checksums import SHA256_CHECKSUM_PATTERN
from sceneops_core.common.schemas import SceneOpsBaseModel, to_camel

from .selectors import RevisionSelector
from .summaries import EpisodeVersionSummary, SceneVersionSummary


class DatasetRecord(SceneOpsBaseModel):
    """A Dataset is a namespace of DatasetVersions with mutable display
    metadata; it is not versioned content."""

    dataset_id: str
    name: str | None = None
    description: str | None = None

    default_version: str | None = None

    created_at: datetime | None = None
    updated_at: datetime | None = None


class DatasetVersionRecord(SceneOpsBaseModel):
    """One committed DatasetVersion: a name bound once to an immutable
    composition (ADR-015 §7, MS-8).

    The row exists only as a complete composition: it is written in one
    transaction with all its members by COMPOSE_DATASET_VERSION and never
    changed afterwards, so it has no status and no update time.
    ``composition_checksum`` identifies the member pins
    (``sceneops_core.datasets.composition``); ``selector`` is how they were
    chosen, or None when the composing Job was given explicit pins.

    Scene-owned and Episode-owned summaries live only under
    ``scene``/``episode`` and are None when the version has no member of
    that domain. A DatasetVersion relates to RobotRuns and sources only
    through its units' provenance; it carries no source location or source
    format. The underlying ``dataset_versions`` SQL row is flat; converters
    in ``sceneops_db.converters.datasets`` map between the two shapes.

    ``extra="forbid"`` is deliberate here: a stale call site that still passes
    a removed field (``status``, ``scene_count``, ``metadata``, ...) must fail
    loudly instead of being silently dropped.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        alias_generator=to_camel,
        extra="forbid",
    )

    dataset_id: str
    version: str

    composition_checksum: str = Field(pattern=SHA256_CHECKSUM_PATTERN)
    selector: RevisionSelector | None = None

    created_at: datetime | None = None

    # ── Domain summaries ────────────────────────────────────────────────────
    scene: SceneVersionSummary | None = None
    episode: EpisodeVersionSummary | None = None
