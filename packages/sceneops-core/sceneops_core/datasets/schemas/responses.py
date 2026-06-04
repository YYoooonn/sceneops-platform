from __future__ import annotations

from sceneops_core.common.schemas import SceneOpsBaseModel

from .manifests import DatasetManifest
from .profile import DatasetProfileReport
from .records import DatasetRecord, DatasetVersionRecord
from .validation import DatasetValidationReport


class DatasetDetailResponse(SceneOpsBaseModel):
    dataset: DatasetRecord


class DatasetListResponse(SceneOpsBaseModel):
    datasets: list[DatasetRecord]
    count: int


class DatasetVersionDetailResponse(SceneOpsBaseModel):
    version: DatasetVersionRecord


class DatasetVersionListResponse(SceneOpsBaseModel):
    versions: list[DatasetVersionRecord]
    count: int


class DatasetManifestResponse(SceneOpsBaseModel):
    manifest: DatasetManifest


class DatasetValidationReportResponse(SceneOpsBaseModel):
    report: DatasetValidationReport


class DatasetProfileReportResponse(SceneOpsBaseModel):
    report: DatasetProfileReport
