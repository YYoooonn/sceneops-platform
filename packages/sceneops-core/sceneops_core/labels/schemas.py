"""LabelSetManifest v1: post-acquisition labels as separate, lineage-bearing
data (ADR-007 §33.2).

A label is a statement *about* acquired data made after acquisition, by a
human, an external dataset or a model. It is not part of the RobotRun's
acquisition provenance, and it is never written into a canonical Scene or
Episode manifest. A label set references what it labels through a stable
canonical reference, an :class:`ObservationAnchor`, so it survives
re-canonicalization (another build configuration, another DatasetVersion).

    anchor  = (robot_run_id, channel, source_clock, timestamp_ns)

``channel`` is the canonical source channel (the recorded topic) and
``timestamp_ns`` the observation's canonical source timestamp in
``source_clock``, so an anchor names an observation, not a Scene. Whether a
given Scene holds the observation is decided when a derived view resolves
the anchor (``SceneSampleView``), never here.

A label set also declares its *coverage*: every anchor it has annotated,
including anchors annotated with no object. Without coverage, "no label" is
ambiguous between "annotated as empty" and "never annotated", and an
evaluation could not tell a true negative from a missing label.

One serialized label set is one immutable revision. A revision is identified
by the checksum of its canonical bytes; consumers pin that checksum, so a
label set can gain revisions without changing what an earlier evaluation
read. The manifest holds no storage location, DatasetVersion, execution
context or import timestamp.

Format knowledge (nuScenes annotation tables, CVAT exports, ...) lives in
adapters outside core. They emit this document; they are not domain types.
"""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Annotated, Final, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    ValidationError,
    field_validator,
    model_validator,
)

from sceneops_core.common.canonical_json import canonical_json_bytes
from sceneops_core.common.checksums import SHA256_CHECKSUM_PATTERN, sha256_checksum
from sceneops_core.common.identifiers import (
    validate_local_id,
    validate_open_identifier,
    validate_verbatim_key,
)
from sceneops_core.provenance import SourceTimestampNs
from sceneops_core.scenes.schemas.manifests import (
    LocalId,
    QuaternionWXYZ,
    SourceClock,
    VerbatimKey,
    Vector3,
)

LABEL_SET_SCHEMA_V1: Final = "sceneops.label_set/v1"


class LabelSetError(ValueError):
    """Bytes are not a valid canonical label set."""


class UnsupportedLabelSetVersionError(LabelSetError):
    pass


class NonCanonicalLabelSetError(LabelSetError):
    pass


class LabelSourceKind(StrEnum):
    """Who made the labels. The kind is provenance, not a trust level:
    whether labels are good enough to evaluate against is an evaluation
    decision."""

    HUMAN = "human"
    EXTERNAL = "external"
    MODEL = "model"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _validate_open_producer(value: str) -> str:
    return validate_open_identifier(value, field="label producer")


class LabelProvenance(_Model):
    """Where a label set came from.

    ``producer`` is the open identifier of the annotating party or adapter
    source (``nuscenes``, ``team-annotation``, ``grounding-dino``), and
    ``producer_version`` its own version string, kept verbatim. Model
    labels must name the model revision that produced them.
    """

    kind: LabelSourceKind
    producer: Annotated[StrictStr, AfterValidator(_validate_open_producer)]
    producer_version: StrictStr | None = None
    model_id: StrictStr | None = None
    model_version: StrictStr | None = None

    @field_validator("producer_version", "model_id", "model_version")
    @classmethod
    def _check_verbatim(cls, value: str | None) -> str | None:
        return (
            None
            if value is None
            else validate_verbatim_key(value, field="label provenance")
        )

    @model_validator(mode="after")
    def _check_model_revision(self) -> LabelProvenance:
        has_model = self.model_id is not None or self.model_version is not None
        if self.kind == LabelSourceKind.MODEL:
            if self.model_id is None or self.model_version is None:
                raise ValueError(
                    "model-generated labels must name model_id and model_version"
                )
        elif has_model:
            raise ValueError("only model-generated labels may name a model")
        return self


class ObservationAnchor(_Model):
    """The canonical reference a label attaches to: one observation of one
    RobotRun, by channel and exact source timestamp."""

    robot_run_id: StrictStr
    channel: VerbatimKey
    source_clock: SourceClock
    timestamp_ns: SourceTimestampNs

    @field_validator("robot_run_id")
    @classmethod
    def _check_robot_run_id(cls, value: str) -> str:
        return validate_verbatim_key(value, field="robot_run_id")

    def sort_key(self) -> tuple[str, str, str, int]:
        return (self.robot_run_id, self.channel, self.source_clock, self.timestamp_ns)


class LabelBox3D(_Model):
    """A 3-D box in the named frame. Same conventions as the Scene
    manifest: metres, [w, l, h] size, [w, x, y, z] unit quaternion."""

    frame_id: VerbatimKey
    center_m: Vector3
    size_wlh_m: Vector3
    rotation_wxyz: QuaternionWXYZ
    velocity_mps: Vector3 | None = None

    @field_validator("size_wlh_m")
    @classmethod
    def _non_negative_size(cls, value: Vector3) -> Vector3:
        if any(component < 0 for component in value):
            raise ValueError("box size components must be >= 0")
        return value


