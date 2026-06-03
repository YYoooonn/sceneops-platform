from __future__ import annotations

from sceneops_core.datasets.schemas import SceneBuildPolicy, SceneSegmentManifest
from sceneops_core.ids import generate_segment_id
from sceneops_worker.datasets.scene_building.predicates.base import ScenePredicate
from sceneops_worker.datasets.scene_building.semantic_models import IndexedKeyframe


class ScenarioMiningSegmenter:
    """Selects scene windows from a semantic keyframe sequence using a predicate.

    Algorithm:
      1. Evaluate predicate on every keyframe → collect "anchor" matches.
      2. Deduplicate: suppress matches within min_gap of a previous match.
      3. For each anchor, collect keyframes in [anchor - pre_event, anchor + post_event].
      4. Emit a SceneSegmentManifest per window.

    The resulting segments can be directly consumed by SceneSegmentDatasetManifestBuilder.
    """

    def __init__(
        self,
        *,
        raw_log_id: str,
        predicate: ScenePredicate,
        pre_event_us: int,
        post_event_us: int,
        min_gap_between_anchors_us: int,
        policy: SceneBuildPolicy,
    ) -> None:
        self._raw_log_id = raw_log_id
        self._predicate = predicate
        self._pre_event_us = pre_event_us
        self._post_event_us = post_event_us
        self._min_gap_us = min_gap_between_anchors_us
        self._policy = policy

    def segment(self, keyframes: list[IndexedKeyframe]) -> list[SceneSegmentManifest]:
        if not keyframes:
            return []

        keyframes = sorted(keyframes, key=lambda kf: kf.timestamp_us)

        anchors = self._find_anchors(keyframes)
        segments = []

        for anchor in anchors:
            window = self._collect_window(keyframes, anchor.timestamp_us)
            if not window:
                continue

            frame_ids = [frame.frame_id for kf in window for frame in kf.frames]
            channels = sorted({frame.channel for kf in window for frame in kf.frames})

            segments.append(
                SceneSegmentManifest(
                    segment_id=generate_segment_id(),
                    raw_log_id=self._raw_log_id,
                    start_timestamp_us=window[0].timestamp_us,
                    end_timestamp_us=window[-1].timestamp_us,
                    frame_ids=frame_ids,
                    channels=channels,
                    policy=self._policy,
                    quality_summary=self._quality_summary(window, anchor),
                )
            )

        return segments

    def _find_anchors(self, keyframes: list[IndexedKeyframe]) -> list[IndexedKeyframe]:
        anchors: list[IndexedKeyframe] = []
        last_anchor_ts: int | None = None

        for kf in keyframes:
            if not self._predicate.evaluate(kf):
                continue
            if (
                last_anchor_ts is not None
                and kf.timestamp_us - last_anchor_ts < self._min_gap_us
            ):
                continue
            anchors.append(kf)
            last_anchor_ts = kf.timestamp_us

        return anchors

    def _collect_window(
        self,
        keyframes: list[IndexedKeyframe],
        anchor_ts: int,
    ) -> list[IndexedKeyframe]:
        start = anchor_ts - self._pre_event_us
        end = anchor_ts + self._post_event_us
        return [kf for kf in keyframes if start <= kf.timestamp_us <= end]

    def _quality_summary(
        self,
        window: list[IndexedKeyframe],
        anchor: IndexedKeyframe,
    ) -> dict:
        return {
            "predicate": self._predicate.describe(),
            "anchor_timestamp_us": anchor.timestamp_us,
            "keyframe_count": len(window),
            "anchor_nearby_object_count": len(anchor.nearby_objects),
            "anchor_speed_kmh": (
                round(anchor.ego_state.speed_ms * 3.6, 1)
                if anchor.ego_state and anchor.ego_state.speed_ms is not None
                else None
            ),
        }
