"""SceneManifest v2 -- the canonical, source-faithful description of one
Scene (ADR-007 §13.5-§13.8, §14, §27).

A Scene is a spatiotemporal environmental observation unit. Within its
selected boundary the manifest keeps every source observation of every
included source channel, each with its own source timestamp, verbatim
source channel identity, SceneOps-owned payload, calibration and the
source's own coordinate-frame and pose information, plus the unit's source
and producer provenance. Labels and annotations are never part of a Scene:
they are independent, lineage-bearing label sets (ADR-007 §33.2, I-50).

Observations are primary. Synchronized samples, nearest-frame association,
per-frame pose interpolation, downsampling and other workflow choices are
derived transformations and have no place here. A grouping the source
itself defines (e.g. a hardware-triggered capture set) is carried as a
non-lossy index over observations (``groups``), never instead of them.

The manifest describes the unit, not its membership: it holds no
DatasetVersion, scene id, record state or execution context. Identical
source, producer and build configuration therefore produce byte-identical
manifests, whatever DatasetVersion registers them. The registrar derives
the DatasetVersion-scoped ``scene_id`` from (dataset_id, dataset_version,
robot_run_id, unit_key).

Source time is never synchronized or converted: every timestamp is an
integer nanosecond count in exactly one declared clock. An observation's
clock is its channel's ``source_clock``; a timestamped structure that no
channel owns (a pose, a keyframe group) declares its own.

Payloads are referenced by SceneOps artifact identity and integrity
(``artifact_id``, checksum, size, media type), never by storage location:
``artifact_id -> ArtifactRecord -> uri -> ArtifactStore``, so moving
storage roots or backends never changes a manifest or its checksum.

Representation conventions fixed by ``sceneops.scene_manifest/v2``:

    time          integer nanoseconds in a declared source clock
    translation   metres, [x, y, z]
    rotation      unit quaternion, [w, x, y, z]
    transform     maps a point in ``child_frame_id`` into ``parent_frame_id``

Free-form metadata is not part of the contract: a source fact that the
schema cannot express is a contract amendment, not a metadata entry.

All bytes go through :meth:`SceneManifest.to_canonical_bytes` and
:func:`load_canonical_scene_manifest`; the loader rejects bytes that parse
but are not already canonical.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from collections.abc import Callable, Hashable, Iterable, Sequence
from typing import Annotated, Any, Final, Literal, TypeVar

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    ValidationError,
    model_validator,
)

from sceneops_core.artifacts.schemas.payload import PayloadRef
from sceneops_core.common.canonical_json import canonical_json_bytes
from sceneops_core.common.checksums import sha256_checksum
from sceneops_core.common.identifiers import (
    validate_local_id,
    validate_source_clock,
    validate_verbatim_key,
)
from sceneops_core.provenance import (
    ProducerInfo,
    RecordingSegmentSource,
    SourceTimestampNs,
)

from .enums import SceneFrameRole, SceneGroupKind, SceneModality

SCENE_MANIFEST_SCHEMA_V2: Final = "sceneops.scene_manifest/v2"

# A unit quaternion is stored as the source gives it (never renormalized);
# this tolerance only rejects values that are not rotations at all.
QUATERNION_NORM_TOLERANCE: Final = 1e-4


class SceneManifestError(ValueError):
    """Bytes are not a valid canonical SceneManifest."""


class UnsupportedSceneManifestVersionError(SceneManifestError):
    pass


class NonCanonicalSceneManifestError(SceneManifestError):
    """The bytes parse as a valid manifest but are not byte-identical to its
    canonical serialization."""


# --- value types --------------------------------------------------------------


def _finite_number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"expected a number, got {type(value).__name__}")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("NaN and Infinity are not allowed")
    return number


def _unit_quaternion(value: tuple[float, float, float, float]) -> tuple:
    norm = math.sqrt(sum(component * component for component in value))
    if abs(norm - 1.0) > QUATERNION_NORM_TOLERANCE:
        raise ValueError(f"rotation_wxyz must be a unit quaternion, norm={norm}")
    return value


Number = Annotated[float, BeforeValidator(_finite_number)]
Vector3 = tuple[Number, Number, Number]
QuaternionWXYZ = Annotated[
    tuple[Number, Number, Number, Number], AfterValidator(_unit_quaternion)
]
Matrix3x3 = tuple[Vector3, Vector3, Vector3]

LocalId = Annotated[
    StrictStr, AfterValidator(lambda v: validate_local_id(v, field="local id"))
]
VerbatimKey = Annotated[
    StrictStr, AfterValidator(lambda v: validate_verbatim_key(v, field="source key"))
]
SourceClock = Annotated[StrictStr, AfterValidator(validate_source_clock)]


class _ManifestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --- components ---------------------------------------------------------------


class SceneCoordinateFrame(_ManifestModel):
    """A coordinate frame as the source names it, with its canonical role."""

    frame_id: VerbatimKey
    role: SceneFrameRole


class SceneChannel(_ManifestModel):
    """One source channel included in the Scene boundary.

    ``channel`` is the source's own identity (a recording topic such as
    ``/camera/front/image/compressed``), kept verbatim. ``source_clock`` is the
    clock in which every observation of this channel is timestamped. ``modality`` and the
    optional ``sensor_id`` are canonical semantics the producer adds through
    its integration or build configuration. A channel with no observations
    is allowed: it records that the configured channel had none inside the
    boundary instead of hiding the absence.
    """

    channel: VerbatimKey
    modality: SceneModality
    frame_id: VerbatimKey
    source_clock: SourceClock
    sensor_id: VerbatimKey | None = None


class FrameTransform(_ManifestModel):
    parent_frame_id: VerbatimKey
    child_frame_id: VerbatimKey
    translation_m: Vector3
    rotation_wxyz: QuaternionWXYZ

    @model_validator(mode="after")
    def _check_frames(self) -> FrameTransform:
        if self.parent_frame_id == self.child_frame_id:
            raise ValueError("a transform must relate two different frames")
        return self


class SceneCalibration(_ManifestModel):
    """Calibration of one channel's sensor. ``extrinsic`` places the
    channel's frame in its parent (typically ego) frame."""

    calibration_id: LocalId
    channel: VerbatimKey
    extrinsic: FrameTransform
    camera_intrinsic: Matrix3x3 | None = None


