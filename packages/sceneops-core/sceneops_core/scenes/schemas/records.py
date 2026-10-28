"""Scene unit and Scene revision records (ADR-015 §3, §6.4).

    SceneRecord           the canonical unit: its id and source binding;
                          immutable once registered
    SceneRevisionRecord   one registered SceneManifest revision of a unit,
                          ``(scene_id, manifest_checksum)``, with the queryable
                          projection of that manifest; immutable once
                          registered

A unit has one or more revisions and no "current" one: every consumer
addresses a revision. Neither record holds DatasetVersion membership; a
DatasetVersion member pins one revision
(``sceneops_core.datasets.schemas.DatasetVersionMember``). There is no
status: validation, profiling and readiness live in run records that pin
the revision they assessed. Only the Scene registrar writes either record.

Everything a revision record holds besides its pin is derivable from the
manifest, which stays the only place the unit can be reconstructed from.
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

from .manifests import SceneManifest


def scene_id_for(source: RecordingSegmentSource | ContentUnitSource) -> str:
    """Deterministic Scene unit identity from its source binding alone
    (ADR-015 §3): the same source unit is one Scene in every DatasetVersion
    that references it."""
    return canonical_unit_id(domain="scene", source=source)


SourceKind = Literal["recording", "content"]


def _source_id(
    source_kind: str, robot_run_id: str | None, content_id: str | None
) -> str:
    """The unit's source identity: exactly the column of its source kind is
    set (the RobotRun for a recording, the SourceContent for content)."""
    expected, other = (
        (robot_run_id, content_id)
        if source_kind == "recording"
        else (content_id, robot_run_id)
    )
    if not expected or other is not None:
        raise ValueError(
            f"a {source_kind} Scene names its source by "
            f"{'robot_run_id' if source_kind == 'recording' else 'content_id'} only; "
            f"got robot_run_id={robot_run_id!r}, content_id={content_id!r}"
        )
    return expected


def _check_unit_identity(
    scene_id: str, source_kind: str, source_id: str, unit_key: str
) -> None:
    expected = unit_id_for(
        domain="scene",
        source_kind=source_kind,
        source_id=source_id,
        unit_key=unit_key,
    )
    if scene_id != expected:
        raise ValueError(
            f"scene_id {scene_id!r} is not the unit of {source_kind} source "
            f"{source_id!r} unit_key {unit_key!r}; expected {expected!r}"
        )


class SceneRecord(SceneOpsBaseModel):
    """The canonical Scene unit. Its id is a function of its source binding,
    which the record keeps: the source kind, the source (the RobotRun whose
    recording, or the SourceContent whose native scene, it is) and the unit
    key within it."""

    model_config = ConfigDict(
        populate_by_name=True, alias_generator=to_camel, extra="forbid"
    )

    scene_id: str
    source_kind: SourceKind
    robot_run_id: str | None = None
    content_id: str | None = None
    unit_key: str

    registered_at: datetime | None = None

    @model_validator(mode="after")
    def _check_identity(self) -> SceneRecord:
        _check_unit_identity(
            self.scene_id, self.source_kind, self.source_id, self.unit_key
        )
        return self

    @property
    def source_id(self) -> str:
        return _source_id(self.source_kind, self.robot_run_id, self.content_id)


class SceneRevisionRecord(SceneOpsBaseModel):
    """One registered SceneManifest revision of a Scene unit.

    ``manifest_artifact_id`` is the revision's deterministic manifest
    ArtifactRecord id. The unit binding (``source_kind``, ``robot_run_id`` or
    ``content_id``, ``unit_key``) is the unit's, carried so a revision is
    self-describing; it is stored once, on the unit."""

    model_config = ConfigDict(
        populate_by_name=True, alias_generator=to_camel, extra="forbid"
    )

    scene_id: str
    source_kind: SourceKind
    robot_run_id: str | None = None
    content_id: str | None = None
    unit_key: str

    manifest_artifact_id: str
    manifest_checksum: str
    producer_fingerprint: str

    # The unit window, [start, end) in window_clock (the producer's declared
    # segmentation clock, or the content's source clock). See
    # SceneManifest.declared_window().
    window_clock: str
    window_start_timestamp_ns: int
    window_end_timestamp_ns: int

    # Source channels with at least one observation; a declared channel
    # with none stays visible only in the manifest.
    observed_channels: list[str] = Field(default_factory=list)
    observation_count: int = Field(ge=0)
    keyframe_count: int = Field(ge=0)

    registered_at: datetime | None = None

    @property
    def source_id(self) -> str:
        return _source_id(self.source_kind, self.robot_run_id, self.content_id)

    @model_validator(mode="after")
    def _check_revision(self) -> SceneRevisionRecord:
        if self.window_end_timestamp_ns <= self.window_start_timestamp_ns:
            raise ValueError("window must be non-empty")
        _check_unit_identity(
            self.scene_id, self.source_kind, self.source_id, self.unit_key
        )
        check_revision_pin(
            unit_id=self.scene_id,
            manifest_artifact_id=self.manifest_artifact_id,
            manifest_checksum=self.manifest_checksum,
        )
        return self

    def unit(self) -> SceneRecord:
        return SceneRecord(
            scene_id=self.scene_id,
            source_kind=self.source_kind,
            robot_run_id=self.robot_run_id,
            content_id=self.content_id,
            unit_key=self.unit_key,
        )

    def projection(self) -> dict[str, Any]:
        """Everything the manifest determines: two records of one revision
        must agree on all of it."""
        return self.model_dump(exclude={"registered_at"})

    def pins(self, *, manifest_artifact_id: str, manifest_checksum: str) -> bool:
        """True if this record is exactly the given revision."""
        return (
            self.manifest_artifact_id == manifest_artifact_id
            and self.manifest_checksum == manifest_checksum
        )

    def pin(self) -> RevisionPin:
        return RevisionPin(
            domain="scene",
            unit_id=self.scene_id,
            manifest_artifact_id=self.manifest_artifact_id,
            manifest_checksum=self.manifest_checksum,
        )


def project_scene_revision(
    *,
    manifest: SceneManifest,
    manifest_artifact_id: str,
    manifest_checksum: str,
) -> SceneRevisionRecord:
    source = manifest.lineage.source
    window = manifest.declared_window()
    return SceneRevisionRecord(
        scene_id=scene_id_for(source),
        source_kind=source.source_kind,
        robot_run_id=source.source_id if source.source_kind == "recording" else None,
        content_id=source.source_id if source.source_kind == "content" else None,
        unit_key=source.unit_key,
        manifest_artifact_id=manifest_artifact_id,
        manifest_checksum=manifest_checksum,
        producer_fingerprint=manifest.lineage.producer.producer_fingerprint,
        window_clock=window.source_clock,
        window_start_timestamp_ns=window.start_timestamp_ns,
        window_end_timestamp_ns=window.end_timestamp_ns,
        observed_channels=manifest.observed_channel_names(),
        observation_count=len(manifest.observations),
        keyframe_count=len(manifest.keyframes()),
    )


__all__ = [
    "SceneRecord",
    "SceneRevisionRecord",
    "project_scene_revision",
    "scene_id_for",
]
