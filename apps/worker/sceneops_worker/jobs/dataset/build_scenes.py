from __future__ import annotations


from sceneops_core.datasets.schemas import (
    DatasetType,
    RawLogFrameIndex,
    RawLogManifest,
    RawSensorFrameManifest,
    SceneSegmentIndex,
    SceneBuildPolicyType,
    TimeRange,
)
from sceneops_core.jobs.schemas import (
    BuildScenesJobParams,
    BuildScenesJobResult,
    JobType,
)
from sceneops_core.ids import generate_raw_log_id
from sceneops_core.common.schemas import JsonDict
from sceneops_worker.datasets.scene_building.builders.dataset_manifest_builder import (
    SceneSegmentDatasetManifestBuilder,
)
from sceneops_worker.datasets.scene_building.indexers.nuscenes import (
    NuscenesRawLogIndexer,
)
from sceneops_worker.datasets.scene_building.models import IndexedRawFrame
from sceneops_worker.datasets.scene_building.predicates.factory import build_predicate
from sceneops_worker.datasets.scene_building.segmenters.fixed_window import (
    FixedWindowSceneSegmenter,
)
from sceneops_worker.datasets.scene_building.segmenters.scenario_mining import (
    ScenarioMiningSegmenter,
)
from sceneops_worker.datasets.scene_building.semantic_indexers.nuscenes import (
    NuscenesSemanticIndexer,
)
from sceneops_worker.jobs.base import JobHandler, JobHandlerRequest
from sceneops_worker.pipelines.context_keys import PipelineContextKey as Ctx


async def _index_and_segment(
    *,
    source_uri: str,
    dataset_version: str,
    params: BuildScenesJobParams,
    raw_log_id: str,
) -> tuple[list[IndexedRawFrame], list]:
    policy_type = params.policy.type

    if policy_type == SceneBuildPolicyType.SCENARIO_MINING:
        if params.policy.mining is None:
            raise ValueError("policy.mining is required for SCENARIO_MINING type")

        semantic_indexer = NuscenesSemanticIndexer(
            source_uri=source_uri,
            version=dataset_version,
        )
        keyframes = await semantic_indexer.index()

        predicate = build_predicate(params.policy.mining.predicate)
        segmenter = ScenarioMiningSegmenter(
            raw_log_id=raw_log_id,
            predicate=predicate,
            pre_event_us=int(params.policy.mining.pre_event_seconds * 1_000_000),
            post_event_us=int(params.policy.mining.post_event_seconds * 1_000_000),
            min_gap_between_anchors_us=int(
                params.policy.mining.min_gap_between_scenes_seconds * 1_000_000
            ),
            policy=params.policy,
        )
        segments = segmenter.segment(keyframes)
        frames = [frame for kf in keyframes for frame in kf.frames]
        return frames, segments

    # Default: FIXED_WINDOW (and future gap_based etc.)
    indexer = NuscenesRawLogIndexer(
        source_uri=source_uri,
        version=dataset_version,
        max_frames=params.max_frames,
    )
    frames = await indexer.index()
    segmenter = FixedWindowSceneSegmenter(
        raw_log_id=raw_log_id,
        policy=params.policy,
    )
    return frames, segmenter.segment(frames)


