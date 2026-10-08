"""RecordingSceneBuilder: one verified L1 recording -> canonical Scenes
(ADR-007 §29.10, §30).

Pure and database-free. It reads the local, verified copy of a registered
recording (``resolve_recording`` owns acquiring it) through the shared
recording reader, and decides what the recording means as Scenes, as a
function of recording content, producer semantics and the normalized
``RecordingSceneBuildConfig`` only (I-32). It never reads recording metadata
records, the acquisition mode or the robot platform, never renames a topic,
and never samples, associates, interpolates or synchronizes.

Two passes over the file:

    plan_recording_scenes   decode every selected message; interpret calibration,
                            frames and poses; choose each observation's canonical
                            time; segment on the declared clock; build the
                            complete SceneManifest set and the payload plan
                            (artifact id, sha256, size, media type per payload).
                            Nothing is written.
    iter_planned_payloads   re-read the selected messages and yield each planned
                            payload's bytes, verified against the plan.

Everything that cannot be expressed faithfully fails the build loudly: an
unsupported encoding, a selected topic absent from the recording, missing or
changing calibration, distortion the manifest cannot carry, a sensor frame
calibrated against a frame other than the ego frame.

Identity rules (frozen by ``semantics_version`` 1):

    observation_id   <topic slug>-<rank>: rank in the channel's canonical order
                     (canonical timestamp, then MCAP sequence when every message
                     of the channel carries one, then per-channel file order)
    pose_id          pose-<source index>-<rank>, same ordering per pose source
    calibration_id   cal-<topic slug>
    unit_key         recording (whole_recording) | segment-<window index>
    payload artifact payload-<sha256(robot_run_id, topic, channel_index,
                     extraction)[:32]>. ``channel_index`` is the message's
                     occurrence index among the messages of its own topic, in
                     that topic's acquisition order (I-34 acquisition
                     evidence); messages of other topics never shift it, so
                     cross-channel write order is not part of the identity.
                     The id depends on no build configuration (not even the
                     time policy), so a retry or any rebuild of the same
                     RobotRun reuses it and two configurations can never give
                     one id different bytes. It is scoped to the RobotRun:
                     an equivalent recording of another acquisition owns its
                     own payload artifacts (ADR-007 §30.9).
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from sceneops_core.common.ids import robot_run_recording_artifact_id
from sceneops_core.provenance import (
    INT64_MAX,
    ProducerInfo,
    RecordingSegmentSource,
    RecordingSourceRevision,
)
from sceneops_core.robots.clock import MCAP_LOG_TIME_CLOCK
from sceneops_core.scenes.recording_build import (
    MCAP_PUBLISH_TIME_CLOCK,
    ObservationTimePolicy,
    PayloadExtraction,
    PoseSourceConfig,
    RecordingSceneBuildConfig,
    SceneChannelConfig,
    TimeSource,
    WholeRecordingSegmentation,
)
from sceneops_core.scenes.schemas import (
    FrameTransform,
    ImageSize,
    SceneCalibration,
    SceneChannel,
    SceneCoordinateFrame,
    SceneFrameRole,
    SceneLineage,
    SceneManifest,
    SceneModality,
    SceneObservation,
    ScenePose,
)
from sceneops_recording import (
    RecordingMessage,
    Ros2Decoder,
    iter_recording_messages,
    stamp_ns,
)

from sceneops_recording.observations import payloads as _shared
from sceneops_recording.observations.payloads import (
    OBSERVATION_PAYLOAD_ID_SCHEMA_V1,
    PlannedPayload,
    RecordingBuildError,
    RecordingRevision,
    canonical_order as _canonical_order,
    observation_payload_artifact_id,
    ros2_cdr_media_type,
)

RECORDING_SCENE_PRODUCER_ID: Final = "sceneops.recording_scene_builder"
WHOLE_RECORDING_UNIT_KEY: Final = "recording"
# Bump whenever the canonical bytes a build produces change for the same
# recording and configuration: it enters the producer fingerprint, so a
# changed builder is a different producer and replacing registered Scenes
# is explicit. 2: SceneManifest v2 (no embedded annotations).
RECORDING_SCENE_SEMANTICS_VERSION: Final = 2
CAMERA_INFO_SCHEMA: Final = "sensor_msgs/msg/CameraInfo"
TF_MESSAGE_SCHEMA: Final = "tf2_msgs/msg/TFMessage"

# Modalities whose observations cannot be interpreted without knowing where
# their sensor sits in the ego frame.
_EXTRINSIC_REQUIRED: Final = frozenset(
    {SceneModality.CAMERA, SceneModality.LIDAR, SceneModality.RADAR}
)
# CameraInfo values SceneManifest v1 can represent: no distortion, identity
# rectification and P = [K | 0]. Anything else fails rather than being lost.
_PROJECTION_TOLERANCE: Final = 1e-9


class RecordingSceneBuildError(RecordingBuildError):
    """The recording cannot be canonicalized faithfully under this config."""


@dataclass(frozen=True)
class PlannedScene:
    unit_key: str
    manifest: SceneManifest


@dataclass(frozen=True)
class SceneBuildPlan:
    producer: ProducerInfo
    scenes: list[PlannedScene]
    # keyed by (topic, per-channel file index)
    payloads: dict[tuple[str, int], PlannedPayload]
    pose_count: int

    @property
    def observation_count(self) -> int:
        return sum(len(s.manifest.observations) for s in self.scenes)


# --- identifiers and payloads (shared with the Episode builder) ---------------


def topic_slug(topic: str) -> str:
    try:
        return _shared.topic_slug(topic)
    except RecordingBuildError as exc:
        raise RecordingSceneBuildError(str(exc)) from exc


def extract_payload(
    channel: SceneChannelConfig, message: RecordingMessage, decoded: Any
) -> tuple[bytes, str]:
    try:
        return _shared.extract_payload(channel.payload, message, decoded)
    except RecordingBuildError as exc:
        raise RecordingSceneBuildError(str(exc)) from exc


# --- planning -----------------------------------------------------------------


@dataclass
class _Observed:
    topic: str
    timestamp_ns: int
    segment_ns: int
    sequence: int | None
    channel_index: int
    payload: PlannedPayload


@dataclass
class _PoseObserved:
    timestamp_ns: int
    segment_ns: int
    sequence: int | None
    channel_index: int
    transform_index: int
    transform: FrameTransform


@dataclass
class _CameraInfo:
    frame_id: str
    width: int
    height: int
    intrinsic: tuple[tuple[float, float, float], ...]


@dataclass
class _Planner:
    revision: RecordingRevision
    config: RecordingSceneBuildConfig
    decoder: Ros2Decoder = field(default_factory=Ros2Decoder)
    channels: dict[str, SceneChannelConfig] = field(init=False)
    channel_frames: dict[str, str] = field(default_factory=dict)
    observed: dict[str, list[_Observed]] = field(default_factory=dict)
    camera_info: dict[str, _CameraInfo] = field(default_factory=dict)
    static: dict[str, FrameTransform] = field(default_factory=dict)
    poses: dict[int, list[_PoseObserved]] = field(default_factory=dict)
    seen_topics: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.channels = {c.topic: c for c in self.config.channels}

    # -- timing -----------------------------------------------------------

    def _time(
        self, policy: ObservationTimePolicy, message: RecordingMessage, header: Any
    ) -> int:
        if policy.source == TimeSource.LOG_TIME:
            return message.log_time_ns
        if policy.source == TimeSource.PUBLISH_TIME:
            return message.publish_time_ns
        if header is None:
            raise RecordingSceneBuildError(
                f"{message.topic!r} ({message.schema_name}) carries no header stamp "
                "for a header_stamp time policy"
            )
        return stamp_ns(header.stamp)

    def _segment_time(
        self,
        policy: ObservationTimePolicy,
        timestamp_ns: int,
        message: RecordingMessage,
    ) -> int:
        """The message's timestamp on the segmentation clock (Q4)."""
        clock = self.config.segmentation.clock
        if policy.clock == clock:
            return timestamp_ns
        if clock == MCAP_LOG_TIME_CLOCK:
            return message.log_time_ns
        if clock == MCAP_PUBLISH_TIME_CLOCK:
            return message.publish_time_ns
        raise RecordingSceneBuildError(  # excluded by config validation
            f"{message.topic!r} has no timestamp on segmentation clock {clock!r}"
        )

    # -- message handlers ---------------------------------------------------

    def on_message(self, message: RecordingMessage) -> None:
        self.seen_topics.add(message.topic)
        decoded = self.decoder.decode(message)
        if message.topic in self.channels:
            self._on_observation(self.channels[message.topic], message, decoded)
        if message.topic in self.config.calibration.static_transform_topics:
            self._on_static_transforms(message, decoded)
        for index, source in enumerate(self.config.poses):
            if source.topic == message.topic:
                self._on_pose(index, source, message, decoded)
        if message.topic in self._camera_info_topics():
            self._on_camera_info(message, decoded)

    def _camera_info_topics(self) -> set[str]:
        return {
            c.camera_info_topic for c in self.config.channels if c.camera_info_topic
        }

    def _on_observation(
        self, channel: SceneChannelConfig, message: RecordingMessage, decoded: Any
    ) -> None:
        header = getattr(decoded, "header", None)
        frame_id = getattr(header, "frame_id", "") if header is not None else ""
        if not frame_id:
            raise RecordingSceneBuildError(
                f"message {message.channel_index} on {message.topic!r} names no "
                "header frame_id; an observation needs its sensor frame"
            )
        known = self.channel_frames.setdefault(message.topic, frame_id)
        if known != frame_id:
            raise RecordingSceneBuildError(
                f"{message.topic!r} changes frame_id from {known!r} to {frame_id!r}"
            )
        timestamp_ns = self._time(channel.time, message, header)
        data, media_type = extract_payload(channel, message, decoded)
        payload = PlannedPayload(
            artifact_id=observation_payload_artifact_id(
                robot_run_id=self.revision.robot_run_id,
                topic=message.topic,
                channel_index=message.channel_index,
                extraction=channel.payload,
            ),
            topic=message.topic,
            channel_index=message.channel_index,
            extraction=channel.payload,
            checksum="sha256:" + hashlib.sha256(data).hexdigest(),
            size_bytes=len(data),
            media_type=media_type,
        )
        if payload.size_bytes == 0:
            raise RecordingSceneBuildError(
                f"message {message.channel_index} on {message.topic!r} has an empty payload"
            )
        self.observed.setdefault(message.topic, []).append(
            _Observed(
                topic=message.topic,
                timestamp_ns=timestamp_ns,
                segment_ns=self._segment_time(channel.time, timestamp_ns, message),
                sequence=message.sequence,
                channel_index=message.channel_index,
                payload=payload,
            )
        )

    def _on_camera_info(self, message: RecordingMessage, decoded: Any) -> None:
        if message.schema_name != CAMERA_INFO_SCHEMA:
            raise RecordingSceneBuildError(
                f"{message.topic!r} is {message.schema_name!r}, not {CAMERA_INFO_SCHEMA!r}"
            )
        k = [float(v) for v in decoded.k]
        d = [float(v) for v in decoded.d]
        r = [float(v) for v in decoded.r]
        p = [float(v) for v in decoded.p]
        expected_p = [
            k[0],
            k[1],
            k[2],
            0.0,
            k[3],
            k[4],
            k[5],
            0.0,
            k[6],
            k[7],
            k[8],
            0.0,
        ]
        identity = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
        if (
            any(v != 0.0 for v in d)
            or any(abs(a - b) > _PROJECTION_TOLERANCE for a, b in zip(r, identity))
            or any(abs(a - b) > _PROJECTION_TOLERANCE for a, b in zip(p, expected_p))
        ):
            raise RecordingSceneBuildError(
                f"{message.topic!r}: CameraInfo has distortion, rectification or a "
                "projection other than [K|0], which SceneManifest v1 cannot express"
            )
        info = _CameraInfo(
            frame_id=decoded.header.frame_id,
            width=int(decoded.width),
            height=int(decoded.height),
            intrinsic=(tuple(k[0:3]), tuple(k[3:6]), tuple(k[6:9])),
        )
        known = self.camera_info.setdefault(message.topic, info)
        if known != info:
            raise RecordingSceneBuildError(
                f"{message.topic!r}: camera calibration changes within the recording; "
                "v1 holds calibration constant per recording"
            )

    @staticmethod
    def _transform(stamped: Any) -> FrameTransform:
        t = stamped.transform.translation
        q = stamped.transform.rotation
        try:
            return FrameTransform(
                parent_frame_id=stamped.header.frame_id,
                child_frame_id=stamped.child_frame_id,
                translation_m=(float(t.x), float(t.y), float(t.z)),
                # ROS (x, y, z, w) -> the manifest's declared [w, x, y, z].
                rotation_wxyz=(float(q.w), float(q.x), float(q.y), float(q.z)),
            )
        except ValueError as exc:
            raise RecordingSceneBuildError(
                f"transform {stamped.header.frame_id!r} -> {stamped.child_frame_id!r} "
                f"is not representable: {exc}"
            ) from exc

    def _on_static_transforms(self, message: RecordingMessage, decoded: Any) -> None:
        if message.schema_name != TF_MESSAGE_SCHEMA:
            raise RecordingSceneBuildError(
                f"{message.topic!r} is {message.schema_name!r}, not {TF_MESSAGE_SCHEMA!r}"
            )
        for stamped in decoded.transforms:
            transform = self._transform(stamped)
            known = self.static.setdefault(transform.child_frame_id, transform)
            if known != transform:
                raise RecordingSceneBuildError(
                    f"static transform of {transform.child_frame_id!r} changes within "
                    "the recording; v1 holds calibration constant per recording"
                )

    def _on_pose(
        self,
        index: int,
        source: PoseSourceConfig,
        message: RecordingMessage,
        decoded: Any,
    ) -> None:
        if message.schema_name != TF_MESSAGE_SCHEMA:
            raise RecordingSceneBuildError(
                f"pose topic {message.topic!r} is {message.schema_name!r}, not "
                f"{TF_MESSAGE_SCHEMA!r}"
            )
        for transform_index, stamped in enumerate(decoded.transforms):
            if (
                stamped.header.frame_id != source.parent_frame_id
                or stamped.child_frame_id != source.child_frame_id
            ):
                continue
            timestamp_ns = self._time(source.time, message, stamped.header)
            self.poses.setdefault(index, []).append(
                _PoseObserved(
                    timestamp_ns=timestamp_ns,
                    segment_ns=self._segment_time(source.time, timestamp_ns, message),
                    sequence=message.sequence,
                    channel_index=message.channel_index,
                    transform_index=transform_index,
                    transform=self._transform(stamped),
                )
            )

    # -- whole-recording interpretation -------------------------------------------

    def check_presence(self) -> None:
        required = (
            set(self.channels)
            | self._camera_info_topics()
            | {p.topic for p in self.config.poses}
            | set(self.config.calibration.static_transform_topics)
        )
        missing = sorted(required - self.seen_topics)
        if missing:
            raise RecordingSceneBuildError(
                f"configured topics {missing} are not in the recording"
            )

    def frame_roles(self) -> dict[str, SceneFrameRole]:
        frames = self.config.frames
        roles = {frames.ego_frame_id: SceneFrameRole.EGO}
        if frames.world_frame_id is not None:
            roles[frames.world_frame_id] = SceneFrameRole.WORLD
        for topic, frame_id in sorted(self.channel_frames.items()):
            if frame_id in roles and roles[frame_id] != SceneFrameRole.SENSOR:
                raise RecordingSceneBuildError(
                    f"channel {topic!r} observes in frame {frame_id!r}, which is "
                    f"configured as the {roles[frame_id].value} frame"
                )
            roles[frame_id] = SceneFrameRole.SENSOR
        for source in self.config.poses:
            for frame_id in (source.parent_frame_id, source.child_frame_id):
                if frame_id not in roles:
                    raise RecordingSceneBuildError(
                        f"pose frame {frame_id!r} is neither the ego, the world nor a "
                        "channel frame"
                    )
        return roles

    def calibrations(self) -> dict[str, SceneCalibration]:
        ego = self.config.frames.ego_frame_id
        result: dict[str, SceneCalibration] = {}
        for channel in self.config.channels:
            frame_id = self.channel_frames[channel.topic]
            extrinsic = self.static.get(frame_id)
            if extrinsic is None:
                if channel.modality in _EXTRINSIC_REQUIRED:
                    raise RecordingSceneBuildError(
                        f"{channel.modality.value} channel {channel.topic!r} has no static "
                        f"transform for its frame {frame_id!r} on "
                        f"{self.config.calibration.static_transform_topics}"
                    )
                continue
            if extrinsic.parent_frame_id != ego:
                raise RecordingSceneBuildError(
                    f"frame {frame_id!r} of {channel.topic!r} is calibrated against "
                    f"{extrinsic.parent_frame_id!r}, not the ego frame {ego!r}; "
                    "transform chains are not interpreted in v1"
                )
            intrinsic = None
            if channel.camera_info_topic is not None:
                info = self.camera_info[channel.camera_info_topic]
                if info.frame_id != frame_id:
                    raise RecordingSceneBuildError(
                        f"CameraInfo on {channel.camera_info_topic!r} is for frame "
                        f"{info.frame_id!r}, but {channel.topic!r} observes in {frame_id!r}"
                    )
                intrinsic = info.intrinsic
            result[channel.topic] = SceneCalibration(
                calibration_id=f"cal-{topic_slug(channel.topic)}",
                channel=channel.topic,
                extrinsic=extrinsic,
                camera_intrinsic=intrinsic,
            )
        return result


