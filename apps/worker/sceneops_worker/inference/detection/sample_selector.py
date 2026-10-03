"""DetectionSampleSelector — selects keyframe samples from a dataset for
detection inference.

Traverses the derived dataset manifest, reads every selected Scene at the
revision the manifest pins, projects it into keyframe samples, locates the
selected observations' payload artifacts and returns DetectionSampleInput
records. It performs no inference and writes nothing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sceneops_core.datasets.schemas import DatasetManifest
from sceneops_worker.inference.detection.base import DetectionSampleInput
from sceneops_worker.inference.detection.uris import normalize_image_uri
from sceneops_worker.scenes import SceneArtifactStore
from sceneops_worker.scenes.keyframes import keyframe_samples, load_pinned_scene
from sceneops_worker.scenes.payloads import ArtifactPayloadLocator

logger = logging.getLogger(__name__)

# The lidar channel used for frustum lifting. Workflow configuration that
# still carries one source's vocabulary.
LIFTING_LIDAR_CHANNEL = "LIDAR_TOP"


@dataclass(frozen=True)
class SampleSelectionConfig:
    """Parameters that govern which samples are selected for inference.

    dataset_id / dataset_version: used to populate DetectionSampleInput metadata.
    camera_channel:               source channel that provides the image.
    scene_ids:                    if set, only these scenes are considered.
    max_scenes:                   cap on number of scenes selected.
    max_samples:                  global cap on total samples selected.
    enable_3d_lifting:            if False, the lidar observation is omitted.
    """

    dataset_id: str
    dataset_version: str
    camera_channel: str
    scene_ids: list[str] | None = None
    max_scenes: int | None = None
    max_samples: int | None = None
    enable_3d_lifting: bool = True


class DetectionSampleSelector:
    """Selection order:
    1. Filter by scene_ids whitelist (fail-fast on unknown IDs).
    2. Apply max_scenes from the front of the remaining list.
    3. Iterate each Scene's keyframes; take the camera channel's observation.
       - Keyframe without that channel: warn + skip.
    4. Stop when max_samples is reached globally.
    """

    async def select(
        self,
        dataset_manifest: DatasetManifest,
        scene_artifact_store: SceneArtifactStore,
        config: SampleSelectionConfig,
        payload_locator: ArtifactPayloadLocator,
    ) -> list[DetectionSampleInput]:
        scene_entries = list(dataset_manifest.scenes)

        if config.scene_ids is not None:
            available_ids = {entry.scene_id for entry in scene_entries}
            unknown = set(config.scene_ids) - available_ids
            if unknown:
                raise ValueError(
                    f"Requested scene_ids not found in dataset manifest: {sorted(unknown)}"
                )
            requested = set(config.scene_ids)
            scene_entries = [e for e in scene_entries if e.scene_id in requested]

        if config.max_scenes is not None:
            scene_entries = scene_entries[: config.max_scenes]

        selected: list[DetectionSampleInput] = []
        skipped_no_channel = 0

        for entry in scene_entries:
            if config.max_samples is not None and len(selected) >= config.max_samples:
                break

            manifest = await load_pinned_scene(scene_artifact_store, entry)
            samples = keyframe_samples(scene_id=entry.scene_id, manifest=manifest)
            uris = await payload_locator.uris(
                kept.observation.payload
                for sample in samples
                for channel, kept in sample.observations.items()
                if channel == config.camera_channel
                or (config.enable_3d_lifting and channel == LIFTING_LIDAR_CHANNEL)
            )
            for sample in samples:
                if (
                    config.max_samples is not None
                    and len(selected) >= config.max_samples
                ):
                    break

                camera = sample.observations.get(config.camera_channel)
                if camera is None:
                    skipped_no_channel += 1
                    logger.warning(
                        "Keyframe %s in scene %s has no %s observation — skipping",
                        sample.sample_id,
                        sample.scene_id,
                        config.camera_channel,
                    )
                    continue

                lidar = (
                    sample.observations.get(LIFTING_LIDAR_CHANNEL)
                    if config.enable_3d_lifting
                    else None
                )
                selected.append(
                    DetectionSampleInput(
                        dataset_id=config.dataset_id,
                        dataset_version=config.dataset_version,
                        scene_id=sample.scene_id,
                        sample_id=sample.sample_id,
                        camera_channel=config.camera_channel,
                        image_uri=normalize_image_uri(
                            uris[camera.observation.payload.artifact_id]
                        ),
                        timestamp_ns=camera.observation.timestamp_ns,
                        camera=camera,
                        lidar=lidar,
                        lidar_uri=(
                            uris[lidar.observation.payload.artifact_id]
                            if lidar is not None
                            else None
                        ),
                    )
                )

        if skipped_no_channel > 0:
            logger.warning(
                "Skipped %d keyframe(s) with no %s observation",
                skipped_no_channel,
                config.camera_channel,
            )

        return selected