class ImageSize(_ManifestModel):
    width_px: StrictInt = Field(ge=1)
    height_px: StrictInt = Field(ge=1)


class SceneObservation(_ManifestModel):
    """One source observation, at its own source time in its channel's
    ``source_clock``.

    ``ego_pose_id`` is set only where the source itself associates a pose
    with this observation; choosing the nearest pose is a derived
    transformation.
    """

    observation_id: LocalId
    channel: VerbatimKey
    timestamp_ns: SourceTimestampNs
    payload: PayloadRef
    calibration_id: LocalId | None = None
    ego_pose_id: LocalId | None = None
    image_size: ImageSize | None = None


class ScenePose(_ManifestModel):
    """A timestamped transform the source provides (e.g. an ego pose)."""

    pose_id: LocalId
    timestamp_ns: SourceTimestampNs
    source_clock: SourceClock
    transform: FrameTransform


class SceneObservationGroup(_ManifestModel):
    """A source-defined grouping of observations, at most one per channel.
    Its own timestamp (the source's reference time for the group) is in
    ``source_clock``; members keep their own channel clocks."""

    group_id: LocalId
    kind: SceneGroupKind
    timestamp_ns: SourceTimestampNs
    source_clock: SourceClock
    observation_ids: list[LocalId] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_members(self) -> SceneObservationGroup:
        if self.observation_ids != sorted(set(self.observation_ids)):
            raise ValueError("observation_ids must be sorted and unique")
        return self


class SceneLineage(_ManifestModel):
    """Full provenance of the unit: the recording segment it was built from
    and the producer that built it. The producer fingerprint must re-derive
    from the manifest's own source revision."""

    source: RecordingSegmentSource
    producer: ProducerInfo

    @model_validator(mode="after")
    def _check_fingerprint(self) -> SceneLineage:
        self.producer.verify(self.source.source_revision())
        return self


# --- manifest -----------------------------------------------------------------


@dataclass(frozen=True)
class SceneTimeWindow:
    """A half-open interval ``[start, end)`` in one source clock."""

    source_clock: str
    start_timestamp_ns: int
    end_timestamp_ns: int


_T = TypeVar("_T")


