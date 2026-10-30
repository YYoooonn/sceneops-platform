"""Source bindings for canonical units (ADR-015 §4).

A canonical unit has exactly one source binding, a closed union
discriminated by ``source_kind``:

    RecordingSegmentSource  "recording"  one window of a registered RobotRun
                                         recording (Acquisition)
    ContentUnitSource       "content"    one native unit of a registered
                                         SourceContent (Dataset Integration)

A binding is provenance only: it does not make Scene and Episode one domain
type, and it carries no Scene-, Episode- or source-format-specific field, no
location, no SourceSnapshot and no execution context. Each domain composes a
binding into its own lineage.

Each binding names its *source identity* (``source_id``): the RobotRun id
or the content id, the stable identity a canonical unit is keyed under
(``sceneops_core.provenance.identity``).

Each binding projects the *source revision* it pins (``source_revision()``):
the build-level identity of the exact source bytes, without the per-unit key
or window. The producer fingerprint is computed over that revision
(``sceneops_core.provenance.producer``), so a manifest's fingerprint can be
re-derived from the manifest itself.

Scene and Episode manifests admit either binding: the recording builders
produce ``RecordingSegmentSource``, the format builders of Dataset
Integration (nuScenes Scenes, LeRobot Episodes) ``ContentUnitSource``.
"""

from __future__ import annotations

from typing import Annotated, Final, Literal, TypeAlias

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
from sceneops_core.sources.content import content_id_for, validate_content_id

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


class ContentSourceRevision(_ProvenanceModel):
    """Which registered SourceContent bytes were read. ``content_checksum``
    pins the exact file list; ``content_id`` is derived from it. No
    SourceSnapshot is named: which declaration a build used is execution
    provenance, recorded on the build's Job (ADR-015 §4, MS-14)."""

    source_kind: Literal["content"] = "content"
    content_id: StrictStr
    content_checksum: StrictStr = Field(pattern=SHA256_CHECKSUM_PATTERN)

    @field_validator("content_id")
    @classmethod
    def _check_content_id(cls, value: str) -> str:
        return validate_content_id(value)

    @model_validator(mode="after")
    def _check_invariants(self) -> ContentSourceRevision:
        _check_content_identity(self.content_id, self.content_checksum)
        return self


SourceRevision: TypeAlias = Annotated[
    RecordingSourceRevision | ContentSourceRevision,
    Field(discriminator="source_kind"),
]


def _check_content_identity(content_id: str, content_checksum: str) -> None:
    expected = content_id_for(content_checksum)
    if content_id != expected:
        raise ValueError(
            f"content_id must be {expected!r} for content_checksum "
            f"{content_checksum}, got {content_id!r}"
        )


def _check_window(start_timestamp_ns: int, end_timestamp_ns: int) -> None:
    if end_timestamp_ns <= start_timestamp_ns:
        raise ValueError(
            "source window [start_timestamp_ns, end_timestamp_ns) must be "
            f"non-empty, got [{start_timestamp_ns}, {end_timestamp_ns})"
        )


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
        _check_window(self.start_timestamp_ns, self.end_timestamp_ns)
        return self

    @property
    def source_id(self) -> str:
        """The RobotRun is the source identity: one run has exactly one
        immutable recording, so the checksum is not needed to name it."""
        return self.robot_run_id

    def contains(self, timestamp_ns: int) -> bool:
        """Half-open window membership, in this segment's ``source_clock``."""
        return self.start_timestamp_ns <= timestamp_ns < self.end_timestamp_ns

    def source_revision(self) -> RecordingSourceRevision:
        return RecordingSourceRevision(
            robot_run_id=self.robot_run_id,
            recording_checksum=self.recording_checksum,
        )


class ContentUnitSource(_ProvenanceModel):
    """The canonical unit is one native unit of a registered SourceContent.

    ``content_id`` names the content and ``content_checksum`` pins its exact
    bytes. ``unit_key`` is the source's own unit key, verbatim (a nuScenes
    ``scene.token``, a LeRobot ``episode_index``), read from the bytes, so
    every snapshot of the same content yields the same keys. The window is
    the half-open interval ``[start_timestamp_ns, end_timestamp_ns)`` in
    ``source_clock``.

    The binding names no SourceSnapshot and copies nothing of a descriptor
    (format, upstream, revision, licence), so registering another
    declaration of the same bytes changes no binding. There is no location:
    consumers reach the bytes only through the verified content reader,
    keyed by ``content_id``. No RobotRun is involved.
    """

    source_kind: Literal["content"] = "content"
    content_id: StrictStr
    content_checksum: StrictStr = Field(pattern=SHA256_CHECKSUM_PATTERN)
    source_clock: StrictStr
    start_timestamp_ns: SourceTimestampNs
    end_timestamp_ns: SourceTimestampNs
    unit_key: StrictStr

    @field_validator("content_id")
    @classmethod
    def _check_content_id(cls, value: str) -> str:
        return validate_content_id(value)

    @field_validator("source_clock")
    @classmethod
    def _check_source_clock(cls, value: str) -> str:
        return validate_source_clock(value)

    @field_validator("unit_key")
    @classmethod
    def _check_unit_key(cls, value: str) -> str:
        return _validate_unit_key(value, field="unit_key")

    @model_validator(mode="after")
    def _check_invariants(self) -> ContentUnitSource:
        _check_content_identity(self.content_id, self.content_checksum)
        _check_window(self.start_timestamp_ns, self.end_timestamp_ns)
        return self

    @property
    def source_id(self) -> str:
        """The content is the source identity. It is a function of the
        bytes, so every SourceSnapshot that declares them names the same
        source."""
        return self.content_id

    def contains(self, timestamp_ns: int) -> bool:
        """Half-open window membership, in this unit's ``source_clock``."""
        return self.start_timestamp_ns <= timestamp_ns < self.end_timestamp_ns

    def source_revision(self) -> ContentSourceRevision:
        return ContentSourceRevision(
            content_id=self.content_id,
            content_checksum=self.content_checksum,
        )


SourceBinding: TypeAlias = Annotated[
    RecordingSegmentSource | ContentUnitSource,
    Field(discriminator="source_kind"),
]


__all__ = [
    "SHA256_CHECKSUM_PATTERN",
    "UNIT_KEY_MAX_LENGTH",
    "ContentSourceRevision",
    "ContentUnitSource",
    "RecordingSegmentSource",
    "RecordingSourceRevision",
    "SourceBinding",
    "SourceRevision",
]
