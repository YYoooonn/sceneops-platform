from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict
from sceneops_core.inference.enums import InferenceBackendType

from .base import BaseRunRecord


class AutoLabelRunRecord(BaseRunRecord):
    dataset_id: str
    dataset_version: str
    model_id: str
    model_version: str

    vlm_backend: InferenceBackendType = InferenceBackendType.VLM

    sample_count: int | None = None
    labeled_sample_count: int | None = None

    auto_label_manifest_uri: str | None = None
    samples_root_uri: str | None = None

    metrics: JsonDict = Field(default_factory=dict)
    class_metrics: JsonDict = Field(default_factory=dict)
