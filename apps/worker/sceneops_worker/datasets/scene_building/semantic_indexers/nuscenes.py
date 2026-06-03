from __future__ import annotations

import asyncio
import math
from pathlib import Path

from sceneops_worker.datasets.scene_building.indexers.nuscenes import (
    _modality_from_channel,
    _role_from_modality,
)
from sceneops_worker.datasets.scene_building.models import IndexedRawFrame
from sceneops_worker.datasets.scene_building.semantic_models import (
    EgoState,
    IndexedKeyframe,
    NearbyObject,
    enrich_ego_kinematics,
)


class NuscenesSemanticIndexer:
    """Indexes nuScenes keyframes with ego state and annotation context.

    Produces IndexedKeyframe per nuScenes sample (keyframe), including:
    - All sensor channel frames (same as NuscenesRawLogIndexer)
    - EgoState (position, rotation) from CAM_FRONT ego_pose
    - NearbyObject list from sample annotations with ego-relative distance
    - Speed / acceleration enriched via consecutive pose finite differences
    """

    def __init__(
        self,
        *,
        source_uri: str,
        version: str,
        max_keyframes: int | None = None,
    ) -> None:
        self.source_uri = source_uri
        self.version = version
        self.max_keyframes = max_keyframes

    async def index(self) -> list[IndexedKeyframe]:
        return await asyncio.to_thread(self._index_sync)

    def _index_sync(self) -> list[IndexedKeyframe]:
        # pylint: disable=import-outside-toplevel, no-name-in-module
        from nuscenes.nuscenes import NuScenes

        nusc = NuScenes(
            version=self.version,
            dataroot=self.source_uri,
            verbose=False,
        )

        keyframes: list[IndexedKeyframe] = []

        for sample in nusc.sample:
            if self.max_keyframes is not None and len(keyframes) >= self.max_keyframes:
                break

            frames = self._build_frames(nusc, sample)
            ego_state = self._build_ego_state(nusc, sample)
            nearby_objects = self._build_nearby_objects(nusc, sample, ego_state)

            keyframes.append(
                IndexedKeyframe(
                    timestamp_us=sample["timestamp"],
                    frames=tuple(frames),
                    ego_state=ego_state,
                    nearby_objects=tuple(nearby_objects),
                    source_keyframe_id=sample["token"],
                )
            )

        keyframes.sort(key=lambda kf: kf.timestamp_us)
        return enrich_ego_kinematics(keyframes)

    def _build_frames(self, nusc, sample: dict) -> list[IndexedRawFrame]:
        frames = []
        for channel, sample_data_token in sample["data"].items():
            sd = nusc.get("sample_data", sample_data_token)
            modality = _modality_from_channel(channel)
            frames.append(
                IndexedRawFrame(
                    frame_id=f"frame_{sample_data_token}",
                    timestamp_us=int(sd["timestamp"]),
                    channel=channel,
                    modality=modality,
                    role=_role_from_modality(modality),
                    uri=str(Path(self.source_uri) / sd["filename"]),
                    source_sample_id=sample["token"],
                    source_scene_id=sample["scene_token"],
                    ego_pose_ref=sd.get("ego_pose_token"),
                    calibration_ref=sd.get("calibrated_sensor_token"),
                    annotation_refs=tuple(sample.get("anns", [])),
                )
            )
        return frames

    def _build_ego_state(self, nusc, sample: dict) -> EgoState | None:
        # Use CAM_FRONT ego pose as the canonical ego position for the keyframe
        cam_token = sample["data"].get("CAM_FRONT")
        if cam_token is None:
            return None

        sd = nusc.get("sample_data", cam_token)
        pose = nusc.get("ego_pose", sd["ego_pose_token"])

        t = pose["translation"]
        r = pose["rotation"]  # [w, x, y, z]

        return EgoState(
            timestamp_us=sample["timestamp"],
            x=t[0],
            y=t[1],
            z=t[2],
            rotation=(r[0], r[1], r[2], r[3]),
        )

    def _build_nearby_objects(
        self,
        nusc,
        sample: dict,
        ego_state: EgoState | None,
    ) -> list[NearbyObject]:
        objects = []
        for ann_token in sample.get("anns", []):
            ann = nusc.get("sample_annotation", ann_token)
            t = ann["translation"]

            distance_m = (
                math.sqrt(
                    (t[0] - ego_state.x) ** 2
                    + (t[1] - ego_state.y) ** 2
                    + (t[2] - ego_state.z) ** 2
                )
                if ego_state is not None
                else 0.0
            )

            objects.append(
                NearbyObject(
                    category_name=ann["category_name"],
                    x=t[0],
                    y=t[1],
                    z=t[2],
                    distance_m=round(distance_m, 2),
                    annotation_token=ann_token,
                )
            )

        return objects
