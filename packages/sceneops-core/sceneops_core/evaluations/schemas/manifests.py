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
)

from sceneops_core.common.canonical_json import canonical_json_bytes
from sceneops_core.common.checksums import SHA256_CHECKSUM_PATTERN, sha256_checksum
from sceneops_core.common.schemas import SceneOpsBaseModel
from sceneops_core.inference.schemas.manifests import PredictionRevisionRef
from sceneops_core.labels.schemas import LabelSetRef
from sceneops_core.sample_views.schemas import SampleViewRef
from sceneops_core.scenarios.schemas.manifests import ScenarioSetRef


EVALUATION_MANIFEST_SCHEMA_V1: Final = "sceneops.detection_evaluation_manifest/v1"


class EvaluationManifestError(ValueError):
    """Bytes are not a valid canonical evaluation manifest."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EvaluationInputs(SceneOpsBaseModel):
    """The exact revisions an evaluation scored (ADR-007 §33.5): the
    prediction manifest revision, the label set revision that defines
    ground truth, and the sample view revisions whose samples were
    compared. Nothing here is resolved at read time: re-running the
    evaluation with these pins reads the same data."""

    prediction: PredictionRevisionRef
    label_set: LabelSetRef
    sample_views: list[SampleViewRef] = Field(default_factory=list)
    scenario_set: ScenarioSetRef | None = None


class EvaluationSampleShardRef(_Model):
    """One evaluated sample's result, pinned by checksum."""

    scene_id: StrictStr
    sample_id: StrictStr
    uri: StrictStr
    checksum: StrictStr = Field(pattern=SHA256_CHECKSUM_PATTERN)


class DetectionEvaluationManifest(_Model):
    """The pinned output of one detection evaluation (ADR-007 §33.5).

    Derived and immutable: one serialized manifest is one revision, identified
    by its checksum. It records what was scored (``inputs``), the result, and
    one checksum-pinned shard per evaluated sample. It holds no storage
    location of itself, execution context or timestamp: those belong to the
    ArtifactRecord and the run record, so re-evaluating the same pins yields
    the same bytes.
    """

    schema_version: Literal["sceneops.detection_evaluation_manifest/v1"] = (
        EVALUATION_MANIFEST_SCHEMA_V1
    )
    evaluation_run_id: StrictStr
    inference_run_id: StrictStr

    dataset_id: StrictStr
    dataset_version: StrictStr

    model_id: StrictStr | None = None
    model_version: StrictStr | None = None

    inputs: EvaluationInputs

    status: Literal["succeeded", "skipped"] = "succeeded"
    match_distance_m: float

    sample_count: StrictInt | None = None
    prediction_count: StrictInt | None = None
    evaluable_prediction_count: StrictInt | None = None
    lifting_failed_prediction_count: StrictInt | None = None
    ground_truth_count: StrictInt | None = None
    evaluation_unit: StrictStr | None = None

    primary_metric_name: StrictStr | None = None
    primary_metric_value: float | None = None

    metrics: dict[str, Any] = Field(default_factory=dict)
    class_metrics: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    # Ordered by (scene_id, sample_id); empty for a skipped evaluation.
    sample_shards: list[EvaluationSampleShardRef] = Field(default_factory=list)

    def to_canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json"))

    def checksum(self) -> str:
        return sha256_checksum(self.to_canonical_bytes())


def load_canonical_evaluation_manifest(data: bytes) -> DetectionEvaluationManifest:
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvaluationManifestError(
            f"evaluation manifest is not UTF-8 JSON: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise EvaluationManifestError("evaluation manifest must be a JSON object")
    version = payload.get("schema_version")
    if version != EVALUATION_MANIFEST_SCHEMA_V1:
        raise EvaluationManifestError(
            f"unsupported evaluation manifest schema_version: {version!r}"
        )
    try:
        manifest = DetectionEvaluationManifest.model_validate(payload)
    except ValidationError as exc:
        raise EvaluationManifestError(f"invalid evaluation manifest: {exc}") from exc
    if manifest.to_canonical_bytes() != data:
        raise EvaluationManifestError(
            "evaluation manifest bytes are not in canonical form"
        )
    return manifest
