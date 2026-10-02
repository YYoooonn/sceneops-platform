"""Source provenance blocks for canonical units (ADR-007 §14, §27.2).

A canonical Scene or Episode came from exactly one of two source kinds:

    ExternalUnitSource       one unit of an already-structured external dataset
    RecordingSegmentSource   one window of a registered RobotRun recording

``UnitSource`` is the discriminated union of the two. It is provenance only:
it does not make Scene and Episode one domain type, and it carries no
Scene-, Episode- or source-format-specific fields. Each domain composes it
into its own lineage.

Each block also projects the *source revision* it pins
(``source_revision()``): the build-level identity of the exact source bytes,
without the per-unit key or window. The producer fingerprint is computed
over that revision (``sceneops_core.provenance.producer``), so a manifest's
fingerprint can be re-derived from the manifest itself.
"""

from __future__ import annotations

from typing import Annotated, Final, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    field_validator,
    model_validator,
)

from sceneops_core.common.identifiers import (
    validate_external_format,
    validate_source_clock,
)
from sceneops_core.common.ids import robot_run_recording_artifact_id
from sceneops_core.datasets.schemas.external import ExternalDatasetRef
from sceneops_core.robots.manifest import RUN_ID_MAX_LENGTH, validate_identifier

from .source_time import SourceTimestampNs

SHA256_CHECKSUM_PATTERN: Final = r"^sha256:[0-9a-f]{64}$"
UNIT_KEY_MAX_LENGTH: Final = 256


class _ProvenanceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _validate_unit_key(value: str, *, field: str) -> str:
    """Unit keys are verbatim source / builder keys, so their alphabet is
    open, but they must be unambiguous: non-empty, bounded, no surrounding
    whitespace and no control characters."""
    if not value or len(value) > UNIT_KEY_MAX_LENGTH:
        raise ValueError(f"{field} must be 1-{UNIT_KEY_MAX_LENGTH} characters")
    if value != value.strip():
        raise ValueError(f"{field} must not have surrounding whitespace")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise ValueError(f"{field} must not contain control characters")
    return value


def _validate_robot_run_id(value: str) -> str:
    return validate_identifier(
        value, field="robot_run_id", max_length=RUN_ID_MAX_LENGTH
    )


# --- source revisions (fingerprint inputs) -----------------------------------


class ExternalSourceRevision(_ProvenanceModel):
    """Which revision of an external dataset was read. ``uri`` and
    ``external_name`` are deliberately absent: location and display names
    are not source identity."""

    source_kind: Literal["external"] = "external"
    format: StrictStr
    format_version: StrictStr = Field(min_length=1)
    external_revision: StrictStr | None
    checksum: StrictStr | None

    @field_validator("format")
    @classmethod
    def _check_format(cls, value: str) -> str:
        return validate_external_format(value)

    @classmethod
    def of(cls, ref: ExternalDatasetRef) -> ExternalSourceRevision:
        return cls(
            format=ref.format,
            format_version=ref.format_version,
            external_revision=ref.external_revision,
            checksum=ref.checksum,
        )


class RecordingSourceRevision(_ProvenanceModel):
    """Which registered recording bytes were read. The checksum pins the
    exact bytes; the recording artifact id is derivable from the run id."""

    source_kind: Literal["recording"] = "recording"
    robot_run_id: StrictStr
    recording_checksum: StrictStr = Field(pattern=SHA256_CHECKSUM_PATTERN)

    @field_validator("robot_run_id")
    @classmethod
    def _check_robot_run_id(cls, value: str) -> str:
        return _validate_robot_run_id(value)


SourceRevision = Annotated[
    ExternalSourceRevision | RecordingSourceRevision,
    Field(discriminator="source_kind"),
]


# --- per-unit source provenance ----------------------------------------------


