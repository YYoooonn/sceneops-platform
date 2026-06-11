from __future__ import annotations

from typing import Any, TypeAlias

from sceneops_core.runs.schemas import RunType
from sceneops_core.scenes.schemas.records import SceneRecord
from sceneops_core.scenes.schemas.runs import (
    SceneComparisonRunRecord,
    ScenePackageExportRunRecord,
    SceneProfileRunRecord,
    SceneReconstructionRunRecord,
    SceneValidationRunRecord,
)

from sceneops_db.models.scenes import SceneModel, SceneRunRecordModel

from ._utils import (
    base_run_to_values,
    enum_to_value,
    error_from_json,
    metadata_from_model,
)

SceneRunRecord: TypeAlias = (
    SceneValidationRunRecord
    | SceneProfileRunRecord
    | SceneComparisonRunRecord
    | SceneReconstructionRunRecord
    | ScenePackageExportRunRecord
)

_SCENE_RUN_TYPE_MAP: dict[str, type[SceneRunRecord]] = {
    RunType.SCENE_VALIDATION.value: SceneValidationRunRecord,
    RunType.SCENE_PROFILE.value: SceneProfileRunRecord,
    RunType.SCENE_COMPARISON.value: SceneComparisonRunRecord,
    RunType.SCENE_RECONSTRUCTION.value: SceneReconstructionRunRecord,
    RunType.SCENE_PACKAGE_EXPORT.value: ScenePackageExportRunRecord,
}


# ── Scene ─────────────────────────────────────────────────────────────────────


def scene_model_to_record(model: SceneModel) -> SceneRecord:
    # SceneRecord has no fields for world_state_manifest_uri, artifact_root_uri,
    # parent_scene_id, lineage, or generation. Preserve them in metadata so
    # they survive a round-trip through the core layer.
    base_meta = metadata_from_model(model)
    db_extras: dict[str, Any] = {}
    if model.world_state_manifest_uri is not None:
        db_extras["world_state_manifest_uri"] = model.world_state_manifest_uri
    if model.artifact_root_uri is not None:
        db_extras["artifact_root_uri"] = model.artifact_root_uri
    if model.parent_scene_id is not None:
        db_extras["parent_scene_id"] = model.parent_scene_id
    if model.lineage is not None:
        db_extras["lineage"] = model.lineage
    if model.generation is not None:
        db_extras["generation"] = model.generation

    return SceneRecord(
        scene_id=model.scene_id,
        dataset_id=model.dataset_id,
        dataset_version=model.dataset_version,
        raw_log_id=model.raw_log_id,
        segment_id=model.segment_id,
        status=model.status,
        origin_type=model.origin_type,
        generation_method=model.generation_method,
        scene_manifest_uri=model.scene_manifest_uri,
        sample_count=model.sample_count,
        frame_count=model.frame_count,
        channels=list(model.channels or []),
        started_at=model.started_at,
        ended_at=model.ended_at,
        metadata={**base_meta, **db_extras} if db_extras else base_meta,
    )


def scene_record_to_values(record: SceneRecord) -> dict[str, Any]:
    meta = record.metadata or {}
    values: dict[str, Any] = {
        "scene_id": record.scene_id,
        "dataset_id": record.dataset_id,
        "dataset_version": record.dataset_version,
        "raw_log_id": record.raw_log_id,
        "segment_id": record.segment_id,
        "status": enum_to_value(record.status),
        "origin_type": enum_to_value(record.origin_type),
        "generation_method": enum_to_value(record.generation_method),
        "scene_manifest_uri": record.scene_manifest_uri,
        "sample_count": record.sample_count,
        "frame_count": record.frame_count,
        "channels": list(record.channels or []),
        "metadata_": meta,
    }
    # Restore DB-only fields that were stashed in metadata by scene_model_to_record.
    if "world_state_manifest_uri" in meta:
        values["world_state_manifest_uri"] = meta["world_state_manifest_uri"]
    if "artifact_root_uri" in meta:
        values["artifact_root_uri"] = meta["artifact_root_uri"]
    if "parent_scene_id" in meta:
        values["parent_scene_id"] = meta["parent_scene_id"]
    if "lineage" in meta:
        values["lineage"] = meta["lineage"]
    if "generation" in meta:
        values["generation"] = meta["generation"]
    return values


# ── SceneRunRecord ────────────────────────────────────────────────────────────


