"""SceneRecord -- the canonical Scene unit as DatasetVersion membership
(ADR-007 §13.3).

A SceneRecord is a queryable projection of exactly one registered
SceneManifest revision, named by ``manifest_artifact_id``. Everything it
holds besides membership and that pointer is derivable from the manifest;
the manifest stays the only place the unit can be reconstructed from.

There is no status: the record exists if and only if the unit is
registered. Validation, profiling and readiness live in run records that
pin the revision they assessed. Only the Scene registrar writes records.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import ConfigDict, Field, model_validator

from sceneops_core.common.schemas import SceneOpsBaseModel, to_camel
from sceneops_core.provenance import RecordingSegmentSource, canonical_unit_id

from .manifests import SceneManifest


def scene_id_for(
    *, dataset_id: str, dataset_version: str, source: RecordingSegmentSource
) -> str:
    """Deterministic, DatasetVersion-scoped Scene identity (ADR-007 §18.1)."""
    return canonical_unit_id(
        domain="scene",
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        source=source,
    )


class SceneRecord(SceneOpsBaseModel):
    model_config = ConfigDict(
        populate_by_name=True, alias_generator=to_camel, extra="forbid"
    )

    scene_id: str
    dataset_id: str
    dataset_version: str

    # The source projection: the RobotRun whose recording the Scene was
    # built from, and the producer's unit key within it.
    robot_run_id: str
    unit_key: str

    producer_fingerprint: str

    manifest_artifact_id: str
    manifest_checksum: str

    # The segment window, [start, end) in window_clock (the producer's
    # declared segmentation clock). See SceneManifest.declared_window().
    window_clock: str
    window_start_timestamp_ns: int
    window_end_timestamp_ns: int

    # Source channels with at least one observation; a declared channel
    # with none stays visible only in the manifest.
    observed_channels: list[str] = Field(default_factory=list)
    observation_count: int
    keyframe_count: int
    annotation_count: int

    registered_at: datetime | None = None
    updated_at: datetime | None = None

    @model_validator(mode="after")
    def _check_window(self) -> SceneRecord:
        if self.window_end_timestamp_ns <= self.window_start_timestamp_ns:
            raise ValueError("window must be non-empty")
        return self

    @property
    def has_ground_truth(self) -> bool:
        return self.annotation_count > 0

    def pins(self, *, manifest_artifact_id: str, manifest_checksum: str) -> bool:
        """True if this record's current revision is exactly the given one."""
        return (
            self.manifest_artifact_id == manifest_artifact_id
            and self.manifest_checksum == manifest_checksum
        )


def project_scene_record(
    *,
    dataset_id: str,
    dataset_version: str,
    manifest: SceneManifest,
    manifest_artifact_id: str,
    manifest_checksum: str,
) -> SceneRecord:
    source = manifest.lineage.source
    window = manifest.declared_window()
    return SceneRecord(
        scene_id=scene_id_for(
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
        observed_channels=manifest.observed_channel_names(),
        observation_count=len(manifest.observations),
        keyframe_count=len(manifest.keyframes()),
        annotation_count=len(manifest.annotations),
    )


__all__ = ["SceneRecord", "project_scene_record", "scene_id_for"]