def plan_recording_scenes(
    path: Path, *, revision: RecordingRevision, config: RecordingSceneBuildConfig
) -> SceneBuildPlan:
    """Pass 1: the complete Scene set of the recording scope, without
    writing anything."""
    uses_log_time = config.segmentation.clock == MCAP_LOG_TIME_CLOCK or any(
        p.time.source == TimeSource.LOG_TIME for p in [*config.channels, *config.poses]
    )
    if uses_log_time and revision.recording_clock != MCAP_LOG_TIME_CLOCK:
        raise RecordingSceneBuildError(
            f"the recording clock is {revision.recording_clock!r}; log_time is "
            f"only defined as {MCAP_LOG_TIME_CLOCK!r}"
        )

    planner = _Planner(revision=revision, config=config)
    topics = (
        set(planner.channels)
        | planner._camera_info_topics()
        | {p.topic for p in config.poses}
        | set(config.calibration.static_transform_topics)
    )
    for message in iter_recording_messages(path, topics=topics):
        planner.on_message(message)
    planner.check_presence()

    roles = planner.frame_roles()
    calibrations = planner.calibrations()
    producer = ProducerInfo.create(
        producer_id=RECORDING_SCENE_PRODUCER_ID,
        semantics_version=RECORDING_SCENE_SEMANTICS_VERSION,
        build_config=config.normalized(),
        source=RecordingSourceRevision(
            robot_run_id=revision.robot_run_id,
            recording_checksum=revision.recording_checksum,
        ),
    )

    # Canonical observations, identified by their rank in channel order.
    observations: list[tuple[_Observed, SceneObservation]] = []
    payloads: dict[tuple[str, int], PlannedPayload] = {}
    for topic, items in sorted(planner.observed.items()):
        channel = planner.channels[topic]
        calibration = calibrations.get(topic)
        image_size = None
        if channel.camera_info_topic is not None:
            info = planner.camera_info[channel.camera_info_topic]
            image_size = ImageSize(width_px=info.width, height_px=info.height)
        slug = topic_slug(topic)
        for rank, item in enumerate(_canonical_order(items)):
            observations.append(
                (
                    item,
                    SceneObservation(
                        observation_id=f"{slug}-{rank:08d}",
                        channel=topic,
                        timestamp_ns=item.timestamp_ns,
                        payload=item.payload.ref(),
                        calibration_id=calibration.calibration_id
                        if calibration
                        else None,
                        image_size=image_size,
                    ),
                )
            )
            payloads[(topic, item.channel_index)] = item.payload
    if not observations:
        raise RecordingSceneBuildError(
            "no observation of any configured channel is in the recording; a "
            "recording build that yields no Scene fails (§18.3)"
        )
    if len({o.observation_id for _, o in observations}) != len(observations):
        raise RecordingSceneBuildError("configured topics collide on their id slug")

    poses: list[tuple[_PoseObserved, ScenePose]] = []
    for index, source in enumerate(config.poses):
        for rank, item in enumerate(
            _canonical_order(planner.poses.get(index, []), extra="transform_index")
        ):
            poses.append(
                (
                    item,
                    ScenePose(
                        pose_id=f"pose-{index:02d}-{rank:08d}",
                        timestamp_ns=item.timestamp_ns,
                        source_clock=source.time.clock,
                        transform=item.transform,
                    ),
                )
            )

    # Segmentation (Q4): half-open windows on the one declared clock.
    segmentation = config.segmentation
    if isinstance(segmentation, WholeRecordingSegmentation):
        # One window over every included observation and pose, so a pose
        # recorded before the first observation is not dropped.
        times = [item.segment_ns for item, _ in observations] + [
            item.segment_ns for item, _ in poses
        ]
        whole = (min(times), max(times) + 1)

        def window_index(segment_ns: int) -> int:
            return 0

        def window_bounds(index: int) -> tuple[int, int]:
            return whole

        def window_unit_key(index: int) -> str:
            return WHOLE_RECORDING_UNIT_KEY

    else:
        origin = min(item.segment_ns for item, _ in observations)
        duration = segmentation.duration_ns

        def window_index(segment_ns: int) -> int:
            return (segment_ns - origin) // duration

        def window_bounds(index: int) -> tuple[int, int]:
            start = origin + index * duration
            return start, start + duration

        def window_unit_key(index: int) -> str:
            return f"segment-{index:06d}"

    by_window: dict[int, list[SceneObservation]] = {}
    for item, observation in observations:
        by_window.setdefault(window_index(item.segment_ns), []).append(observation)
    poses_by_window: dict[int, list[ScenePose]] = {}
    for item, pose in poses:
        index = window_index(item.segment_ns)
        if index in by_window:
            poses_by_window.setdefault(index, []).append(pose)

    frames = sorted(
        (SceneCoordinateFrame(frame_id=f, role=r) for f, r in roles.items()),
        key=lambda f: f.frame_id,
    )
    channels = sorted(
        (
            SceneChannel(
                channel=c.topic,
                modality=c.modality,
                frame_id=planner.channel_frames[c.topic],
                source_clock=c.time.clock,
                sensor_id=c.sensor_id,
            )
            for c in config.channels
        ),
        key=lambda c: c.channel,
    )
    calibration_list = sorted(calibrations.values(), key=lambda c: c.calibration_id)

    scenes: list[PlannedScene] = []
    for index in sorted(by_window):
        start, end = window_bounds(index)
        if start < 0 or end > INT64_MAX:
            raise RecordingSceneBuildError(
                f"segment window [{start}, {end}) is outside the int64 time range"
            )
        unit_key = window_unit_key(index)
        source = RecordingSegmentSource(
            robot_run_id=revision.robot_run_id,
            recording_artifact_id=robot_run_recording_artifact_id(
                revision.robot_run_id
            ),
            recording_checksum=revision.recording_checksum,
            source_clock=segmentation.clock,
            start_timestamp_ns=start,
            end_timestamp_ns=end,
            unit_key=unit_key,
        )
        manifest = SceneManifest(
            lineage=SceneLineage(source=source, producer=producer),
            coordinate_frames=frames,
            channels=channels,
            calibrations=calibration_list,
            observations=sorted(
                by_window[index],
                key=lambda o: (o.channel, o.timestamp_ns, o.observation_id),
            ),
            poses=sorted(
                poses_by_window.get(index, []),
                key=lambda p: (p.source_clock, p.timestamp_ns, p.pose_id),
            ),
        )
        scenes.append(PlannedScene(unit_key=unit_key, manifest=manifest))

    return SceneBuildPlan(
        producer=producer,
        scenes=scenes,
        payloads=payloads,
        pose_count=sum(len(s.manifest.poses) for s in scenes),
    )


