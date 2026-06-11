from __future__ import annotations

from sceneops_core.common.schemas import JsonDict
from sceneops_core.datasets.schemas import DatasetManifest
from sceneops_core.jobs.schemas.params import (
    DetectionSceneSelectionConfig,
    DetectionSceneSelectionMode,
)
from sceneops_worker.scenes import SceneArtifactStore


async def select_detection_scenes(
    *,
    dataset_manifest: DatasetManifest,
    scene_artifact_store: SceneArtifactStore,
    selection: DetectionSceneSelectionConfig,
) -> JsonDict:
    requested_scene_ids = set(selection.scene_ids)

    selected_scene_ids: list[str] = []
    skipped_scenes: list[JsonDict] = []

    total_scene_count = 0
    inspected_scene_count = 0
    selected_annotation_count = 0
    selected_sample_count = 0

    for scene_entry in dataset_manifest.scenes:
        total_scene_count += 1

        scene_id = scene_entry.scene_id

        if requested_scene_ids and scene_id not in requested_scene_ids:
            skipped_scenes.append(
                {
                    "scene_id": scene_id,
                    "reason": "not_in_requested_scene_ids",
                }
            )
            continue

        scene_manifest = await scene_artifact_store.load_scene_manifest(
            scene_entry.scene_manifest_uri
        )
        if scene_manifest is None:
            skipped_scenes.append(
                {
                    "scene_id": scene_id,
                    "reason": "scene_manifest_not_found",
                }
            )
            continue

        inspected_scene_count += 1

        annotation_count = int(scene_manifest.annotation_count or 0)
        sample_count = int(scene_manifest.sample_count or len(scene_manifest.samples))
        has_ground_truth = bool(scene_manifest.has_ground_truth) or annotation_count > 0

        if selection.mode == DetectionSceneSelectionMode.GROUND_TRUTH_ONLY:
            min_annotation_count = int(selection.min_annotation_count or 1)

            if not has_ground_truth or annotation_count < min_annotation_count:
                skipped_scenes.append(
                    {
                        "scene_id": scene_id,
                        "reason": "scene_has_no_ground_truth",
                        "annotation_count": annotation_count,
                        "has_ground_truth": has_ground_truth,
                        "sample_count": sample_count,
                    }
                )
                continue

            if selection.ground_truth_sources:
                ground_truth_source = scene_manifest.ground_truth_source
                if ground_truth_source not in selection.ground_truth_sources:
                    skipped_scenes.append(
                        {
                            "scene_id": scene_id,
                            "reason": "ground_truth_source_not_allowed",
                            "ground_truth_source": ground_truth_source,
                            "allowed_ground_truth_sources": list(
                                selection.ground_truth_sources
                            ),
                            "sample_count": sample_count,
                        }
                    )
                    continue

        selected_scene_ids.append(scene_manifest.scene_id)
        selected_annotation_count += annotation_count
        selected_sample_count += sample_count

        if selection.max_scenes is not None:
            if len(selected_scene_ids) >= selection.max_scenes:
                break

    return {
        "mode": getattr(selection.mode, "value", selection.mode),
        "requested_scene_count": len(requested_scene_ids),
        "requested_scene_ids": sorted(requested_scene_ids),
        "selected_scene_ids": selected_scene_ids,
        "selected_scene_count": len(selected_scene_ids),
        "selected_sample_count": selected_sample_count,
        "selected_annotation_count": selected_annotation_count,
        "total_scene_count": total_scene_count,
        "inspected_scene_count": inspected_scene_count,
        "skipped_scene_count": len(skipped_scenes),
        "skipped_scenes": skipped_scenes[:100],
        "max_scenes": selection.max_scenes,
        "max_samples": selection.max_samples,
        "max_samples_per_scene": getattr(selection, "max_samples_per_scene", None),
        "min_annotation_count": getattr(selection, "min_annotation_count", None),
        "ground_truth_sources": list(
            getattr(selection, "ground_truth_sources", []) or []
        ),
    }
