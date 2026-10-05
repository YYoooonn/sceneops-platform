"""DetectionPredictionManifest v1: the pinned output of one detection run
(ADR-007 §33.5).

It is derived (L3) and immutable. It records exactly what was run and on
what, so an evaluation can name the *revision* of the predictions it
scored and verify it:

    inputs        the SceneSampleView revisions and sample ids that ran
    scenario_set  the ScenarioSet revision they came from, if any
    config        the explicit run configuration
    shards        one per sample, each pinned by checksum

One serialized manifest is one revision, identified by its checksum. The
manifest holds no storage location of itself, execution context or
timestamp; where its shards live is recorded per shard.
"""

from __future__ import annotations

import json
from typing import Any, Final, Literal

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
from sceneops_core.sample_views.schemas import SampleViewRef
from sceneops_core.scenarios.schemas.manifests import ScenarioSetRef
from sceneops_core.scenes.schemas.manifests import LocalId

PREDICTION_MANIFEST_SCHEMA_V1: Final = "sceneops.detection_prediction_manifest/v1"


class PredictionManifestError(ValueError):
    """Bytes are not a valid canonical prediction manifest."""


class UnsupportedPredictionManifestVersionError(PredictionManifestError):
    pass


class NonCanonicalPredictionManifestError(PredictionManifestError):
    pass


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PredictionInputRef(_Model):
    """One pinned view and the samples of it that ran."""

    sample_view: SampleViewRef
    sample_ids: list[LocalId] = Field(min_length=1)

    @model_validator(mode="after")
    def _sorted_unique(self) -> PredictionInputRef:
        if self.sample_ids != sorted(set(self.sample_ids)):
            raise ValueError("sample_ids must be sorted and unique")
        return self


class DetectionPredictionShardRef(_Model):
    """One sample's predictions, pinned by checksum."""

    scene_id: LocalId
    sample_id: LocalId
    uri: StrictStr
    checksum: StrictStr = Field(pattern=SHA256_CHECKSUM_PATTERN)
    prediction_count: StrictInt = Field(ge=0)


class PredictionRevisionRef(_Model):
    """Pins exactly one prediction manifest revision."""

    inference_run_id: LocalId
    manifest_artifact_id: LocalId
    manifest_checksum: StrictStr = Field(pattern=SHA256_CHECKSUM_PATTERN)


class DetectionPredictionManifest(_Model):
    schema_version: Literal["sceneops.detection_prediction_manifest/v1"] = (
        PREDICTION_MANIFEST_SCHEMA_V1
    )
    inference_run_id: LocalId
    dataset_id: StrictStr
    dataset_version: StrictStr

    # Run configuration, as the JSON of DetectionInferenceConfig.
    config: dict[str, Any]
    inputs: list[PredictionInputRef]
    scenario_set: ScenarioSetRef | None = None

    scene_count: StrictInt = Field(ge=0)
    sample_count: StrictInt = Field(ge=0)
    prediction_count: StrictInt = Field(ge=0)
    evaluable_prediction_count: StrictInt = Field(ge=0)
    lifting_succeeded_count: StrictInt = Field(ge=0)
    lifting_failed_count: StrictInt = Field(ge=0)
    lifting_not_applicable_count: StrictInt = Field(ge=0)

    # Ordered by (scene_id, sample_id).
    prediction_shards: list[DetectionPredictionShardRef]

    @model_validator(mode="after")
    def _check(self) -> DetectionPredictionManifest:
        order = [(s.scene_id, s.sample_id) for s in self.prediction_shards]
        if order != sorted(set(order)):
            raise ValueError("prediction_shards must be sorted and unique")
        ran = {
            (item.sample_view.scene_id, sample_id)
            for item in self.inputs
            for sample_id in item.sample_ids
        }
        if set(order) - ran:
            raise ValueError("a prediction shard is outside the pinned inputs")
        if sum(s.prediction_count for s in self.prediction_shards) != (
            self.prediction_count
        ):
            raise ValueError("prediction_count must equal the shard total")
        return self

    def to_canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json"))

    def checksum(self) -> str:
        return sha256_checksum(self.to_canonical_bytes())

    def input_for_scene(self, scene_id: str) -> PredictionInputRef | None:
        for item in self.inputs:
            if item.sample_view.scene_id == scene_id:
                return item
        return None


def load_canonical_prediction_manifest(data: bytes) -> DetectionPredictionManifest:
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PredictionManifestError(
            f"prediction manifest is not UTF-8 JSON: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise PredictionManifestError("prediction manifest must be a JSON object")
    version = payload.get("schema_version")
    if version != PREDICTION_MANIFEST_SCHEMA_V1:
        raise UnsupportedPredictionManifestVersionError(
            f"unsupported prediction manifest schema_version: {version!r}"
        )
    try:
        manifest = DetectionPredictionManifest.model_validate(payload)
    except ValidationError as exc:
        raise PredictionManifestError(f"invalid prediction manifest: {exc}") from exc
    if manifest.to_canonical_bytes() != data:
        raise NonCanonicalPredictionManifestError(
            "prediction manifest bytes are not in canonical form"
        )
    return manifest


__all__ = [
    "PREDICTION_MANIFEST_SCHEMA_V1",
    "DetectionPredictionManifest",
    "DetectionPredictionShardRef",
    "NonCanonicalPredictionManifestError",
    "PredictionInputRef",
    "PredictionManifestError",
    "PredictionRevisionRef",
    "UnsupportedPredictionManifestVersionError",
    "load_canonical_prediction_manifest",
]
