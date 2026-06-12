from __future__ import annotations

from sceneops_core.common.schemas import JsonDict
from sceneops_core.observations.schemas import RawLogManifest, RawSensorFrameManifest
from sceneops_core.scenes.schemas.manifests import (
    SceneManifest,
    SceneSampleManifest,
    SceneSensorFrameManifest,
)
from sceneops_core.scenes.schemas.segments import SceneSegment

from .association import AssociatedSample, FrameAssociator
from .context import SceneBuildContext
from .reports import SampleGroupingReport
from .resolvers import CalibrationResolver, EgoPoseResolver, ImageMetadataResolver
from .sampling import SampleAnchorSelector


class SceneAssembler:
    def __init__(
        self,
        *,
        anchor_selector: SampleAnchorSelector | None = None,
        associator: FrameAssociator | None = None,
        calibration_resolver: CalibrationResolver | None = None,
        ego_pose_resolver: EgoPoseResolver | None = None,
        image_resolver: ImageMetadataResolver | None = None,
    ) -> None:
        self._anchor_selector = anchor_selector or SampleAnchorSelector()
        self._associator = associator or FrameAssociator()
        self._calibration_resolver = calibration_resolver or CalibrationResolver()
        self._ego_pose_resolver = ego_pose_resolver or EgoPoseResolver()
        self._image_resolver = image_resolver or ImageMetadataResolver()

    def build_scene(
        self,
        *,
        segment: SceneSegment,
        context: SceneBuildContext,
        dataset_id: str,
        dataset_version: str,
    ) -> tuple[SceneManifest, SampleGroupingReport]:
        raw_frames = context.frames_for_ids(segment.frame_ids)

        anchors = self._anchor_selector.select(
            frames=raw_frames,
            config=context.sampling,
        )

        associated_samples = self._associator.associate(
            scene_id=segment.segment_id,
            frames=raw_frames,
            anchors=anchors,
            config=context.sampling,
        )

        kept_associated_samples = self._filter_associated_samples(
            associated_samples=associated_samples,
            context=context,
        )

        samples = [
            self._build_sample(
                associated=associated,
                context=context,
            )
            for associated in kept_associated_samples
        ]

        report = SampleGroupingReport.from_associated_samples(
            associated_samples=associated_samples,
            kept_sample_ids={sample.sample_id for sample in samples},
        )

        self._attach_resolution_stats(report=report, samples=samples)

        used_calibration_ids = {
            frame.calibration_id
            for sample in samples
            for frame in sample.sensor_frames
            if frame.calibration_id is not None
        }

        used_ego_pose_ids = {
            frame.ego_pose_id
            for sample in samples
            for frame in sample.sensor_frames
            if frame.ego_pose_id is not None
        }

        calibrated_sensors = [
            context.calibration_by_id[calibration_id]
            for calibration_id in sorted(used_calibration_ids)
            if calibration_id in context.calibration_by_id
        ]

        ego_poses = [
            context.ego_pose_by_id[ego_pose_id]
            for ego_pose_id in sorted(used_ego_pose_ids)
            if ego_pose_id in context.ego_pose_by_id
        ]

        channels = sorted(
            {frame.channel for sample in samples for frame in sample.sensor_frames}
        )

        scene_manifest = SceneManifest(
            scene_id=segment.segment_id,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            calibrated_sensors=calibrated_sensors,
            ego_poses=ego_poses,
            samples=samples,
            sample_count=len(samples),
            frame_count=sum(len(sample.sensor_frames) for sample in samples),
            channels=channels,
            start_timestamp_us=segment.start_timestamp_us,
            end_timestamp_us=segment.end_timestamp_us,
            metadata=self._scene_metadata(
                raw_manifest=context.manifest,
                segment=segment,
                context=context,
                report=report,
                used_calibration_ids=used_calibration_ids,
                used_ego_pose_ids=used_ego_pose_ids,
            ),
        )

        return scene_manifest, report

    @staticmethod
    def _filter_associated_samples(
        *,
        associated_samples: list[AssociatedSample],
        context: SceneBuildContext,
    ) -> list[AssociatedSample]:
        kept: list[AssociatedSample] = []

        for sample in associated_samples:
            if context.sampling.drop_empty_samples and not sample.frames:
                continue

            if (
                context.sampling.drop_samples_missing_required_channels
                and sample.missing_channels
            ):
                continue

            kept.append(sample)

        return kept

    def _build_sample(
        self,
        *,
        associated: AssociatedSample,
        context: SceneBuildContext,
    ) -> SceneSampleManifest:
        sensor_frames = [
            self._build_scene_frame(
                raw_frame=raw_frame,
                sample_id=associated.sample_id,
                context=context,
            )
            for raw_frame in associated.frames
        ]

        return SceneSampleManifest(
            sample_id=associated.sample_id,
            scene_id=associated.scene_id,
            timestamp_us=associated.timestamp_us,
            frame_index=associated.frame_index,
            sensor_frames=sensor_frames,
            metadata={
                "source": "raw_scene_builder",
                "anchor_frame_id": associated.anchor.anchor_frame_id,
                "anchor_channel": associated.anchor.anchor_channel,
                "missing_channels": associated.missing_channels,
            },
        )

    def _build_scene_frame(
        self,
        *,
        raw_frame: RawSensorFrameManifest,
        sample_id: str,
        context: SceneBuildContext,
    ) -> SceneSensorFrameManifest:
        calibration = self._calibration_resolver.resolve(
            frame=raw_frame,
            context=context,
            config=context.sampling,
        )

        ego_pose = self._ego_pose_resolver.resolve(
            timestamp_us=raw_frame.timestamp_us,
            context=context,
            config=context.sampling,
        )

        metadata: JsonDict = {
            **raw_frame.metadata,
            "raw_sequence_id": raw_frame.sequence_id,
            "raw_sensor_id": raw_frame.sensor_id,
        }

        return SceneSensorFrameManifest(
            frame_id=raw_frame.frame_id,
            sample_id=sample_id,
            timestamp_us=raw_frame.timestamp_us,
            channel=raw_frame.channel,
            modality=raw_frame.modality,
            uri=raw_frame.uri,
            calibration_id=calibration.calibration_id if calibration else None,
            ego_pose_id=ego_pose.ego_pose_id if ego_pose else None,
            image=self._image_resolver.resolve(frame=raw_frame),
            metadata=metadata,
        )

    @staticmethod
    def _attach_resolution_stats(
        *,
        report: SampleGroupingReport,
        samples: list[SceneSampleManifest],
    ) -> None:
        frames_without_calibration_count = sum(
            1
            for sample in samples
            for frame in sample.sensor_frames
            if frame.calibration_id is None
        )

        frames_without_ego_pose_count = sum(
            1
            for sample in samples
            for frame in sample.sensor_frames
            if frame.ego_pose_id is None
        )

        samples_with_missing_calibration_count = sum(
            1
            for sample in samples
            if any(frame.calibration_id is None for frame in sample.sensor_frames)
        )

        samples_with_missing_ego_pose_count = sum(
            1
            for sample in samples
            if any(frame.ego_pose_id is None for frame in sample.sensor_frames)
        )

        report.add_resolution_stats(
            samples_with_missing_calibration_count=(
                samples_with_missing_calibration_count
            ),
            samples_with_missing_ego_pose_count=samples_with_missing_ego_pose_count,
            frames_without_calibration_count=frames_without_calibration_count,
            frames_without_ego_pose_count=frames_without_ego_pose_count,
        )

    @staticmethod
    def _scene_metadata(
        *,
        raw_manifest: RawLogManifest,
        segment: SceneSegment,
        context: SceneBuildContext,
        report: SampleGroupingReport,
        used_calibration_ids: set[str],
        used_ego_pose_ids: set[str],
    ) -> JsonDict:
        missing_calibration_ids = sorted(
            calibration_id
            for calibration_id in used_calibration_ids
            if calibration_id not in context.calibration_by_id
        )

        missing_ego_pose_ids = sorted(
            ego_pose_id
            for ego_pose_id in used_ego_pose_ids
            if ego_pose_id not in context.ego_pose_by_id
        )

        return {
            "source": "raw_log",
            "raw_log_id": raw_manifest.raw_log_id,
            "segment_id": segment.segment_id,
            "source_type": str(raw_manifest.source_type or ""),
            "source_format": str(raw_manifest.source_format),
            "segmentation": segment.segmentation.to_db_dict(),
            "sampling": {
                "strategy": str(context.sampling.strategy),
                "anchor_channel": context.sampling.anchor_channel,
                "sample_interval_ms": context.sampling.sample_interval_ms,
                "every_nth_anchor": context.sampling.every_nth_anchor,
                "max_samples": context.sampling.max_samples,
                "min_sample_gap_ms": context.sampling.min_sample_gap_ms,
                "required_channels": list(context.sampling.required_channels),
                "association_strategy": str(context.sampling.association_strategy),
                "association_tolerance_ms": context.sampling.association_tolerance_ms,
                "allow_frame_reuse": context.sampling.allow_frame_reuse,
                "drop_empty_samples": context.sampling.drop_empty_samples,
                "drop_samples_missing_required_channels": (
                    context.sampling.drop_samples_missing_required_channels
                ),
                "ego_pose_strategy": str(context.sampling.ego_pose_strategy),
                "ego_pose_tolerance_ms": context.sampling.ego_pose_tolerance_ms,
            },
            "missing_calibration_count": len(missing_calibration_ids),
            "missing_ego_pose_count": len(missing_ego_pose_ids),
            "build_report": report.to_metadata(),
        }