def _require_sorted_unique(
    items: Sequence[_T],
    *,
    order: Callable[[_T], Any],
    identity: Callable[[_T], Hashable],
    what: str,
) -> None:
    keys = [order(item) for item in items]
    if keys != sorted(keys):
        raise ValueError(f"{what} are not in canonical order")
    identities = [identity(item) for item in items]
    if len(identities) != len(set(identities)):
        raise ValueError(f"{what} contain duplicate ids")


def _require_known(values: Iterable[str | None], known: set[str], what: str) -> None:
    unknown = sorted({value for value in values if value is not None} - known)
    if unknown:
        raise ValueError(f"{what} references unknown ids: {unknown[:10]}")


class SceneManifest(_ManifestModel):
    schema_version: Literal["sceneops.scene_manifest/v2"] = SCENE_MANIFEST_SCHEMA_V2
    lineage: SceneLineage

    coordinate_frames: list[SceneCoordinateFrame] = Field(min_length=1)
    channels: list[SceneChannel] = Field(min_length=1)
    calibrations: list[SceneCalibration] = Field(default_factory=list)
    observations: list[SceneObservation] = Field(min_length=1)
    poses: list[ScenePose] = Field(default_factory=list)
    groups: list[SceneObservationGroup] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_invariants(self) -> SceneManifest:
        self._check_ordering()
        self._check_references()
        self._check_source_window()
        return self

    def _check_ordering(self) -> None:
        _require_sorted_unique(
            self.coordinate_frames,
            order=lambda f: f.frame_id,
            identity=lambda f: f.frame_id,
            what="coordinate_frames",
        )
        _require_sorted_unique(
            self.channels,
            order=lambda c: c.channel,
            identity=lambda c: c.channel,
            what="channels",
        )
        _require_sorted_unique(
            self.calibrations,
            order=lambda c: c.calibration_id,
            identity=lambda c: c.calibration_id,
            what="calibrations",
        )
        _require_sorted_unique(
            self.observations,
            order=lambda o: (o.channel, o.timestamp_ns, o.observation_id),
            identity=lambda o: o.observation_id,
            what="observations",
        )
        _require_sorted_unique(
            self.poses,
            order=lambda p: (p.source_clock, p.timestamp_ns, p.pose_id),
            identity=lambda p: p.pose_id,
            what="poses",
        )
        _require_sorted_unique(
            self.groups,
            order=lambda g: (g.source_clock, g.timestamp_ns, g.group_id),
            identity=lambda g: g.group_id,
            what="groups",
        )

    def _check_references(self) -> None:
        frame_roles = {f.frame_id: f.role for f in self.coordinate_frames}
        frames = set(frame_roles)
        channel_frames = {c.channel: c.frame_id for c in self.channels}

        _require_known((c.frame_id for c in self.channels), frames, "channels")

        calibration_channels: dict[str, str] = {}
        for calibration in self.calibrations:
            channel_frame = channel_frames.get(calibration.channel)
            if channel_frame is None:
                raise ValueError(
                    f"calibration {calibration.calibration_id} references unknown "
                    f"channel {calibration.channel!r}"
                )
            extrinsic = calibration.extrinsic
            if extrinsic.child_frame_id != channel_frame:
                raise ValueError(
                    f"calibration {calibration.calibration_id} must place frame "
                    f"{channel_frame!r} of channel {calibration.channel!r}, got "
                    f"{extrinsic.child_frame_id!r}"
                )
            _require_known([extrinsic.parent_frame_id], frames, "calibration extrinsic")
            calibration_channels[calibration.calibration_id] = calibration.channel

        for pose in self.poses:
            _require_known(
                [pose.transform.parent_frame_id, pose.transform.child_frame_id],
                frames,
                "poses",
            )
        ego_pose_ids = {
            p.pose_id
            for p in self.poses
            if frame_roles[p.transform.child_frame_id] == SceneFrameRole.EGO
        }

        observation_channels: dict[str, str] = {}
        for observation in self.observations:
            if observation.channel not in channel_frames:
                raise ValueError(
                    f"observation {observation.observation_id} references unknown "
                    f"channel {observation.channel!r}"
                )
            if observation.calibration_id is not None:
                calibrated = calibration_channels.get(observation.calibration_id)
                if calibrated != observation.channel:
                    raise ValueError(
                        f"observation {observation.observation_id} references "
                        f"calibration {observation.calibration_id!r} that is not a "
                        f"calibration of channel {observation.channel!r}"
                    )
            if (
                observation.ego_pose_id is not None
                and observation.ego_pose_id not in ego_pose_ids
            ):
                raise ValueError(
                    f"observation {observation.observation_id} references "
                    f"{observation.ego_pose_id!r}, which is not an ego pose"
                )
            observation_channels[observation.observation_id] = observation.channel

        for group in self.groups:
            _require_known(group.observation_ids, set(observation_channels), "groups")
            member_channels = [observation_channels[i] for i in group.observation_ids]
            if len(member_channels) != len(set(member_channels)):
                raise ValueError(
                    f"group {group.group_id} has more than one observation of a channel"
                )

    def _check_source_window(self) -> None:
        """A recording segment's window is a half-open interval in the
        segment's clock. Every timestamp in that clock must lie inside it;
        timestamps in other clocks are not comparable to it and are never
        converted to make them so."""
        source = self.lineage.source
        for clock, timestamp in self.clocked_timestamps():
            if clock == source.source_clock and not source.contains(timestamp):
                raise ValueError(
                    f"timestamp {timestamp} ({clock}) lies outside the segment "
                    f"window [{source.start_timestamp_ns}, "
                    f"{source.end_timestamp_ns})"
                )

    def clocked_timestamps(self) -> Iterable[tuple[str, int]]:
        """Every source timestamp in the manifest with its one clock."""
        channel_clock = {c.channel: c.source_clock for c in self.channels}
        yield from (
            (channel_clock[o.channel], o.timestamp_ns) for o in self.observations
        )
        yield from ((p.source_clock, p.timestamp_ns) for p in self.poses)
        yield from ((g.source_clock, g.timestamp_ns) for g in self.groups)

    # --- serialization ---------------------------------------------------------

    def to_canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json"))

    def checksum(self) -> str:
        return sha256_checksum(self.to_canonical_bytes())

    # --- read helpers ----------------------------------------------------------

    def declared_window(self) -> SceneTimeWindow:
        """The Scene's temporal boundary: its segment window, a half-open
        interval in the segment's clock. Observation extent is never
        promoted to a window."""
        source = self.lineage.source
        return SceneTimeWindow(
            source_clock=source.source_clock,
            start_timestamp_ns=source.start_timestamp_ns,
            end_timestamp_ns=source.end_timestamp_ns,
        )

    def keyframes(self) -> list[SceneObservationGroup]:
        return [g for g in self.groups if g.kind == SceneGroupKind.KEYFRAME]

    def observed_channel_names(self) -> list[str]:
        return sorted({o.channel for o in self.observations})


