from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import SceneOpsBaseModel

from .validation import DatasetValidationStatus


class SceneVersionSummary(SceneOpsBaseModel):
    """Scene-domain state for one DatasetVersion — the authoritative home for
    these fields as of SceneOps V2 Request 03/04 (no flat duplicates remain
    on DatasetVersionRecord).

    Groups build-time counts/channels, the raw source root for the Scene
    build path (Request 04 — Episode sources come from RobotRun.mcap_uri
    instead, so this never belonged as generic DatasetVersion state), and
    the latest validate_scene/profile_scene quality-cache pointers.
    Distribution fields are not present anywhere — nothing in the codebase
    ever wrote them, and they were dropped from DatasetVersionRecord
    entirely in Request 04 rather than being moved here.

    IMPORTANT: a non-None ``scene`` on DatasetVersionRecord is not proof that
    Scene data was ever built. ``required_channels``/``manifest_uri``/
    ``raw_source_root_uri`` can be set via the dataset-version API before any
    scene job has run, which is enough to make this summary non-default (see
    ``is_unset()``). To check whether Scene data actually exists, look at a
    concrete signal — ``scene_count > 0``, ``manifest_uri is not None``, or
    query SceneRecord rows directly — not `version.scene is not None` by
    itself.
    """

    scene_count: int = 0
    sample_count: int = 0
    frame_count: int = 0

    channels: list[str] = Field(default_factory=list)
    required_channels: list[str] = Field(default_factory=list)

    manifest_uri: str | None = None
    raw_source_root_uri: str | None = None

    latest_validation_run_id: str | None = None
    validation_status: DatasetValidationStatus | None = None
    should_block_pipeline: bool | None = None
    validation_report_uri: str | None = None

    latest_profile_run_id: str | None = None
    profile_report_uri: str | None = None

    def is_unset(self) -> bool:
        """True if every field is still at its untouched default.

        Named "unset", not "empty" — this only says nothing has configured
        or written into this summary yet. It says nothing about whether
        Scene *data* exists (see class docstring). There is no stored "has
        scene activity" flag on the DatasetVersion row; this is inferred
        from the flat columns underneath until/unless that changes.
        """
        return self == SceneVersionSummary()


class EpisodeVersionSummary(SceneOpsBaseModel):
    """Episode-domain state for one DatasetVersion — the authoritative home
    for these fields as of SceneOps V2 Request 03.

    Deliberately minimal for now — only episode_count exists today (written
    by BuildEpisodesJobHandler). Do not add speculative fields here ahead of
    the workflows that would populate them.

    Same caveat as SceneVersionSummary: non-None does not by itself prove
    Episode data exists — check ``episode_count > 0`` or query EpisodeRecord
    rows when presence actually matters.
    """

    episode_count: int = 0

    def is_unset(self) -> bool:
        return self == EpisodeVersionSummary()
