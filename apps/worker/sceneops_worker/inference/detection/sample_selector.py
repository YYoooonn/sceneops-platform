"""DetectionSampleSelector — selects samples from a dataset for detection inference.

Responsibility: traverse the dataset manifest, validate scene selection criteria,
resolve image URIs, and return a list of DetectionSampleInput ready for inference.

This class does not perform inference or write any artifacts.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sceneops_core.datasets.schemas import DatasetManifest
from sceneops_worker.inference.detection.base import DetectionSampleInput
from sceneops_worker.inference.detection.uris import resolve_raw_uri
from sceneops_worker.scenes import SceneArtifactStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SampleSelectionConfig:
    """Parameters that govern which samples are selected for inference.

    dataset_id / dataset_version: used to populate DetectionSampleInput metadata.
    camera_channel:               which sensor channel provides the image.
    raw_source_root_uri:          base URI for resolving sensor-relative paths.
    scene_ids:                    if set, only these scenes are considered.
    max_scenes:                   cap on number of scenes selected.
    max_samples:                  global cap on total samples selected.
    enable_3d_lifting:            if False, lidar_uri and lidar_sensor_frame are omitted.
    """

    dataset_id: str
    dataset_version: str
    camera_channel: str
    raw_source_root_uri: str
    scene_ids: list[str] | None = None
    max_scenes: int | None = None
    max_samples: int | None = None
    enable_3d_lifting: bool = True


class DetectionSampleSelector:
    """Selects DetectionSampleInput objects from a dataset manifest.

    Selection order:
      1. Filter by scene_ids whitelist (fail-fast on unknown IDs).
      2. Apply max_scenes from the front of the remaining list.
      3. Iterate scene samples; resolve camera image URI.
         - Missing camera channel: warn + skip.
      4. Stop when max_samples is reached globally.
    """

    async def select(
        self,
        dataset_manifest: DatasetManifest,
        scene_artifact_store: SceneArtifactStore,
        config: SampleSelectionConfig,
    ) -> list[DetectionSampleInput]:
        scene_entries = list(dataset_manifest.scenes)

        # ── validate and filter scene_ids ──────────────────────────────────────
        if config.scene_ids is not None:
            available_ids = {entry.scene_id for entry in scene_entries}
            unknown = set(config.scene_ids) - available_ids
            if unknown:
                raise ValueError(
                    f"Requested scene_ids not found in dataset manifest: {sorted(unknown)}"
                )
            requested = set(config.scene_ids)
            scene_entries = [e for e in scene_entries if e.scene_id in requested]

        # ── apply max_scenes ───────────────────────────────────────────────────
        if config.max_scenes is not None:
            scene_entries = scene_entries[: config.max_scenes]

        # ── iterate samples ────────────────────────────────────────────────────
        selected: list[DetectionSampleInput] = []
        skipped_no_channel = 0

        for scene_entry in scene_entries:
            if config.max_samples is not None and len(selected) >= config.max_samples:
                break

            scene_manifest = await scene_artifact_store.load_scene_manifest(
                scene_entry.scene_manifest_uri
            )
            if scene_manifest is None:
                logger.warning(
                    "Scene manifest not found; skipping: %s",
                    scene_entry.scene_manifest_uri,
                )
                continue

            # Build scene-level registry indexes once per scene manifest
            calibrated_sensor_index = {
                c.calibration_id: c for c in scene_manifest.calibrated_sensors
            }
            ego_pose_index = {p.ego_pose_id: p for p in scene_manifest.ego_poses}

            for sample in scene_manifest.samples:
                if (
                    config.max_samples is not None
                    and len(selected) >= config.max_samples
                ):
                    break

                sensors_by_channel = {sf.channel: sf for sf in sample.sensor_frames}
                camera_sf = sensors_by_channel.get(config.camera_channel)

                if camera_sf is None:
                    skipped_no_channel += 1
                    logger.warning(
                        "Sample %s in scene %s has no %s channel — skipping",
                        sample.sample_id,
                        sample.scene_id,
                        config.camera_channel,
                    )
                    continue

                image_uri = resolve_raw_uri(config.raw_source_root_uri, camera_sf.uri)

                lidar_uri = None
                lidar_sf = None
                if config.enable_3d_lifting:
                    lidar_sf = sensors_by_channel.get("LIDAR_TOP")
                    if lidar_sf is not None:
                        lidar_uri = resolve_raw_uri(
                            config.raw_source_root_uri, lidar_sf.uri
                        )

                selected.append(
                    DetectionSampleInput(
                        dataset_id=config.dataset_id,
                        dataset_version=config.dataset_version,
                        scene_id=sample.scene_id,
                        sample_id=sample.sample_id,
                        camera_channel=config.camera_channel,
                        image_uri=image_uri,
                        timestamp_us=sample.timestamp_us,
                        lidar_uri=lidar_uri,
                        camera_sensor_frame=camera_sf,
                        lidar_sensor_frame=lidar_sf,
                        scene_manifest_uri=scene_entry.scene_manifest_uri,
                        calibrated_sensor_index=calibrated_sensor_index,
                        ego_pose_index=ego_pose_index,
                    )
                )

        if skipped_no_channel > 0:
            logger.warning(
                "Skipped %d sample(s) with no %s sensor channel",
                skipped_no_channel,
                config.camera_channel,
            )

        return selected
