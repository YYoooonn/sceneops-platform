"""Source provenance for canonical units (ADR-007 §14, §27.2, §29.9).

Every canonical Scene and Episode comes from a registered RobotRun
recording (I-31): its source block is a ``RecordingSegmentSource``, one
window of that recording. The block is provenance only: it does not make
Scene and Episode one domain type, and it carries no Scene-, Episode- or
source-format-specific fields. Each domain composes it into its own
lineage.

The block also projects the *source revision* it pins
(``source_revision()``): the build-level identity of the exact recording
bytes, without the per-unit key or window. The producer fingerprint is
computed over that revision (``sceneops_core.provenance.producer``), so a
manifest's fingerprint can be re-derived from the manifest itself.

The serialized ``source_kind = "recording"`` discriminator is kept, so the
bytes of recording-derived manifests and fingerprints stay as they were
when a second, external source kind existed.
"""

from __future__ import annotations

from typing import Final, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    field_validator,
    model_validator,
)

from sceneops_core.common.checksums import SHA256_CHECKSUM_PATTERN
from sceneops_core.common.identifiers import (
    VERBATIM_KEY_MAX_LENGTH,
    validate_source_clock,
    validate_verbatim_key,
)
from sceneops_core.common.ids import robot_run_recording_artifact_id
from sceneops_core.robots.manifest import RUN_ID_MAX_LENGTH, validate_identifier

from .source_time import SourceTimestampNs

UNIT_KEY_MAX_LENGTH: Final = VERBATIM_KEY_MAX_LENGTH


class _ProvenanceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _validate_unit_key(value: str, *, field: str) -> str:
    """Unit keys are verbatim source / builder keys."""
    return validate_verbatim_key(value, field=field, max_length=UNIT_KEY_MAX_LENGTH)


def _validate_robot_run_id(value: str) -> str:
    return validate_identifier(
        value, field="robot_run_id", max_length=RUN_ID_MAX_LENGTH
    )


# --- source revisions (fingerprint inputs) -----------------------------------


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


# --- per-unit source provenance ----------------------------------------------


class RecordingSegmentSource(_ProvenanceModel):
    """The canonical unit is one window of a registered RobotRun recording.

    The RobotRun is the source identity authority; the recording
    ArtifactRecord identifies the immutable bytes and ``recording_checksum``
    pins their exact revision. The window is the half-open interval
    ``[start_timestamp_ns, end_timestamp_ns)`` in ``source_clock``: the one
    segmentation clock the producer's build configuration declares (ADR-007
    §30.2, Q4). It may be the recording clock (``mcap_log_time``) or a
    source-semantic clock the configuration reads from message timestamps;
    it is never a default and never derived from the RobotRun's
    ``started_at`` / ``ended_at``. Timestamps in any other clock are not
    comparable with the window. ``unit_key`` is the producer-defined stable
    key of this unit within the recording (e.g. a segment index or mission
    boundary key); it must be derived from the source and the build
    configuration, never from execution state.

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


__all__ = [
    "SHA256_CHECKSUM_PATTERN",
    "UNIT_KEY_MAX_LENGTH",
    "RecordingSegmentSource",
    "RecordingSourceRevision",
]
