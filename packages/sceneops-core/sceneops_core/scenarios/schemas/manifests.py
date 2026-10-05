"""ScenarioSetManifest v1: a curated, revision-pinned selection of samples
(ADR-007 §33.4).

A ScenarioSet is derived (L3). It does not describe a Scene or an Episode, it
selects among *sample views* of Scenes. Every member pins the exact
SceneSampleView revision it selected from, and the curation records the
exact label set revision whose labels it counted, so a ScenarioSet can be
reproduced and later runs (inference, evaluation) can verify what they were
given:

    Scene revision -> SceneSampleView revision -> ScenarioSet member

Membership is explicit data in this manifest, not a mutable query. The
manifest holds no storage location, execution context or timestamp; one
serialized manifest is one immutable revision identified by its checksum.
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
    model_validator,
)

from sceneops_core.common.canonical_json import canonical_json_bytes
from sceneops_core.common.checksums import SHA256_CHECKSUM_PATTERN, sha256_checksum
from sceneops_core.labels.schemas import LabelSetRef
from sceneops_core.sample_views.schemas import SampleViewRef
from sceneops_core.scenes.schemas.manifests import LocalId, VerbatimKey

SCENARIO_SET_SCHEMA_V1: Final = "sceneops.scenario_set/v1"


class ScenarioSetError(ValueError):
    """Bytes are not a valid canonical ScenarioSet."""


class UnsupportedScenarioSetVersionError(ScenarioSetError):
    pass


class NonCanonicalScenarioSetError(ScenarioSetError):
    pass


class ScenarioSortKey(StrEnum):
    LABEL_COUNT = "label_count"
    SAMPLE_COUNT = "sample_count"
    SCENE_ID = "scene_id"


class SortOrder(StrEnum):
    ASC = "asc"
    DESC = "desc"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ScenarioSetRef(_Model):
    """Pins exactly one ScenarioSet revision."""

    scenario_set_id: LocalId
    manifest_artifact_id: LocalId
    manifest_checksum: StrictStr = Field(pattern=SHA256_CHECKSUM_PATTERN)


class ScenarioCuration(_Model):
    """The explicit curation parameters a set was mined with.

    Label criteria count labels of exactly ``label_set`` (a pinned
    revision). They are only meaningful with one, and a set that uses them
    without one is rejected, never silently unfiltered.
    """

    label_set: LabelSetRef | None = None
    require_labels: bool = False
    min_label_count: StrictInt | None = Field(default=None, ge=0)
    max_label_count: StrictInt | None = Field(default=None, ge=0)
    min_sample_count: StrictInt | None = Field(default=None, ge=1)
    required_channels: list[VerbatimKey] = Field(default_factory=list)
    readiness: list[StrictStr] | None = None
    sort_by: ScenarioSortKey = ScenarioSortKey.LABEL_COUNT
    order: SortOrder = SortOrder.DESC
    max_candidates: StrictInt = Field(ge=1)

    @model_validator(mode="after")
    def _label_criteria_need_a_label_set(self) -> ScenarioCuration:
        uses_labels = (
            self.require_labels
            or self.min_label_count is not None
            or self.max_label_count is not None
        )
        if uses_labels and self.label_set is None:
            raise ValueError("label criteria require a label_set")
        if self.required_channels != sorted(set(self.required_channels)):
            raise ValueError("required_channels must be sorted and unique")
        return self


class ScenarioMember(_Model):
    scene_id: LocalId
    sample_view: SampleViewRef
    sample_ids: list[LocalId] = Field(min_length=1)
    sample_count: StrictInt = Field(ge=1)
    label_count: StrictInt = Field(ge=0)
    channels: list[VerbatimKey]
    readiness: StrictStr

    @model_validator(mode="after")
    def _check(self) -> ScenarioMember:
        if self.sample_view.scene_id != self.scene_id:
            raise ValueError("a member's view must belong to its scene")
        if self.sample_ids != sorted(set(self.sample_ids)):
            raise ValueError("sample_ids must be sorted and unique")
        if len(self.sample_ids) > self.sample_count:
            raise ValueError("sample_ids exceed the view's sample count")
        if self.channels != sorted(set(self.channels)):
            raise ValueError("channels must be sorted and unique")
        return self


class ScenarioSetManifest(_Model):
    schema_version: Literal["sceneops.scenario_set/v1"] = SCENARIO_SET_SCHEMA_V1
    scenario_set_id: LocalId
    dataset_id: StrictStr
    dataset_version: StrictStr
    curation: ScenarioCuration
    input_scene_count: StrictInt = Field(ge=0)
    rejected_scene_count: StrictInt = Field(ge=0)
    # In curation rank order.
    members: list[ScenarioMember] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> ScenarioSetManifest:
        scene_ids = [m.scene_id for m in self.members]
        if len(scene_ids) != len(set(scene_ids)):
            raise ValueError("a scene may be a member only once")
        if len(self.members) > self.curation.max_candidates:
            raise ValueError("members exceed max_candidates")
        if len(self.members) + self.rejected_scene_count != self.input_scene_count:
            # max_candidates truncation is counted as rejection.
            raise ValueError("members and rejected scenes must account for every input")
        return self

    def to_canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json"))

    def checksum(self) -> str:
        return sha256_checksum(self.to_canonical_bytes())

    def label_set(self) -> LabelSetRef | None:
        return self.curation.label_set

    def scene_ids(self) -> list[str]:
        return [m.scene_id for m in self.members]

    def member(self, scene_id: str) -> ScenarioMember | None:
        for member in self.members:
            if member.scene_id == scene_id:
                return member
        return None


def load_canonical_scenario_set(data: bytes) -> ScenarioSetManifest:
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ScenarioSetError(f"scenario set is not UTF-8 JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ScenarioSetError("scenario set must be a JSON object")
    version = payload.get("schema_version")
    if version != SCENARIO_SET_SCHEMA_V1:
        raise UnsupportedScenarioSetVersionError(
            f"unsupported scenario set schema_version: {version!r}"
        )
    try:
        manifest = ScenarioSetManifest.model_validate(payload)
    except ValidationError as exc:
        raise ScenarioSetError(f"invalid scenario set: {exc}") from exc
    if manifest.to_canonical_bytes() != data:
        raise NonCanonicalScenarioSetError(
            "scenario set bytes are not in canonical form"
        )
    return manifest


__all__ = [
    "SCENARIO_SET_SCHEMA_V1",
    "NonCanonicalScenarioSetError",
    "ScenarioCuration",
    "ScenarioMember",
    "ScenarioSetError",
    "ScenarioSetManifest",
    "ScenarioSetRef",
    "ScenarioSortKey",
    "SortOrder",
    "UnsupportedScenarioSetVersionError",
    "load_canonical_scenario_set",
]
