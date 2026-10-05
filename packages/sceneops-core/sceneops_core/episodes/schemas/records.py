"""EpisodeRecord -- the canonical Episode unit as DatasetVersion membership
(ADR-007 §13.3, §31).

An EpisodeRecord is a queryable projection of exactly one registered
EpisodeManifest revision, named by ``manifest_artifact_id``. Everything it
holds besides membership and that pointer is derivable from the manifest.

There is no status, task or outcome: the record exists if and only if the
unit is registered, and readiness lives in run records that pin the
revision they assessed. Only the Episode registrar writes records.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import ConfigDict, Field, model_validator

from sceneops_core.common.schemas import SceneOpsBaseModel, to_camel
from sceneops_core.provenance import RecordingSegmentSource, canonical_unit_id

from ..recording_build import EpisodeStreamRole
from .manifests import EpisodeManifest


def episode_id_for(
    *, dataset_id: str, dataset_version: str, source: RecordingSegmentSource
) -> str:
    """Deterministic, DatasetVersion-scoped Episode identity (ADR-007 §18.1)."""
    return canonical_unit_id(
        domain="episode",
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        source=source,
    )


class EpisodeRecord(SceneOpsBaseModel):
    model_config = ConfigDict(
        populate_by_name=True, alias_generator=to_camel, extra="forbid"
    )

    episode_id: str
    dataset_id: str
    dataset_version: str

    # The source projection: the RobotRun whose recording the Episode was
    # built from, and the producer's unit key within it.
    robot_run_id: str
    unit_key: str

    producer_fingerprint: str

    manifest_artifact_id: str
    manifest_checksum: str

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

    observation_count: int
    state_count: int
    action_count: int
    event_count: int

    registered_at: datetime | None = None
    updated_at: datetime | None = None

    @model_validator(mode="after")
    def _check_window(self) -> EpisodeRecord:
        if self.window_end_timestamp_ns <= self.window_start_timestamp_ns:
            raise ValueError("window must be non-empty")
        return self

    def pins(self, *, manifest_artifact_id: str, manifest_checksum: str) -> bool:
        return (
            self.manifest_artifact_id == manifest_artifact_id
            and self.manifest_checksum == manifest_checksum
        )


def project_episode_record(
    *,
    dataset_id: str,
    dataset_version: str,
    manifest: EpisodeManifest,
    manifest_artifact_id: str,
    manifest_checksum: str,
) -> EpisodeRecord:
    source = manifest.lineage.source
    window = manifest.declared_window()
    return EpisodeRecord(
        episode_id=episode_id_for(
            dataset_id=dataset_id, dataset_version=dataset_version, source=source
        ),
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        robot_run_id=source.robot_run_id,
        unit_key=source.unit_key,
        producer_fingerprint=manifest.lineage.producer.producer_fingerprint,
        manifest_artifact_id=manifest_artifact_id,
        manifest_checksum=manifest_checksum,
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


__all__ = ["EpisodeRecord", "episode_id_for", "project_episode_record"]
