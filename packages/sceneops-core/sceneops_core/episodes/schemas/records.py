"""Episode unit and Episode revision records (ADR-015 §3, §6.4).

    EpisodeRecord           the canonical unit: its id and source binding;
                            immutable once registered
    EpisodeRevisionRecord   one registered EpisodeManifest revision of a unit,
                            ``(episode_id, manifest_checksum)``, with the
                            queryable projection of that manifest; immutable
                            once registered

A unit has one or more revisions and no "current" one: every consumer
addresses a revision. Neither record holds DatasetVersion membership; a
DatasetVersion member pins one revision
(``sceneops_core.datasets.schemas.DatasetVersionMember``). There is no
status, task or outcome: readiness lives in run records that pin the
revision they assessed. Only the Episode registrar writes either record.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import ConfigDict, Field, model_validator

from sceneops_core.common.schemas import SceneOpsBaseModel, to_camel
from sceneops_core.provenance import (
    ContentUnitSource,
    RecordingSegmentSource,
    RevisionPin,
    canonical_unit_id,
    check_revision_pin,
    unit_id_for,
)

from ..recording_build import EpisodeStreamRole
from .manifests import EpisodeManifest


def episode_id_for(source: RecordingSegmentSource | ContentUnitSource) -> str:
    """Deterministic Episode unit identity from its source binding alone
    (ADR-015 §3): the same source unit is one Episode in every DatasetVersion
    that references it."""
    return canonical_unit_id(domain="episode", source=source)


def _check_unit_identity(episode_id: str, robot_run_id: str, unit_key: str) -> None:
    expected = unit_id_for(
        domain="episode",
        source_kind="recording",
        source_id=robot_run_id,
        unit_key=unit_key,
    )
    if episode_id != expected:
        raise ValueError(
            f"episode_id {episode_id!r} is not the unit of RobotRun "
            f"{robot_run_id!r} unit_key {unit_key!r}; expected {expected!r}"
        )


class EpisodeRecord(SceneOpsBaseModel):
    """The canonical Episode unit. Its id is a function of its source
    binding, which the record keeps: the RobotRun whose recording is the
    source and the producer's unit key within it."""

    model_config = ConfigDict(
        populate_by_name=True, alias_generator=to_camel, extra="forbid"
    )

    episode_id: str
    source_kind: Literal["recording"] = "recording"
    robot_run_id: str
    unit_key: str

    registered_at: datetime | None = None

    @model_validator(mode="after")
    def _check_identity(self) -> EpisodeRecord:
        _check_unit_identity(self.episode_id, self.robot_run_id, self.unit_key)
        return self


class EpisodeRevisionRecord(SceneOpsBaseModel):
    """One registered EpisodeManifest revision of an Episode unit.

    ``manifest_artifact_id`` is the revision's deterministic manifest
    ArtifactRecord id. The unit binding (``source_kind``, ``robot_run_id``,
    ``unit_key``) is the unit's, carried so a revision is self-describing;
    it is stored once, on the unit."""

    model_config = ConfigDict(
        populate_by_name=True, alias_generator=to_camel, extra="forbid"
    )

    episode_id: str
    source_kind: Literal["recording"] = "recording"
    robot_run_id: str
    unit_key: str

    manifest_artifact_id: str
    manifest_checksum: str
    producer_fingerprint: str

    # The episode window, [start, end) in window_clock (the producer's
    # declared segmentation clock).
    window_clock: str
    window_start_timestamp_ns: int
    window_end_timestamp_ns: int

    # Topics of each role with at least one occurrence in the Episode.
    observation_topics: list[str] = Field(default_factory=list)
    state_topics: list[str] = Field(default_factory=list)
    action_topics: list[str] = Field(default_factory=list)
    event_topics: list[str] = Field(default_factory=list)

    observation_count: int = Field(ge=0)
    state_count: int = Field(ge=0)
    action_count: int = Field(ge=0)
    event_count: int = Field(ge=0)

    registered_at: datetime | None = None

    @model_validator(mode="after")
    def _check_revision(self) -> EpisodeRevisionRecord:
        if self.window_end_timestamp_ns <= self.window_start_timestamp_ns:
            raise ValueError("window must be non-empty")
        _check_unit_identity(self.episode_id, self.robot_run_id, self.unit_key)
        check_revision_pin(
            unit_id=self.episode_id,
            manifest_artifact_id=self.manifest_artifact_id,
            manifest_checksum=self.manifest_checksum,
        )
        return self

    def unit(self) -> EpisodeRecord:
        return EpisodeRecord(
            episode_id=self.episode_id,
            source_kind=self.source_kind,
            robot_run_id=self.robot_run_id,
            unit_key=self.unit_key,
        )

    def projection(self) -> dict[str, Any]:
        """Everything the manifest determines: two records of one revision
        must agree on all of it."""
        return self.model_dump(exclude={"registered_at"})

    def pins(self, *, manifest_artifact_id: str, manifest_checksum: str) -> bool:
        return (
            self.manifest_artifact_id == manifest_artifact_id
            and self.manifest_checksum == manifest_checksum
        )

    def pin(self) -> RevisionPin:
        return RevisionPin(
            domain="episode",
            unit_id=self.episode_id,
            manifest_artifact_id=self.manifest_artifact_id,
            manifest_checksum=self.manifest_checksum,
        )


def project_episode_revision(
    *,
    manifest: EpisodeManifest,
    manifest_artifact_id: str,
    manifest_checksum: str,
) -> EpisodeRevisionRecord:
    source = manifest.lineage.source
    window = manifest.declared_window()
    return EpisodeRevisionRecord(
        episode_id=episode_id_for(source),
        source_kind=source.source_kind,
        robot_run_id=source.robot_run_id,
        unit_key=source.unit_key,
        manifest_artifact_id=manifest_artifact_id,
        manifest_checksum=manifest_checksum,
        producer_fingerprint=manifest.lineage.producer.producer_fingerprint,
        window_clock=window.source_clock,
        window_start_timestamp_ns=window.start_timestamp_ns,
        window_end_timestamp_ns=window.end_timestamp_ns,
        observation_topics=manifest.observed_topics(EpisodeStreamRole.OBSERVATION),
        state_topics=manifest.observed_topics(EpisodeStreamRole.STATE),
        action_topics=manifest.observed_topics(EpisodeStreamRole.ACTION),
        event_topics=manifest.observed_topics(EpisodeStreamRole.EVENT),
        observation_count=len(manifest.observations),
        state_count=len(manifest.states),
        action_count=len(manifest.actions),
        event_count=len(manifest.events),
    )


__all__ = [
    "EpisodeRecord",
    "EpisodeRevisionRecord",
    "episode_id_for",
    "project_episode_revision",
]