class Box3DLabel(_Model):
    label_id: LocalId
    anchor: ObservationAnchor
    category: VerbatimKey
    instance_id: VerbatimKey | None = None
    box: LabelBox3D
    attributes: list[VerbatimKey] = Field(default_factory=list)

    @field_validator("attributes")
    @classmethod
    def _sorted_unique_attributes(cls, value: list[str]) -> list[str]:
        if value != sorted(set(value)):
            raise ValueError("attributes must be sorted and unique")
        return value


class LabelSetManifest(_Model):
    schema_version: Literal["sceneops.label_set/v1"] = LABEL_SET_SCHEMA_V1
    label_set_id: LocalId
    provenance: LabelProvenance
    # Every anchor this set annotated, with or without objects.
    coverage: list[ObservationAnchor] = Field(min_length=1)
    labels: list[Box3DLabel] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_invariants(self) -> LabelSetManifest:
        keys = [anchor.sort_key() for anchor in self.coverage]
        if keys != sorted(set(keys)):
            raise ValueError("coverage must be sorted by anchor and unique")
        label_order = [
            (label.anchor.sort_key(), label.label_id) for label in self.labels
        ]
        if label_order != sorted(label_order):
            raise ValueError("labels must be sorted by (anchor, label_id)")
        label_ids = [label.label_id for label in self.labels]
        if len(label_ids) != len(set(label_ids)):
            raise ValueError("label_id must be unique within a label set")
        covered = set(keys)
        for label in self.labels:
            if label.anchor.sort_key() not in covered:
                raise ValueError(
                    f"label {label.label_id} is anchored outside the declared coverage"
                )
        return self

    def to_canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json"))

    def checksum(self) -> str:
        return sha256_checksum(self.to_canonical_bytes())

    @classmethod
    def normalized(
        cls,
        *,
        label_set_id: str,
        provenance: LabelProvenance,
        coverage: list[ObservationAnchor],
        labels: list[Box3DLabel],
    ) -> LabelSetManifest:
        """Sort and de-duplicate coverage and order labels canonically, so an
        adapter need not know the canonical order. Duplicate label ids are
        still rejected, never merged."""
        unique = {anchor.sort_key(): anchor for anchor in coverage}
        return cls(
            label_set_id=label_set_id,
            provenance=provenance,
            coverage=[unique[key] for key in sorted(unique)],
            labels=sorted(
                labels, key=lambda item: (item.anchor.sort_key(), item.label_id)
            ),
        )


class LabelSetRef(_Model):
    """Pins exactly one label set revision."""

    label_set_id: LocalId
    manifest_artifact_id: LocalId
    manifest_checksum: StrictStr = Field(pattern=SHA256_CHECKSUM_PATTERN)

    @field_validator("manifest_artifact_id")
    @classmethod
    def _check_artifact_id(cls, value: str) -> str:
        return validate_local_id(value, field="manifest_artifact_id")


def load_canonical_label_set(data: bytes) -> LabelSetManifest:
    """Strictly parse label set bytes and require canonical form."""
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LabelSetError(f"label set is not UTF-8 JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise LabelSetError("label set must be a JSON object")
    version = payload.get("schema_version")
    if version != LABEL_SET_SCHEMA_V1:
        raise UnsupportedLabelSetVersionError(
            f"unsupported label set schema_version: {version!r}"
        )
    try:
        manifest = LabelSetManifest.model_validate(payload)
    except ValidationError as exc:
        raise LabelSetError(f"invalid label set: {exc}") from exc
    if manifest.to_canonical_bytes() != data:
        raise NonCanonicalLabelSetError(
            "label set bytes are not in canonical label set v1 form"
        )
    return manifest


def parse_label_set_document(data: bytes) -> LabelSetManifest:
    """Parse an adapter-produced label set document and normalize its order.

    The document has the manifest's shape but need not be canonically
    ordered or serialized; the result is the one canonical revision.
    """
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LabelSetError(f"label document is not UTF-8 JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise LabelSetError("label document must be a JSON object")
    version = payload.get("schema_version", LABEL_SET_SCHEMA_V1)
    if version != LABEL_SET_SCHEMA_V1:
        raise UnsupportedLabelSetVersionError(
            f"unsupported label set schema_version: {version!r}"
        )
    try:
        provenance = LabelProvenance.model_validate(payload.get("provenance"))
        coverage = [
            ObservationAnchor.model_validate(a) for a in payload.get("coverage", [])
        ]
        labels = [Box3DLabel.model_validate(item) for item in payload.get("labels", [])]
        return LabelSetManifest.normalized(
            label_set_id=payload.get("label_set_id", ""),
            provenance=provenance,
            coverage=coverage,
            labels=labels,
        )
    except ValidationError as exc:
        raise LabelSetError(f"invalid label document: {exc}") from exc


__all__ = [
    "LABEL_SET_SCHEMA_V1",
    "Box3DLabel",
    "LabelBox3D",
    "LabelProvenance",
    "LabelSetError",
    "LabelSetManifest",
    "LabelSetRef",
    "LabelSourceKind",
    "NonCanonicalLabelSetError",
    "ObservationAnchor",
    "UnsupportedLabelSetVersionError",
    "load_canonical_label_set",
    "parse_label_set_document",
]