def iter_planned_payloads(
    path: Path, plan: SceneBuildPlan, config: RecordingSceneBuildConfig
) -> Iterator[tuple[PlannedPayload, bytes]]:
    """Pass 2: each planned payload's bytes, verified against the plan, in
    file order. A recording that changed between passes fails loudly."""
    channels = {c.topic: c for c in config.channels}
    decoder = Ros2Decoder()
    remaining = set(plan.payloads)
    for message in iter_recording_messages(path, topics=channels):
        planned = plan.payloads.get((message.topic, message.channel_index))
        if planned is None:
            continue
        decoded = (
            decoder.decode(message)
            if planned.extraction == PayloadExtraction.COMPRESSED_IMAGE
            else None
        )
        data, media_type = extract_payload(channels[message.topic], message, decoded)
        checksum = "sha256:" + hashlib.sha256(data).hexdigest()
        if (checksum, len(data), media_type) != (
            planned.checksum,
            planned.size_bytes,
            planned.media_type,
        ):
            raise RecordingSceneBuildError(
                f"payload of message {message.channel_index} on {message.topic!r} "
                "differs from the plan"
            )
        remaining.discard((message.topic, message.channel_index))
        yield planned, data
    if remaining:
        raise RecordingSceneBuildError(
            f"{len(remaining)} planned payload(s) were not found on re-read"
        )


__all__ = [
    "OBSERVATION_PAYLOAD_ID_SCHEMA_V1",
    "RECORDING_SCENE_PRODUCER_ID",
    "RECORDING_SCENE_SEMANTICS_VERSION",
    "WHOLE_RECORDING_UNIT_KEY",
    "PlannedPayload",
    "PlannedScene",
    "RecordingRevision",
    "RecordingSceneBuildError",
    "SceneBuildPlan",
    "extract_payload",
    "iter_planned_payloads",
    "observation_payload_artifact_id",
    "plan_recording_scenes",
    "ros2_cdr_media_type",
    "topic_slug",
]
