from __future__ import annotations

import math
from dataclasses import dataclass, field

from sceneops_worker.datasets.scene_building.models import IndexedRawFrame


@dataclass(frozen=True)
class EgoState:
    timestamp_us: int
    x: float
    y: float
    z: float
    rotation: tuple[float, float, float, float]  # [w, x, y, z] global frame
    speed_ms: float | None = None
    acceleration_ms2: float | None = None  # positive = accel, negative = decel


@dataclass(frozen=True)
class NearbyObject:
    category_name: str
    x: float
    y: float
    z: float
    distance_m: float
    annotation_token: str | None = None


@dataclass(frozen=True)
class IndexedKeyframe:
    """A single logical keyframe with all co-occurring sensor frames and semantic state.

    Format-agnostic: populated by SemanticLogIndexer implementations.
    `frames` maps to IndexedRawFrame for downstream artifact building.
    """

    timestamp_us: int
    frames: tuple[IndexedRawFrame, ...]
    ego_state: EgoState | None = None
    nearby_objects: tuple[NearbyObject, ...] = ()
    source_keyframe_id: str | None = None  # format-specific id for traceability
    metadata: dict = field(default_factory=dict)


def compute_ego_speed_ms(a: EgoState, b: EgoState) -> float:
    dt_s = (b.timestamp_us - a.timestamp_us) / 1_000_000.0
    if dt_s <= 0:
        return 0.0
    dx = b.x - a.x
    dy = b.y - a.y
    dz = b.z - a.z
    return math.sqrt(dx * dx + dy * dy + dz * dz) / dt_s


def compute_ego_acceleration_ms2(
    speed_prev_ms: float,
    speed_curr_ms: float,
    dt_us: int,
) -> float:
    dt_s = dt_us / 1_000_000.0
    if dt_s <= 0:
        return 0.0
    return (speed_curr_ms - speed_prev_ms) / dt_s


def enrich_ego_kinematics(
    keyframes: list[IndexedKeyframe],
) -> list[IndexedKeyframe]:
    """Compute speed and acceleration for each keyframe from consecutive ego poses.

    Requires at least 2 keyframes with ego_state. Returns keyframes unchanged if
    insufficient data.
    """
    if len(keyframes) < 2:
        return keyframes

    states = [kf.ego_state for kf in keyframes]
    speeds: list[float | None] = [None] * len(keyframes)
    accels: list[float | None] = [None] * len(keyframes)

    for i in range(1, len(keyframes)):
        if states[i - 1] is not None and states[i] is not None:
            speeds[i] = compute_ego_speed_ms(states[i - 1], states[i])  # type: ignore[arg-type]

    for i in range(1, len(keyframes)):
        if speeds[i - 1] is not None and speeds[i] is not None:
            dt = keyframes[i].timestamp_us - keyframes[i - 1].timestamp_us
            accels[i] = compute_ego_acceleration_ms2(speeds[i - 1], speeds[i], dt)  # type: ignore[arg-type]

    enriched = []
    for i, kf in enumerate(keyframes):
        if kf.ego_state is None or (speeds[i] is None and accels[i] is None):
            enriched.append(kf)
            continue

        new_state = EgoState(
            timestamp_us=kf.ego_state.timestamp_us,
            x=kf.ego_state.x,
            y=kf.ego_state.y,
            z=kf.ego_state.z,
            rotation=kf.ego_state.rotation,
            speed_ms=speeds[i],
            acceleration_ms2=accels[i],
        )
        enriched.append(
            IndexedKeyframe(
                timestamp_us=kf.timestamp_us,
                frames=kf.frames,
                ego_state=new_state,
                nearby_objects=kf.nearby_objects,
                source_keyframe_id=kf.source_keyframe_id,
                metadata=kf.metadata,
            )
        )

    return enriched