def scene_run_model_to_record(model: SceneRunRecordModel) -> SceneRunRecord:
    cls = _SCENE_RUN_TYPE_MAP.get(model.type)
    if cls is None:
        raise ValueError(f"Unknown scene run type: {model.type!r}")

    base = dict(
        run_id=model.run_id,
        type=model.type,
        status=model.status,
        pipeline_run_id=model.pipeline_run_id,
        pipeline_task_run_id=model.pipeline_task_run_id,
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

    if model.type == RunType.SCENE_VALIDATION.value:
        s = model.summary or {}
        return SceneValidationRunRecord(
            **base,
            scene_id=model.scene_id,
            scene_manifest_uri=model.scene_manifest_uri,
            dataset_id=model.dataset_id,
            dataset_version=model.dataset_version,
            validation_report_uri=model.report_uri,
            validation_status=s.get("validation_status"),
            should_block_pipeline=s.get("should_block_pipeline", False),
            checked_sample_count=s.get("checked_sample_count"),
            checked_frame_count=s.get("checked_frame_count"),
            issue_count=s.get("issue_count"),
            error_count=s.get("error_count"),
            warning_count=s.get("warning_count"),
            missing_channel_count=s.get("missing_channel_count"),
            missing_artifact_count=s.get("missing_artifact_count"),
            summary=s,
        )
    elif model.type == RunType.SCENE_PROFILE.value:
        s = model.summary or {}
        return SceneProfileRunRecord(
            **base,
            scene_id=model.scene_id,
            scene_manifest_uri=model.scene_manifest_uri,
            dataset_id=model.dataset_id,
            dataset_version=model.dataset_version,
            profile_report_uri=model.report_uri,
            sample_count=s.get("sample_count"),
            frame_count=s.get("frame_count"),
            asset_count=s.get("asset_count"),
            annotation_count=s.get("annotation_count"),
            observed_channels=s.get("observed_channels", []),
            asset_summary=s.get("asset_summary", {}),
            world_state_summary=s.get("world_state_summary", {}),
            annotation_summary=s.get("annotation_summary", {}),
        )
    elif model.type == RunType.SCENE_COMPARISON.value:
        return SceneComparisonRunRecord(
            **base,
            source_scene_id=model.source_scene_id,
            source_scene_manifest_uri=model.scene_manifest_uri,
            target_scene_id=model.target_scene_id,
            comparison_report_uri=model.report_uri,
            summary=model.summary or {},
        )
    elif model.type == RunType.SCENE_RECONSTRUCTION.value:
        return SceneReconstructionRunRecord(
            **base,
            raw_log_id=model.raw_log_id,
            raw_log_manifest_uri=model.raw_log_manifest_uri,
            raw_log_frame_index_uri=model.raw_log_frame_index_uri,
            scene_id=model.scene_id,
            scene_manifest_uri=model.scene_manifest_uri,
            world_state_manifest_uri=model.world_state_manifest_uri,
        )
    else:  # SCENE_PACKAGE_EXPORT
        return ScenePackageExportRunRecord(
            **base,
            scene_id=model.scene_id,
            scene_manifest_uri=model.scene_manifest_uri or "",
            package_uri=model.package_uri,
            summary=model.summary or {},
        )


def scene_run_record_to_values(record: SceneRunRecord) -> dict[str, Any]:
    base = base_run_to_values(record)

    if isinstance(record, SceneValidationRunRecord):
        summary = {
            **(record.summary or {}),
            "validation_status": record.validation_status,
            "should_block_pipeline": record.should_block_pipeline,
            "checked_sample_count": record.checked_sample_count,
            "checked_frame_count": record.checked_frame_count,
            "issue_count": record.issue_count,
            "error_count": record.error_count,
            "warning_count": record.warning_count,
            "missing_channel_count": record.missing_channel_count,
            "missing_artifact_count": record.missing_artifact_count,
        }
        return {
            **base,
            "scene_id": record.scene_id,
            "scene_manifest_uri": record.scene_manifest_uri,
            "dataset_id": record.dataset_id,
            "dataset_version": record.dataset_version,
            "report_uri": record.validation_report_uri,
            "summary": summary,
        }
    elif isinstance(record, SceneProfileRunRecord):
        summary = {
            "sample_count": record.sample_count,
            "frame_count": record.frame_count,
            "asset_count": record.asset_count,
            "annotation_count": record.annotation_count,
            "observed_channels": record.observed_channels,
            "asset_summary": record.asset_summary,
            "world_state_summary": record.world_state_summary,
            "annotation_summary": record.annotation_summary,
        }
        return {
            **base,
            "scene_id": record.scene_id,
            "scene_manifest_uri": record.scene_manifest_uri,
            "dataset_id": record.dataset_id,
            "dataset_version": record.dataset_version,
            "report_uri": record.profile_report_uri,
            "summary": summary,
        }
    elif isinstance(record, SceneComparisonRunRecord):
        return {
            **base,
            "source_scene_id": record.source_scene_id,
            "scene_manifest_uri": record.source_scene_manifest_uri,
            "target_scene_id": record.target_scene_id,
            "report_uri": record.comparison_report_uri,
            "summary": record.summary or {},
        }
    elif isinstance(record, SceneReconstructionRunRecord):
        return {
            **base,
            "raw_log_id": record.raw_log_id,
            "raw_log_manifest_uri": record.raw_log_manifest_uri,
            "raw_log_frame_index_uri": record.raw_log_frame_index_uri,
            "scene_id": record.scene_id,
            "scene_manifest_uri": record.scene_manifest_uri,
            "world_state_manifest_uri": record.world_state_manifest_uri,
        }
    else:  # ScenePackageExportRunRecord
        return {
            **base,
            "scene_id": record.scene_id,
            "scene_manifest_uri": record.scene_manifest_uri,
            "package_uri": record.package_uri,
            "summary": record.summary or {},
        }
