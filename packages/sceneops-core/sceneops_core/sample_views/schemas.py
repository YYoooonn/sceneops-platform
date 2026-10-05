"""SceneSampleView v1: a derived, policy-driven sample view of one Scene
(ADR-007 §33.3).

A canonical Scene keeps every observation asynchronous, on its own channel
and clock. Perception workflows need *samples*: a chosen instant with the
observations, pose and labels that belong to it. That choice is a workflow
decision, so it is made here, in a derived artifact, never in the Scene:

    Scene revision + SampleViewPolicy + label set revisions
        -> SceneSampleViewManifest

The policy is explicit and is part of the artifact, so the same inputs
always rebuild the same bytes:

    anchor    which channel defines the sample instants, and the stride
    members   per channel: nearest / previous association inside a tolerance,
              and whether the sample needs it
    pose      one named frame-pair, associated the same way
    labels    pinned label set revisions; labels attach through the
              observations a sample holds (their ObservationAnchor)

Association never crosses clocks: every associated channel and pose must be
on the anchor channel's clock, and a configuration that asks otherwise
fails instead of converting. Nothing is interpolated or resampled.

The view stores only references and time deltas (observation ids, pose ids,
label ids). Payloads, calibrations and pose values stay in the pinned Scene
revision, which is read through its checksum, so the view never becomes a
second copy of canonical data.
"""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Final, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    ValidationError,
    field_validator,
    model_validator,
)

from sceneops_core.common.canonical_json import canonical_json_bytes
from sceneops_core.common.checksums import SHA256_CHECKSUM_PATTERN, sha256_checksum
from sceneops_core.labels.schemas import LabelSetRef
from sceneops_core.provenance import SourceTimestampNs
from sceneops_core.scenes.schemas.manifests import LocalId, SourceClock, VerbatimKey

SAMPLE_VIEW_SCHEMA_V1: Final = "sceneops.scene_sample_view/v1"

# Identifies the behaviour of the view builder (association tie-breaks, drop
# rules, label attachment). Bump when a change would alter the output for an
# existing input.
SAMPLE_VIEW_SEMANTICS_VERSION: Final = "1"


class SampleViewError(ValueError):
    """Bytes are not a valid canonical SceneSampleView."""


class UnsupportedSampleViewVersionError(SampleViewError):
    pass


class NonCanonicalSampleViewError(SampleViewError):
    pass


class AssociationMode(StrEnum):
    """How a channel or pose is associated with the anchor instant.

    ``nearest``   the closest timestamp, before or after; ties go to the
                  earlier timestamp.
    ``previous``  the latest timestamp at or before the anchor.
    """

    NEAREST = "nearest"
    PREVIOUS = "previous"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --- policy -------------------------------------------------------------------


class SampleAnchorPolicy(_Model):
    """Every ``stride``-th observation of ``channel``, in canonical order,
    is a sample instant."""

    channel: VerbatimKey
    stride: StrictInt = Field(default=1, ge=1)


class MemberPolicy(_Model):
    channel: VerbatimKey
    association: AssociationMode = AssociationMode.NEAREST
    tolerance_ns: StrictInt = Field(ge=0)
    # A required member that cannot be associated drops the whole sample;
    # an optional one is simply absent from it.
    required: bool = True


class PosePolicy(_Model):
    """The pose of one named frame pair, associated at the anchor instant.
    The transform maps ``child_frame_id`` into ``parent_frame_id``."""

    parent_frame_id: VerbatimKey
    child_frame_id: VerbatimKey
    association: AssociationMode = AssociationMode.NEAREST
    tolerance_ns: StrictInt = Field(ge=0)
    required: bool = True


class SampleViewPolicy(_Model):
    anchor: SampleAnchorPolicy
    members: list[MemberPolicy] = Field(default_factory=list)
    pose: PosePolicy | None = None

    @field_validator("members")
    @classmethod
    def _canonical_members(cls, value: list[MemberPolicy]) -> list[MemberPolicy]:
        channels = [m.channel for m in value]
        if len(channels) != len(set(channels)):
            raise ValueError("members must name each channel at most once")
        return sorted(value, key=lambda m: m.channel)

    @model_validator(mode="after")
    def _anchor_is_not_a_member(self) -> SampleViewPolicy:
        if any(m.channel == self.anchor.channel for m in self.members):
            raise ValueError("the anchor channel is not a member")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json"))

    def checksum(self) -> str:
        return sha256_checksum(self.canonical_bytes())


# --- manifest -----------------------------------------------------------------


class SceneRevisionRef(_Model):
    """Pins exactly one canonical Scene manifest revision."""

    scene_id: LocalId
    manifest_artifact_id: LocalId
    manifest_checksum: StrictStr = Field(pattern=SHA256_CHECKSUM_PATTERN)


class SampleViewRef(_Model):
    """Pins exactly one SceneSampleView revision."""

    scene_id: LocalId
    manifest_artifact_id: LocalId
    manifest_checksum: StrictStr = Field(pattern=SHA256_CHECKSUM_PATTERN)


class SampleMember(_Model):
    channel: VerbatimKey
    observation_id: LocalId
    timestamp_ns: SourceTimestampNs
    # member timestamp minus the sample's anchor timestamp (signed).
    time_delta_ns: StrictInt


class SamplePose(_Model):
    pose_id: LocalId
    time_delta_ns: StrictInt


