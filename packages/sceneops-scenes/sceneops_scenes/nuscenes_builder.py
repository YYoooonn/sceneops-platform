"""nuScenes Scene builder: one registered nuScenes v1.0 SourceContent ->
canonical Scenes (ADR-015 §6.2, §6.3, §6.5–§6.7; M3.0 D2, D3, D6, D7).

Pure and database-free. It reads the content only through the native reader
(``sceneops_sources.nuscenes``), which reads only verified content files, and
decides what the content means as Scenes as a function of the content bytes,
producer semantics and the normalized ``NuScenesSceneBuildConfig`` (MS-4). It
never reads a SourceSnapshot or its descriptor, writes nothing and plans no
payload: every reading stays the preserved file it is. CAN, maps and the
annotation tables are never read.

Canonical rules (frozen by ``semantics_version`` 2):

    unit           one Scene per native scene; unit_key = scene.token
                   (scene.name is display data and enters nothing)
    readings       every sample_data whose sample belongs to the scene, key frames
                   and sweeps, of the selected channels
    clock          nuscenes.timestamp, origin unix_epoch, resolution 1 000 ns;
                   t_ns = timestamp_us · 1 000, exact, for readings, ego poses and
                   samples alike (one clock: every ego_pose is at its reading's
                   timestamp in the source)
    window         [first, last + 1 ns) over every reading the source assigns to
                   the scene's samples, of every channel, selected or not: the
                   boundary is the source's, not the channel selection's
    channels       channel = sensor.channel, verbatim; modality = sensor.modality
                   (camera | lidar | radar, else the build fails); frame = the
                   channel name; a selected channel without reading is kept
    frames         "global" (world: the frame ego poses are given in), "ego" (ego:
                   the vehicle frame calibrations are given in), one sensor frame
                   per selected channel
    calibrations   one per calibrated_sensor a reading references:
                   cal-<calibrated_sensor.token>, ego -> channel frame, translation
                   and rotation [w, x, y, z] verbatim; a camera's 3x3
                   camera_intrinsic is required, a lidar or radar has none
    ego poses      one per reading's ego_pose: pose-<ego_pose.token>, global -> ego,
                   at ego_pose.timestamp, linked from the reading as ego_pose_id
    observations   <channel>-<rank, 8 digits>, rank in (timestamp, token) order of
                   the channel; payload = PayloadRef of the reading's preserved
                   file in place (sources Logical URI, the inventory's sha256 and
                   size); media type from modality, file suffix and fileformat
                   (D3); a camera's image size from width /
                   height; calibration_id and ego_pose_id from the reading's own
                   tokens; native_ref = sample_data.token
    keyframes      one group per sample: keyframe-<rank, 6 digits> in (timestamp,
                   token) order, at sample.timestamp, holding the key-frame reading
                   of each selected channel (at most one per channel);
                   native_ref = sample.token; a sample with no key frame on a
                   selected channel fails the build
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from sceneops_core.artifacts.schemas import PayloadRef
from sceneops_core.common.identifiers import validate_local_id
from sceneops_core.provenance import (
    ClockDomain,
    ClockOrigin,
    PinnedContent,
    ProducerInfo,
    SourceTimeUnit,
    promote_to_ns,
)
from sceneops_core.scenes.nuscenes_build import NuScenesSceneBuildConfig
from sceneops_core.scenes.schemas import (
    FrameTransform,
    ImageSize,
    SceneCalibration,
    SceneChannel,
    SceneCoordinateFrame,
    SceneFrameRole,
    SceneGroupKind,
    SceneLineage,
    SceneManifest,
    SceneModality,
    SceneObservation,
    SceneObservationGroup,
    ScenePose,
)
from sceneops_sources import ContentFileError, ContentFiles, content_file_payload_ref
from sceneops_sources.nuscenes import (
    NuScenesCalibratedSensor,
    NuScenesContent,
    NuScenesEgoPose,
    NuScenesFormatError,
    NuScenesSampleData,
    NuScenesScene,
    read_nuscenes_content,
)

from .plan import PlannedScene, SceneBuildPlan

NUSCENES_SCENE_PRODUCER_ID: Final = "sceneops.nuscenes_scene_builder"
# Bump whenever the canonical bytes a build produces change for the same
# content and configuration (it enters the producer fingerprint).
# 2: SceneManifest v5 (a reading is a PayloadRef by Logical URI).
NUSCENES_SCENE_SEMANTICS_VERSION: Final = 2
NUSCENES_CLOCK: Final = "nuscenes.timestamp"
NUSCENES_CLOCK_DOMAIN: Final = ClockDomain(
    clock=NUSCENES_CLOCK, origin=ClockOrigin.UNIX_EPOCH, resolution_ns=1_000
)
WORLD_FRAME: Final = "global"
EGO_FRAME: Final = "ego"

# D3: the media type of a preserved reading, from the channel's modality and
# the file's own evidence (suffix and the table's fileformat, which is "pcd"
# for lidar and radar alike).
_MEDIA_TYPES: Final = {
    SceneModality.CAMERA: (".jpg", "jpg", "image/jpeg"),
    SceneModality.LIDAR: (".pcd.bin", "pcd", "application/x.nuscenes.lidar-pcd-bin"),
    SceneModality.RADAR: (".pcd", "pcd", "application/x.nuscenes.radar-pcd"),
}


class NuScenesSceneBuildError(ValueError):
    """The content cannot be canonicalized faithfully as Scenes under this
    configuration."""


def _ns(timestamp_us: int) -> int:
    return promote_to_ns(timestamp_us, SourceTimeUnit.MICROSECONDS)


def _modality(channel: str, value: str) -> SceneModality:
    try:
        modality = SceneModality(value)
    except ValueError:
        modality = SceneModality.OTHER
    if modality not in _MEDIA_TYPES:
        raise NuScenesSceneBuildError(
            f"channel {channel!r} has modality {value!r}; only camera, lidar and "
            "radar readings are supported"
        )
    return modality


def _transform(
    parent: str, child: str, translation: tuple, rotation: tuple, where: str
) -> FrameTransform:
    try:
        return FrameTransform(
            parent_frame_id=parent,
            child_frame_id=child,
            translation_m=translation,
            # nuScenes stores rotations as [w, x, y, z], the manifest's order.
            rotation_wxyz=rotation,
        )
    except ValueError as exc:
        raise NuScenesSceneBuildError(f"{where} is not representable: {exc}") from exc


class _Channels:
    """The selected channels, with what the source says about each."""

    def __init__(self, content: NuScenesContent, config: NuScenesSceneBuildConfig):
        by_channel = {s.channel: s for s in content.sensors.values()}
        if len(by_channel) != len(content.sensors):
            raise NuScenesSceneBuildError("two sensors share one channel name")
        unknown = sorted(set(config.channels) - set(by_channel))
        if unknown:
            raise NuScenesSceneBuildError(
                f"channels {unknown} are not sensor channels of the dataset"
            )
        for channel in config.channels:
            if channel in (WORLD_FRAME, EGO_FRAME):
                raise NuScenesSceneBuildError(
                    f"channel {channel!r} collides with the {channel!r} frame"
                )
            try:
                validate_local_id(channel, field="channel")
            except ValueError as exc:
                raise NuScenesSceneBuildError(str(exc)) from exc
        self.modality = {
            c: _modality(c, by_channel[c].modality) for c in sorted(config.channels)
        }

    def manifest_channels(self) -> list[SceneChannel]:
        return [
            SceneChannel(
                channel=channel,
                modality=modality,
                frame_id=channel,
                source_clock=NUSCENES_CLOCK,
            )
            for channel, modality in self.modality.items()
        ]

    def frames(self) -> list[SceneCoordinateFrame]:
        frames = [
            SceneCoordinateFrame(frame_id=WORLD_FRAME, role=SceneFrameRole.WORLD),
            SceneCoordinateFrame(frame_id=EGO_FRAME, role=SceneFrameRole.EGO),
            *(
                SceneCoordinateFrame(frame_id=c, role=SceneFrameRole.SENSOR)
                for c in self.modality
            ),
        ]
        return sorted(frames, key=lambda f: f.frame_id)


def _select_scenes(
    content: NuScenesContent,
    unit_keys: Sequence[str] | None,
    pinned: PinnedContent,
) -> list[NuScenesScene]:
    by_key = {s.token: s for s in content.scenes}
    if unit_keys is None:
        selected = sorted(content.scenes, key=lambda s: s.token)
    else:
        unknown = sorted(set(unit_keys) - set(by_key))
        if unknown:
            raise NuScenesSceneBuildError(
                f"unit_keys {unknown} name no scene of {pinned.content_id}"
            )
        selected = [by_key[k] for k in sorted(set(unit_keys))]
    if not selected:
        raise NuScenesSceneBuildError(
            "the content holds no scene; a build that yields no Scene fails"
        )
    return selected


def _calibration(
    calibrated: NuScenesCalibratedSensor, channel: str, modality: SceneModality
) -> SceneCalibration:
    where = f"calibrated_sensor {calibrated.token} of {channel}"
    if modality == SceneModality.CAMERA:
        if not calibrated.camera_intrinsic:
            raise NuScenesSceneBuildError(f"{where} has no camera_intrinsic")
        intrinsic = calibrated.camera_intrinsic
    else:
        if calibrated.camera_intrinsic:
            raise NuScenesSceneBuildError(
                f"{where} carries a camera_intrinsic for a {modality.value} sensor"
            )
        intrinsic = None
    return SceneCalibration(
        calibration_id=f"cal-{calibrated.token}",
        channel=channel,
        extrinsic=_transform(
            EGO_FRAME, channel, calibrated.translation, calibrated.rotation, where
        ),
        camera_intrinsic=intrinsic,
    )


def _pose(ego_pose: NuScenesEgoPose) -> ScenePose:
    return ScenePose(
        pose_id=f"pose-{ego_pose.token}",
        timestamp_ns=_ns(ego_pose.timestamp),
        source_clock=NUSCENES_CLOCK,
        transform=_transform(
            WORLD_FRAME,
            EGO_FRAME,
            ego_pose.translation,
            ego_pose.rotation,
            f"ego_pose {ego_pose.token}",
        ),
    )


def _payload(
    reading: NuScenesSampleData,
    modality: SceneModality,
    pinned: PinnedContent,
    entries: dict,
) -> PayloadRef:
    suffix, fileformat, media_type = _MEDIA_TYPES[modality]
    if not reading.filename.endswith(suffix) or reading.fileformat != fileformat:
        raise NuScenesSceneBuildError(
            f"sample_data {reading.token}: a {modality.value} reading is stored as "
            f"{reading.filename!r} (fileformat {reading.fileformat!r}); the evidence "
            f"for {media_type} is a {suffix} file of fileformat {fileformat!r}"
        )
    return content_file_payload_ref(
        pinned.content_id, entries[reading.filename], media_type=media_type
    )


def _image_size(
    reading: NuScenesSampleData, modality: SceneModality
) -> ImageSize | None:
    if modality == SceneModality.CAMERA:
        if reading.width < 1 or reading.height < 1:
            raise NuScenesSceneBuildError(
                f"camera sample_data {reading.token} has no image size"
            )
        return ImageSize(width_px=reading.width, height_px=reading.height)
    if (reading.width, reading.height) != (0, 0):
        raise NuScenesSceneBuildError(
            f"{modality.value} sample_data {reading.token} has an image size"
        )
    return None


def _plan_scene(
    *,
    content: NuScenesContent,
    pinned: PinnedContent,
    producer: ProducerInfo,
    channels: _Channels,
    scene: NuScenesScene,
) -> PlannedScene:
    where = f"scene {scene.token} ({scene.name})"
    try:
        samples = content.scene_samples(scene)
        all_readings = content.scene_readings(scene)
    except NuScenesFormatError as exc:
        raise NuScenesSceneBuildError(str(exc)) from exc
    if not all_readings:
        raise NuScenesSceneBuildError(f"{where} has no sensor reading")
    window = (
        _ns(min(r.timestamp for r in all_readings)),
        _ns(max(r.timestamp for r in all_readings)) + 1,
    )
    readings = [
        (r, content.sensor_of(r).channel)
        for r in all_readings
        if content.sensor_of(r).channel in channels.modality
    ]
    entries = {entry.path: entry for entry in pinned.inventory.files}
    missing = sorted({r.filename for r, _ in readings} - set(entries))
    if missing:
        raise NuScenesSceneBuildError(
            f"{where} references {len(missing)} file(s) that are not files of "
            f"{pinned.content_id}, e.g. {missing[:3]}; a selected scene's files must "
            "be preserved"
        )

    calibrations: dict[str, SceneCalibration] = {}
    poses: dict[str, ScenePose] = {}
    observations: list[SceneObservation] = []
    observation_of: dict[str, str] = {}
    by_channel: dict[str, list[NuScenesSampleData]] = {}
    for reading, channel in readings:
        by_channel.setdefault(channel, []).append(reading)
    for channel, items in sorted(by_channel.items()):
        modality = channels.modality[channel]
        for rank, reading in enumerate(
            sorted(items, key=lambda r: (r.timestamp, r.token))
        ):
            calibrated = content.calibrated_sensors[reading.calibrated_sensor_token]
            calibration = calibrations.get(calibrated.token)
            if calibration is None:
                calibration = _calibration(calibrated, channel, modality)
                calibrations[calibrated.token] = calibration
            ego_pose = content.ego_poses[reading.ego_pose_token]
            pose = poses.setdefault(ego_pose.token, _pose(ego_pose))
            observation = SceneObservation(
                observation_id=f"{channel}-{rank:08d}",
                channel=channel,
                timestamp_ns=_ns(reading.timestamp),
                payload=_payload(reading, modality, pinned, entries),
                calibration_id=calibration.calibration_id,
                ego_pose_id=pose.pose_id,
                image_size=_image_size(reading, modality),
                native_ref=reading.token,
            )
            observations.append(observation)
            observation_of[reading.token] = observation.observation_id

    groups: list[SceneObservationGroup] = []
    key_frames: dict[str, list[tuple[str, str]]] = {}
    for reading, channel in readings:
        if reading.is_key_frame:
            key_frames.setdefault(reading.sample_token, []).append(
                (channel, observation_of[reading.token])
            )
    for rank, sample in enumerate(
        sorted(samples, key=lambda s: (s.timestamp, s.token))
    ):
        members = key_frames.get(sample.token, [])
        member_channels = [c for c, _ in members]
        if len(member_channels) != len(set(member_channels)):
            raise NuScenesSceneBuildError(
                f"{where}: sample {sample.token} has more than one key frame on a "
                "channel"
            )
        if not members:
            raise NuScenesSceneBuildError(
                f"{where}: sample {sample.token} has no key frame on the selected "
                "channels"
            )
        groups.append(
            SceneObservationGroup(
                group_id=f"keyframe-{rank:06d}",
                kind=SceneGroupKind.KEYFRAME,
                timestamp_ns=_ns(sample.timestamp),
                source_clock=NUSCENES_CLOCK,
                observation_ids=sorted(o for _, o in members),
                native_ref=sample.token,
            )
        )

    manifest = SceneManifest(
        lineage=SceneLineage(
            source=pinned.unit_source(
                source_clock=NUSCENES_CLOCK,
                start_timestamp_ns=window[0],
                end_timestamp_ns=window[1],
                unit_key=scene.token,
            ),
            producer=producer,
        ),
        clock_domains=[NUSCENES_CLOCK_DOMAIN],
        coordinate_frames=channels.frames(),
        channels=channels.manifest_channels(),
        calibrations=sorted(calibrations.values(), key=lambda c: c.calibration_id),
        observations=sorted(
            observations, key=lambda o: (o.channel, o.timestamp_ns, o.observation_id)
        ),
        poses=sorted(
            poses.values(), key=lambda p: (p.source_clock, p.timestamp_ns, p.pose_id)
        ),
        groups=groups,
    )
    return PlannedScene(unit_key=scene.token, manifest=manifest)


def plan_nuscenes_scenes(
    *,
    pinned: PinnedContent,
    files: ContentFiles,
    config: NuScenesSceneBuildConfig,
    unit_keys: Sequence[str] | None = None,
) -> SceneBuildPlan:
    """The Scenes of ``unit_keys`` (default: every scene of the content),
    without writing anything. A manifest depends only on its own scene, so
    building a subset yields the same revisions as building everything."""
    if files.inventory != pinned.inventory:
        raise NuScenesSceneBuildError(
            "the content files are not the pinned content's inventory"
        )
    try:
        content = read_nuscenes_content(files)
    except (NuScenesFormatError, ContentFileError) as exc:
        raise NuScenesSceneBuildError(str(exc)) from exc
    channels = _Channels(content, config)
    scenes = _select_scenes(content, unit_keys, pinned)
    producer = ProducerInfo.create(
        producer_id=NUSCENES_SCENE_PRODUCER_ID,
        semantics_version=NUSCENES_SCENE_SEMANTICS_VERSION,
        build_config=config.normalized(),
        source=pinned.source_revision,
    )
    planned = []
    for scene in scenes:
        try:
            planned.append(
                _plan_scene(
                    content=content,
                    pinned=pinned,
                    producer=producer,
                    channels=channels,
                    scene=scene,
                )
            )
        except ValueError as exc:
            if isinstance(exc, NuScenesSceneBuildError):
                raise
            # A manifest invariant the source violates (e.g. a pose outside
            # the scene window).
            raise NuScenesSceneBuildError(
                f"scene {scene.token} ({scene.name}) is not a valid Scene: {exc}"
            ) from exc
    return SceneBuildPlan(
        producer=producer,
        scenes=planned,
        payloads={},
        pose_count=sum(len(s.manifest.poses) for s in planned),
    )


__all__ = [
    "EGO_FRAME",
    "NUSCENES_CLOCK",
    "NUSCENES_SCENE_PRODUCER_ID",
    "NUSCENES_SCENE_SEMANTICS_VERSION",
    "NuScenesSceneBuildError",
    "WORLD_FRAME",
    "plan_nuscenes_scenes",
]
