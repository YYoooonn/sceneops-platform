from __future__ import annotations

from dataclasses import dataclass

from sceneops_core.observations.schemas import RawLogFrameIndex, RawLogManifest
from sceneops_core.scenes.schemas import (
    SampleGroupingConfig,
    SceneSegment,
    SceneSegmentationConfig,
)
from sceneops_core.scenes.schemas.segments import SceneSegmentIndex
from sceneops_worker.observations.artifacts import ObservationArtifactStore
from sceneops_worker.scenes.artifacts import SceneArtifactStore

from .context import SceneBuildContext
from .reports import SampleGroupingReport
from .assembler import SceneAssembler
from .segmentation import SceneSegmenter


@dataclass
class SceneBuildResult:
    scene_ids: list[str]
    scene_manifest_uris: list[str]
    segment_index_uri: str
    total_samples: int
    total_frames: int
    observation_count: int
    grouping_report: SampleGroupingReport


class SceneBuilder:
    def __init__(
        self,
        *,
        scene_artifact_store: SceneArtifactStore,
        observation_artifact_store: ObservationArtifactStore,
        segmenter: SceneSegmenter | None = None,
        assembler: SceneAssembler | None = None,
    ) -> None:
        self._scene_store = scene_artifact_store
        self._obs_store = observation_artifact_store
        self._segmenter = segmenter or SceneSegmenter()
        self._assembler = assembler or SceneAssembler()

    async def build(
        self,
        *,
        manifest: RawLogManifest,
        frame_index: RawLogFrameIndex,
        dataset_id: str,
        dataset_version: str,
        version_root_uri: str,
        segmentation: SceneSegmentationConfig,
        sampling: SampleGroupingConfig,
        max_built_scenes: int | None = None,
    ) -> SceneBuildResult:
        context = SceneBuildContext.from_frame_index(
            manifest=manifest,
            frame_index=frame_index,
            sampling=sampling,
        )

        all_segments = self._segmenter.segment(
            frames=frame_index.frames,
            config=segmentation,
            raw_log_id=manifest.raw_log_id,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
        )

        scene_ids: list[str] = []
        scene_manifest_uris: list[str] = []
        total_samples = 0
        total_frames = 0
        emitted_segments: list[SceneSegment] = []
        accumulated_report = SampleGroupingReport()

        for segment in all_segments:
            scene_manifest, report = self._assembler.build_scene(
                segment=segment,
                context=context,
                dataset_id=dataset_id,
                dataset_version=dataset_version,
            )

            accumulated_report.merge(report)
            # INFO drop scene if no sample count
            if scene_manifest.sample_count == 0:
                continue

            emitted_segments.append(segment)

            uri = await self._scene_store.write_scene_manifest(
                dataset_id=dataset_id,
                dataset_version=dataset_version,
                scene_id=scene_manifest.scene_id,
                manifest=scene_manifest,
            )

            scene_ids.append(scene_manifest.scene_id)
            scene_manifest_uris.append(uri)
            total_samples += scene_manifest.sample_count
            total_frames += scene_manifest.frame_count

            if max_built_scenes and len(emitted_segments) >= max_built_scenes:
                break

        segment_index = SceneSegmentIndex(
            raw_log_id=manifest.raw_log_id,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            segments=emitted_segments,
        )

        segment_index_uri = self._obs_store.scene_segments_uri(version_root_uri)

        await self._obs_store.save_scene_segment_index(
            uri=segment_index_uri,
            segment_index=segment_index,
        )

        return SceneBuildResult(
            scene_ids=scene_ids,
            scene_manifest_uris=scene_manifest_uris,
            segment_index_uri=segment_index_uri,
            total_samples=total_samples,
            total_frames=total_frames,
            observation_count=len(frame_index.frames),
            grouping_report=accumulated_report,
        )
