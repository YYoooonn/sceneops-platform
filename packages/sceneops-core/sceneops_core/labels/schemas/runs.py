from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict
from sceneops_core.runs.schemas import BaseRunRecord, RunType


class SceneAutoLabelRunRecord(BaseRunRecord):
    type: RunType = RunType.SCENE_AUTO_LABEL

    scene_id: str | None = None
    scene_manifest_uri: str

    labeler_id: str | None = None
    labeler_backend: str = "vlm"

    output_scene_manifest_uri: str | None = None
    output_label_uri: str | None = None

    sample_count: int | None = None
    labeled_sample_count: int | None = None
    annotation_count: int = 0

    metrics: JsonDict = Field(default_factory=dict)


class DatasetAutoLabelRunRecord(BaseRunRecord):
    type: RunType = RunType.DATASET_AUTO_LABEL

    dataset_id: str
    dataset_version: str

    dataset_manifest_uri: str | None = None

    labeler_id: str | None = None
    labeler_backend: str = "vlm"

    output_dataset_id: str | None = None
    output_dataset_version: str | None = None
    output_dataset_manifest_uri: str | None = None

    labeled_scene_count: int = 0
    annotation_count: int = 0

    metrics: JsonDict = Field(default_factory=dict)
