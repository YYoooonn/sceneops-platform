from __future__ import annotations

from typing import Any

from sceneops_core.artifacts.schemas.refs import ArtifactRef

from sceneops_db.models.artifacts import ArtifactRefModel

from ._utils import metadata_from_model


def artifact_ref_model_to_ref(model: ArtifactRefModel) -> ArtifactRef:
    return ArtifactRef(
        kind=model.kind,
        uri=model.uri,
        media_type=model.media_type,
        size_bytes=model.size_bytes,
        checksum=model.checksum,
        metadata=metadata_from_model(model),
    )


def artifact_ref_to_values(ref: ArtifactRef) -> dict[str, Any]:
    return {
        "kind": ref.kind.value if hasattr(ref.kind, "value") else ref.kind,
        "uri": ref.uri,
        "media_type": ref.media_type,
        "size_bytes": ref.size_bytes,
        "checksum": ref.checksum,
        "metadata_": ref.metadata or {},
    }


def artifact_ref_to_values_with_owner(
    ref: ArtifactRef,
    *,
    artifact_id: str,
    owner_type: str | None = None,
    owner_id: str | None = None,
    dataset_id: str | None = None,
    dataset_version: str | None = None,
    scene_id: str | None = None,
    scenario_set_id: str | None = None,
    run_id: str | None = None,
    job_id: str | None = None,
    pipeline_run_id: str | None = None,
    backend: str | None = None,
) -> dict[str, Any]:
    values = artifact_ref_to_values(ref)
    values.update(
        {
            "artifact_id": artifact_id,
            "backend": backend,
            "owner_type": owner_type,
            "owner_id": owner_id,
            "dataset_id": dataset_id,
            "dataset_version": dataset_version,
            "scene_id": scene_id,
            "scenario_set_id": scenario_set_id,
            "run_id": run_id,
            "job_id": job_id,
            "pipeline_run_id": pipeline_run_id,
        }
    )
    return values