class ExternalUnitSource(_ProvenanceModel):
    """The canonical unit is one source-domain unit of an external dataset.

    ``source_unit_key`` is that unit's stable identity inside the external
    dataset as the source defines it (e.g. a nuScenes scene token, a LeRobot
    episode index), kept verbatim. Together with ``external_ref.format`` it
    is the unit's external identity (§18.1); ``external_ref.uri`` is
    provenance only.
    """

    source_kind: Literal["external"] = "external"
    external_ref: ExternalDatasetRef
    source_unit_key: StrictStr

    @field_validator("source_unit_key")
    @classmethod
    def _check_source_unit_key(cls, value: str) -> str:
        return _validate_unit_key(value, field="source_unit_key")

    def source_revision(self) -> ExternalSourceRevision:
        return ExternalSourceRevision.of(self.external_ref)


class RecordingSegmentSource(_ProvenanceModel):
    """The canonical unit is one window of a registered RobotRun recording.

    The RobotRun is the source identity authority; the recording
    ArtifactRecord identifies the immutable bytes and ``recording_checksum``
    pins their exact revision. The window is the half-open interval
    ``[start_timestamp_ns, end_timestamp_ns)`` in ``source_clock``, which is
    copied from the RobotRun (never a default). ``unit_key`` is the
    producer-defined stable key of this unit within the recording (e.g. a
    segment index or mission boundary key); it must be derived from the
    source and the build configuration, never from execution state.

    There is no recording URI here: consumers reach the bytes only through
    the verified recording resolver, keyed by ``robot_run_id``.
    """

    source_kind: Literal["recording"] = "recording"
    robot_run_id: StrictStr
    recording_artifact_id: StrictStr
    recording_checksum: StrictStr = Field(pattern=SHA256_CHECKSUM_PATTERN)
    source_clock: StrictStr
    start_timestamp_ns: SourceTimestampNs
    end_timestamp_ns: SourceTimestampNs
    unit_key: StrictStr

    @field_validator("robot_run_id")
    @classmethod
    def _check_robot_run_id(cls, value: str) -> str:
        return _validate_robot_run_id(value)

    @field_validator("source_clock")
    @classmethod
    def _check_source_clock(cls, value: str) -> str:
        return validate_source_clock(value)

    @field_validator("unit_key")
    @classmethod
    def _check_unit_key(cls, value: str) -> str:
        return _validate_unit_key(value, field="unit_key")

    @model_validator(mode="after")
    def _check_invariants(self) -> RecordingSegmentSource:
        expected_artifact_id = robot_run_recording_artifact_id(self.robot_run_id)
        if self.recording_artifact_id != expected_artifact_id:
            raise ValueError(
                f"recording_artifact_id must be {expected_artifact_id!r} for "
                f"robot_run_id {self.robot_run_id!r}, got "
                f"{self.recording_artifact_id!r}"
            )
        if self.end_timestamp_ns <= self.start_timestamp_ns:
            raise ValueError(
                "segment window [start_timestamp_ns, end_timestamp_ns) must be "
                f"non-empty, got [{self.start_timestamp_ns}, "
                f"{self.end_timestamp_ns})"
            )
        return self

    def contains(self, timestamp_ns: int) -> bool:
        """Half-open window membership, in this segment's ``source_clock``."""
        return self.start_timestamp_ns <= timestamp_ns < self.end_timestamp_ns

    def source_revision(self) -> RecordingSourceRevision:
        return RecordingSourceRevision(
            robot_run_id=self.robot_run_id,
            recording_checksum=self.recording_checksum,
        )


UnitSource = Annotated[
    ExternalUnitSource | RecordingSegmentSource,
    Field(discriminator="source_kind"),
]


__all__ = [
    "SHA256_CHECKSUM_PATTERN",
    "UNIT_KEY_MAX_LENGTH",
    "ExternalSourceRevision",
    "ExternalUnitSource",
    "RecordingSegmentSource",
    "RecordingSourceRevision",
    "SourceRevision",
    "UnitSource",
]