def load_canonical_scene_manifest(data: bytes) -> SceneManifest:
    """Strictly parse manifest bytes and require them to be canonical:
    ``canonical(parse(data)) == data``."""
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SceneManifestError(f"manifest is not UTF-8 JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise SceneManifestError("manifest must be a JSON object")
    schema_version = payload.get("schema_version")
    if schema_version != SCENE_MANIFEST_SCHEMA_V2:
        raise UnsupportedSceneManifestVersionError(
            f"unsupported SceneManifest schema_version: {schema_version!r}"
        )

    try:
        manifest = SceneManifest.model_validate(payload)
    except ValidationError as exc:
        raise SceneManifestError(f"invalid SceneManifest: {exc}") from exc

    if manifest.to_canonical_bytes() != data:
        raise NonCanonicalSceneManifestError(
            "manifest bytes are not in canonical SceneManifest v1 form"
        )
    return manifest


__all__ = [
    "QUATERNION_NORM_TOLERANCE",
    "SCENE_MANIFEST_SCHEMA_V2",
    "FrameTransform",
    "ImageSize",
    "NonCanonicalSceneManifestError",
    "SceneCalibration",
    "SceneChannel",
    "SceneCoordinateFrame",
    "SceneLineage",
    "SceneManifest",
    "SceneManifestError",
    "SceneObservation",
    "SceneObservationGroup",
    "ScenePose",
    "SceneTimeWindow",
    "UnsupportedSceneManifestVersionError",
    "load_canonical_scene_manifest",
]
