"""Canonical builder inputs: the registered, pinned source one build reads
(ADR-015 §6, §6.7).

A builder is a pure function of a pinned source, its producer semantics and
a normalized build configuration (MS-4). Each ingress family hands builders
its own pinned source, and each one issues the source revision and source
bindings of what is built from it, so a builder never assembles source
provenance from loose fields:

    PinnedRecording   a registered RobotRun recording   (Acquisition)
                        -> RecordingSourceRevision, RecordingSegmentSource
    PinnedContent     a registered SourceContent        (Dataset Integration)
                        -> ContentSourceRevision, ContentUnitSource

The local bytes are not part of the contract: they come from the verified
resolver of each source (``resolve_recording`` for recordings), which checks
them against the pinned checksum. Neither input names a DatasetVersion, and
``PinnedContent`` names no SourceSnapshot: a snapshot pipeline resolves its
``snapshot_id`` to the content once and records both on its Job.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator

from sceneops_core.common.checksums import SHA256_CHECKSUM_PATTERN
from sceneops_core.common.identifiers import validate_source_clock
from sceneops_core.common.ids import robot_run_recording_artifact_id
from sceneops_core.robots.manifest import RUN_ID_MAX_LENGTH, validate_identifier
from sceneops_core.sources.content import SourceContentInventory

from .sources import (
    ContentSourceRevision,
    ContentUnitSource,
    RecordingSegmentSource,
    RecordingSourceRevision,
)


class _PinnedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PinnedRecording(_PinnedModel):
    """A registered RobotRun recording, from its RobotRunRecord and
    recording ArtifactRecord. ``recording_clock`` is the clock the
    recording's own message times are in (``RobotRunRecord.source_clock``)."""

    robot_run_id: StrictStr
    recording_checksum: StrictStr = Field(pattern=SHA256_CHECKSUM_PATTERN)
    recording_clock: StrictStr

    @field_validator("robot_run_id")
    @classmethod
    def _check_robot_run_id(cls, value: str) -> str:
        return validate_identifier(
            value, field="robot_run_id", max_length=RUN_ID_MAX_LENGTH
        )

    @field_validator("recording_clock")
    @classmethod
    def _check_recording_clock(cls, value: str) -> str:
        return validate_source_clock(value)

    @property
    def source_revision(self) -> RecordingSourceRevision:
        return RecordingSourceRevision(
            robot_run_id=self.robot_run_id,
            recording_checksum=self.recording_checksum,
        )

    def segment_source(
        self,
        *,
        source_clock: str,
        start_timestamp_ns: int,
        end_timestamp_ns: int,
        unit_key: str,
    ) -> RecordingSegmentSource:
        return RecordingSegmentSource(
            robot_run_id=self.robot_run_id,
            recording_artifact_id=robot_run_recording_artifact_id(self.robot_run_id),
            recording_checksum=self.recording_checksum,
            source_clock=source_clock,
            start_timestamp_ns=start_timestamp_ns,
            end_timestamp_ns=end_timestamp_ns,
            unit_key=unit_key,
        )


class PinnedContent(_PinnedModel):
    """A registered SourceContent, by its inventory: the file list every
    preserved-file reference a builder emits must match."""

    inventory: SourceContentInventory

    @property
    def content_id(self) -> str:
        return self.inventory.content_id

    @property
    def content_checksum(self) -> str:
        return self.inventory.content_checksum

    @property
    def source_revision(self) -> ContentSourceRevision:
        return ContentSourceRevision(
            content_id=self.content_id,
            content_checksum=self.content_checksum,
        )

    def unit_source(
        self,
        *,
        source_clock: str,
        start_timestamp_ns: int,
        end_timestamp_ns: int,
        unit_key: str,
    ) -> ContentUnitSource:
        return ContentUnitSource(
            content_id=self.content_id,
            content_checksum=self.content_checksum,
            source_clock=source_clock,
            start_timestamp_ns=start_timestamp_ns,
            end_timestamp_ns=end_timestamp_ns,
            unit_key=unit_key,
        )


__all__ = ["PinnedContent", "PinnedRecording"]
