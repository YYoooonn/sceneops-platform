from __future__ import annotations

from dataclasses import dataclass

from sceneops_worker.datasets.scene_building.semantic_models import IndexedKeyframe


@dataclass(frozen=True)
class EgoKinematicPredicate:
    """Matches keyframes based on ego vehicle kinematic state.

    Use cases:
    - Deceleration scenes: decel_min_ms2=2.0 → "ego is braking hard"
    - Low-speed urban: speed_max_kmh=30 → good for pedestrian interaction scenarios
    - Highway: speed_min_kmh=80 → lane-change or cut-in interventions
    """

    speed_min_kmh: float | None = None
    speed_max_kmh: float | None = None
    decel_min_ms2: float | None = None  # positive threshold for deceleration magnitude

    def evaluate(self, keyframe: IndexedKeyframe) -> bool:
        if keyframe.ego_state is None:
            return False

        speed_ms = keyframe.ego_state.speed_ms
        if speed_ms is None:
            return False

        speed_kmh = speed_ms * 3.6

        if self.speed_min_kmh is not None and speed_kmh < self.speed_min_kmh:
            return False
        if self.speed_max_kmh is not None and speed_kmh > self.speed_max_kmh:
            return False

        if self.decel_min_ms2 is not None:
            accel = keyframe.ego_state.acceleration_ms2
            if accel is None or -accel < self.decel_min_ms2:
                return False

        return True

    def describe(self) -> str:
        parts = []
        if self.speed_min_kmh is not None:
            parts.append(f"speed>={self.speed_min_kmh}km/h")
        if self.speed_max_kmh is not None:
            parts.append(f"speed<={self.speed_max_kmh}km/h")
        if self.decel_min_ms2 is not None:
            parts.append(f"decel>={self.decel_min_ms2}m/s²")
        return f"EgoKinematic({', '.join(parts)})"