class SampleLabels(_Model):
    """What one pinned label set says about one sample.

    ``covered``: the set annotated at least one observation the sample
    holds, so an empty ``label_ids`` is an annotated negative. Not covered
    means the set says nothing about the sample.
    """

    label_set_id: LocalId
    covered: bool
    label_ids: list[LocalId] = Field(default_factory=list)

    @model_validator(mode="after")
    def _uncovered_has_no_labels(self) -> SampleLabels:
        if self.label_ids and not self.covered:
            raise ValueError("an uncovered sample cannot carry labels")
        if self.label_ids != sorted(set(self.label_ids)):
            raise ValueError("label_ids must be sorted and unique")
        return self


class SceneSample(_Model):
    sample_id: LocalId
    anchor: SampleMember
    members: list[SampleMember] = Field(default_factory=list)
    pose: SamplePose | None = None
    labels: list[SampleLabels] = Field(default_factory=list)

    def member_observation_ids(self) -> list[str]:
        return [self.anchor.observation_id, *(m.observation_id for m in self.members)]

    def member(self, channel: str) -> SampleMember | None:
        if channel == self.anchor.channel:
            return self.anchor
        for member in self.members:
            if member.channel == channel:
                return member
        return None


class DroppedAnchor(_Model):
    """An anchor instant that produced no sample, with the reasons."""

    observation_id: LocalId
    reasons: list[StrictStr] = Field(min_length=1)


class LabelAttachmentStats(_Model):
    """Accounting of one label set against this Scene, so no label is
    silently lost.

    ``covered_observation_count``  observations of this Scene the set annotated
    ``attached_label_count``       labels attached to some sample
    ``unattached_label_count``     labels anchored on a Scene observation that
                                   no sample holds
    """

    label_set_id: LocalId
    covered_observation_count: StrictInt = Field(ge=0)
    attached_label_count: StrictInt = Field(ge=0)
    unattached_label_count: StrictInt = Field(ge=0)


class SceneSampleViewManifest(_Model):
    schema_version: Literal["sceneops.scene_sample_view/v1"] = SAMPLE_VIEW_SCHEMA_V1
    builder_semantics_version: StrictStr = SAMPLE_VIEW_SEMANTICS_VERSION
    scene: SceneRevisionRef
    policy: SampleViewPolicy
    anchor_clock: SourceClock
    label_sets: list[LabelSetRef] = Field(default_factory=list)
    label_stats: list[LabelAttachmentStats] = Field(default_factory=list)
    samples: list[SceneSample] = Field(default_factory=list)
    dropped: list[DroppedAnchor] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_invariants(self) -> SceneSampleViewManifest:
        set_ids = [ref.label_set_id for ref in self.label_sets]
        if set_ids != sorted(set(set_ids)):
            raise ValueError("label_sets must be sorted by label_set_id and unique")
        if [s.label_set_id for s in self.label_stats] != set_ids:
            raise ValueError("label_stats must cover exactly the pinned label sets")
        sample_ids = [s.sample_id for s in self.samples]
        if len(sample_ids) != len(set(sample_ids)):
            raise ValueError("sample ids must be unique")
        order = [(s.anchor.timestamp_ns, s.sample_id) for s in self.samples]
        if order != sorted(order):
            raise ValueError("samples must be ordered by anchor timestamp")
        declared = set(set_ids)
        for sample in self.samples:
            if sample.anchor.channel != self.policy.anchor.channel:
                raise ValueError(f"sample {sample.sample_id} anchor channel mismatch")
            labelled = [entry.label_set_id for entry in sample.labels]
            if labelled != set_ids or set(labelled) - declared:
                raise ValueError(
                    f"sample {sample.sample_id} must report every pinned label set"
                )
        return self

    def to_canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json"))

    def checksum(self) -> str:
        return sha256_checksum(self.to_canonical_bytes())

    def sample(self, sample_id: str) -> SceneSample | None:
        for sample in self.samples:
            if sample.sample_id == sample_id:
                return sample
        return None


def load_canonical_sample_view(data: bytes) -> SceneSampleViewManifest:
    """Strictly parse sample view bytes and require canonical form."""
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SampleViewError(f"sample view is not UTF-8 JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise SampleViewError("sample view must be a JSON object")
    version = payload.get("schema_version")
    if version != SAMPLE_VIEW_SCHEMA_V1:
        raise UnsupportedSampleViewVersionError(
            f"unsupported sample view schema_version: {version!r}"
        )
    try:
        manifest = SceneSampleViewManifest.model_validate(payload)
    except ValidationError as exc:
        raise SampleViewError(f"invalid sample view: {exc}") from exc
    if manifest.to_canonical_bytes() != data:
        raise NonCanonicalSampleViewError("sample view bytes are not in canonical form")
    return manifest


__all__ = [
    "SAMPLE_VIEW_SCHEMA_V1",
    "SAMPLE_VIEW_SEMANTICS_VERSION",
    "AssociationMode",
    "DroppedAnchor",
    "LabelAttachmentStats",
    "MemberPolicy",
    "NonCanonicalSampleViewError",
    "PosePolicy",
    "SampleAnchorPolicy",
    "SampleLabels",
    "SampleMember",
    "SamplePose",
    "SampleViewError",
    "SampleViewPolicy",
    "SampleViewRef",
    "SceneRevisionRef",
    "SceneSample",
    "SceneSampleViewManifest",
    "UnsupportedSampleViewVersionError",
    "load_canonical_sample_view",
]
