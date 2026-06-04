from __future__ import annotations

from typing import Any, TypeAlias

from sceneops_core.labels.schemas.runs import (
    DatasetAutoLabelRunRecord,
    SceneAutoLabelRunRecord,
)
from sceneops_core.runs.schemas import RunType

from sceneops_db.models.labels import LabelRunModel

from ._utils import base_run_to_values, error_from_json, metadata_from_model

LabelRunRecord: TypeAlias = SceneAutoLabelRunRecord | DatasetAutoLabelRunRecord

_LABEL_RUN_TYPE_MAP: dict[str, type[LabelRunRecord]] = {
    RunType.SCENE_AUTO_LABEL.value: SceneAutoLabelRunRecord,
    RunType.DATASET_AUTO_LABEL.value: DatasetAutoLabelRunRecord,
}


def label_run_model_to_record(model: LabelRunModel) -> LabelRunRecord:
    cls = _LABEL_RUN_TYPE_MAP.get(model.type)
    if cls is None:
        raise ValueError(f"Unknown label run type: {model.type!r}")

    base = dict(
        run_id=model.run_id,
        type=model.type,
        status=model.status,
        pipeline_run_id=model.pipeline_run_id,
        pipeline_step_run_id=model.pipeline_step_run_id,
        job_id=model.job_id,
        params=model.params or {},
        result=model.result,
        error=error_from_json(model.error),
        artifact_root_uri=model.artifact_root_uri,
        manifest_uri=model.manifest_uri,
        created_at=model.created_at,
        updated_at=model.updated_at,
        started_at=model.started_at,
        finished_at=model.finished_at,
        metadata=metadata_from_model(model),
    )

    if model.type == RunType.SCENE_AUTO_LABEL.value:
        return SceneAutoLabelRunRecord(
            **base,
            scene_id=model.scene_id,
            scene_manifest_uri=model.scene_manifest_uri or "",
            labeler_id=model.labeler_id,
            labeler_backend=model.labeler_backend,
            output_scene_manifest_uri=model.output_scene_manifest_uri,
            output_label_uri=model.output_label_uri,
            sample_count=model.sample_count,
            labeled_sample_count=model.labeled_sample_count,
            annotation_count=model.annotation_count,
            metrics=model.metrics or {},
        )
    else:  # DATASET_AUTO_LABEL
        return DatasetAutoLabelRunRecord(
            **base,
            dataset_id=model.dataset_id or "",
            dataset_version=model.dataset_version or "",
            dataset_manifest_uri=model.dataset_manifest_uri,
            labeler_id=model.labeler_id,
            labeler_backend=model.labeler_backend,
            output_dataset_id=model.output_dataset_id,
            output_dataset_version=model.output_dataset_version,
            output_dataset_manifest_uri=model.output_dataset_manifest_uri,
            labeled_scene_count=model.labeled_scene_count,
            annotation_count=model.annotation_count,
            metrics=model.metrics or {},
        )


def label_run_record_to_values(record: LabelRunRecord) -> dict[str, Any]:
    base = base_run_to_values(record)

    if isinstance(record, SceneAutoLabelRunRecord):
        return {
            **base,
            "scene_id": record.scene_id,
            "scene_manifest_uri": record.scene_manifest_uri,
            "labeler_id": record.labeler_id,
            "labeler_backend": record.labeler_backend,
            "output_scene_manifest_uri": record.output_scene_manifest_uri,
            "output_label_uri": record.output_label_uri,
            "sample_count": record.sample_count,
            "labeled_sample_count": record.labeled_sample_count,
            "annotation_count": record.annotation_count,
            "metrics": record.metrics or {},
        }
    else:  # DatasetAutoLabelRunRecord
        return {
            **base,
            "dataset_id": record.dataset_id,
            "dataset_version": record.dataset_version,
            "dataset_manifest_uri": record.dataset_manifest_uri,
            "labeler_id": record.labeler_id,
            "labeler_backend": record.labeler_backend,
            "output_dataset_id": record.output_dataset_id,
            "output_dataset_version": record.output_dataset_version,
            "output_dataset_manifest_uri": record.output_dataset_manifest_uri,
            "labeled_scene_count": record.labeled_scene_count,
            "annotation_count": record.annotation_count,
            "metrics": record.metrics or {},
        }