class BuildScenesJobHandler(JobHandler):
    @property
    def job_type(self) -> JobType:
        return JobType.BUILD_SCENES

    @property
    def params_model(self) -> type[BuildScenesJobParams]:
        return BuildScenesJobParams

    def build_step_params(self, base: JsonDict, context_values: dict) -> JsonDict:
        return base

    def extract_context_updates(self, result: JsonDict) -> dict:
        parsed = BuildScenesJobResult.model_validate(result)
        updates: dict = {
            Ctx.SCENE_COUNT: parsed.scene_count,
            Ctx.SAMPLE_COUNT: parsed.sample_count,
            Ctx.BUILD_SCENES_RAW_LOG_ID: parsed.raw_log_id,
            Ctx.BUILD_SCENES_RAW_LOG_MANIFEST_URI: parsed.raw_log_manifest_uri,
            Ctx.BUILD_SCENES_SCENE_SEGMENTS_URI: parsed.scene_segments_uri,
            Ctx.BUILD_SCENES_SCENE_INDEX_URI: parsed.scene_index_uri,
            Ctx.BUILD_SCENES_FRAME_COUNT: parsed.frame_count,
            Ctx.BUILD_SCENES_CHANNELS: parsed.channels,
        }
        if parsed.dataset_manifest_uri is not None:
            updates[Ctx.DATASET_MANIFEST_URI] = parsed.dataset_manifest_uri
        return updates

    async def run(
        self, request: JobHandlerRequest[BuildScenesJobParams]
    ) -> BuildScenesJobResult:
        # job = request.job
        context = request.context
        params = request.params

        dataset_id = params.dataset_id
        dataset_version = params.dataset_version
        source_uri = params.source_uri

        if source_uri is None:
            raise ValueError("source_uri is required for build_scenes job")

        version_root_uri = context.dataset_artifact_store.dataset_version_root_uri(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
        )
        raw_root_uri = context.dataset_artifact_store.raw_root_uri(version_root_uri)

        raw_log_id = generate_raw_log_id()

        if params.dataset_type != DatasetType.NUSCENES:
            raise ValueError(
                f"Unsupported build_scenes dataset_type: {params.dataset_type}"
            )

        frames, segments = await _index_and_segment(
            source_uri=source_uri,
            dataset_version=dataset_version,
            params=params,
            raw_log_id=raw_log_id,
        )

        if not frames:
            raise ValueError("No raw frames indexed")

        raw_frame_manifests = [
            RawSensorFrameManifest(
                frame_id=frame.frame_id,
                timestamp_us=frame.timestamp_us,
                channel=frame.channel,
                modality=frame.modality,
                role=frame.role,
                uri=frame.uri,
                source_sample_id=frame.source_sample_id,
                source_scene_id=frame.source_scene_id,
                ego_pose_ref=frame.ego_pose_ref,
                calibration_ref=frame.calibration_ref,
                annotation_refs=list(frame.annotation_refs),
            )
            for frame in frames
        ]

        frame_index_uri = context.dataset_artifact_store.raw_frame_index_uri(
            version_root_uri
        )
        raw_log_manifest_uri = context.dataset_artifact_store.raw_log_manifest_uri(
            version_root_uri
        )
        scene_segments_uri = context.dataset_artifact_store.scene_segments_uri(
            version_root_uri
        )

        frame_index = RawLogFrameIndex(
            raw_log_id=raw_log_id,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            frames=raw_frame_manifests,
        )

        raw_log_manifest = RawLogManifest(
            raw_log_id=raw_log_id,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            dataset_type=params.dataset_type,
            source_format=params.source_format,
            root_uri=source_uri,
            time_range=TimeRange(
                start_timestamp_us=min(frame.timestamp_us for frame in frames),
                end_timestamp_us=max(frame.timestamp_us for frame in frames),
            ),
            channels=sorted({frame.channel for frame in frames}),
            frame_count=len(frames),
            frame_index_uri=frame_index_uri,
            metadata={
                "use_existing_dataset_scenes": params.use_existing_dataset_scenes,
            },
        )

        await context.dataset_artifact_store.save_raw_frame_index(
            uri=frame_index_uri,
            frame_index=frame_index,
        )
        await context.dataset_artifact_store.save_raw_log_manifest(
            uri=raw_log_manifest_uri,
            manifest=raw_log_manifest,
        )

        if params.max_scenes is not None:
            segments = segments[: params.max_scenes]

        if not segments:
            raise ValueError("No valid scene segments built from raw log")

        segment_index = SceneSegmentIndex(
            raw_log_id=raw_log_id,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            segments=segments,
        )

        await context.dataset_artifact_store.save_scene_segment_index(
            uri=scene_segments_uri,
            segment_index=segment_index,
        )

        builder = SceneSegmentDatasetManifestBuilder(
            artifact_store=context.dataset_artifact_store,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            dataset_type=params.dataset_type,
            source="raw_log_scene_builder",
        )

        built = builder.build(
            version_root_uri=version_root_uri,
            raw_root_uri=raw_root_uri,
            frames=frames,
            segments=segments,
        )

        for scene in built.scenes:
            await context.dataset_artifact_store.save_scene_manifest(
                uri=context.dataset_artifact_store.scene_manifest_uri(
                    version_root_uri=version_root_uri,
                    scene_id=scene.scene_id,
                ),
                manifest=scene,
            )

        for sample in built.samples:
            await context.dataset_artifact_store.save_sample_manifest(
                uri=context.dataset_artifact_store.sample_manifest_uri(
                    version_root_uri=version_root_uri,
                    sample_id=sample.sample_id,
                ),
                manifest=sample,
            )

        await context.dataset_artifact_store.save_scene_index(
            uri=built.dataset_manifest.uris.scene_index,
            scene_index=built.scene_index,
        )

        dataset_manifest_uri = None
        if params.write_dataset_manifest:
            await context.dataset_artifact_store.save_dataset_manifest(
                uri=built.dataset_manifest.uris.dataset_manifest,
                manifest=built.dataset_manifest,
            )
            dataset_manifest_uri = built.dataset_manifest.uris.dataset_manifest

        return BuildScenesJobResult(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            raw_log_manifest_uri=raw_log_manifest_uri,
            frame_index_uri=frame_index_uri,
            scene_segments_uri=scene_segments_uri,
            scene_index_uri=built.dataset_manifest.uris.scene_index,
            scene_root_uri=built.dataset_manifest.uris.scene_root,
            sample_root_uri=built.dataset_manifest.uris.sample_root,
            dataset_manifest_uri=dataset_manifest_uri,
            raw_log_id=raw_log_id,
            scene_count=len(built.scenes),
            sample_count=len(built.samples),
            frame_count=len(frames),
            channels=sorted({frame.channel for frame in frames}),
            result_summary={
                "source_uri": source_uri,
                "policy": params.policy.to_artifact_dict(),
            },
        )
