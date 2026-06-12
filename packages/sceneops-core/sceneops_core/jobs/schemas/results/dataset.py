from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict

from .base import BaseJobResult


class AutoLabelDatasetJobResult(BaseJobResult):
    output_dataset_id: str | None = None
    output_dataset_version: str | None = None

    output_dataset_manifest_uri: str | None = None

    labeled_scene_count: int = 0
    annotation_count: int = 0

    metadata: JsonDict = Field(default_factory=dict)


class CheckDistributionJobResult(BaseJobResult):
    report_uri: str | None = None

    summary: JsonDict = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)

    metadata: JsonDict = Field(default_factory=dict)


class ExportDatasetJobResult(BaseJobResult):
    export_uri: str

    output_format: str = "sceneops"

    exported_scene_count: int = 0
    exported_sample_count: int = 0

    metadata: JsonDict = Field(default_factory=dict)
