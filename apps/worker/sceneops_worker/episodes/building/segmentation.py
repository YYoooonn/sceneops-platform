from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sceneops_core.episodes.schemas import (
    EpisodeSegmentationConfig,
    EpisodeSegmentationStrategy,
    EpisodeSource,
)
from sceneops_core.robots.schemas import MissionRecord


@dataclass(frozen=True)
class EpisodeWindow:
    """A [start_us, end_us) time window that becomes one Episode.

    end_us is None for an "open" window — either an in-progress Mission with
    no ended_at yet, or WHOLE_RUN, which has no fixed upper bound of its own
    and instead relies on there simply being no more source data past it.
    Boundary/segmentation information only — EpisodeBuilder owns turning a
    window into observation/action frames.
    """

    segment_index: int
    start_us: int
    end_us: int | None
    mission: MissionRecord | None = None

    @property
    def mission_id(self) -> str | None:
        return self.mission.mission_id if self.mission is not None else None


def _datetime_to_us(value: datetime) -> int:
    return int(value.timestamp() * 1_000_000)


def _source_time_range(source: EpisodeSource) -> tuple[int, int] | None:
    """Min/max timestamp_us across the frames/robot_states that Episode
    processing actually reads — never Scene-owned RawLog metadata (SceneOps
    V2 Request 12). Missions are boundary signals, not source data, so their
    started_at/ended_at aren't part of this range."""
    timestamps = [f.timestamp_us for f in source.frames] + [
        s.timestamp_us for s in source.robot_states
    ]
    if not timestamps:
        return None
    return min(timestamps), max(timestamps)


class EpisodeSegmenter:
    """Decides where Episodes begin/end within one EpisodeSource.

    Plays the same role ``SceneSegmenter`` plays for Scene (raw inputs ->
    segment boundaries), but deliberately not shared with it (SceneOps V2
    Request 13) — Episode boundaries are either an external semantic signal
    (Mission) or a plain time split, neither of which needs Scene's
    sequence/gap grouping machinery.
    """

    def segment(
        self,
        *,
        source: EpisodeSource,
        config: EpisodeSegmentationConfig,
    ) -> list[EpisodeWindow]:
        if config.strategy == EpisodeSegmentationStrategy.MISSION_BOUNDARY:
            return self._mission_boundary_windows(source)
        if config.strategy == EpisodeSegmentationStrategy.WHOLE_RUN:
            return self._whole_run_windows()
        if config.strategy == EpisodeSegmentationStrategy.FIXED_WINDOW:
            return self._fixed_windows(source, config)
        raise NotImplementedError(
            f"Unsupported segmentation strategy: {config.strategy!r}"
        )

    def _mission_boundary_windows(self, source: EpisodeSource) -> list[EpisodeWindow]:
        # Only started_at is required to use a Mission as a boundary — a
        # Mission with started_at set but any other field unset/odd is still
        # used as-is, matching EpisodeBuilder's pre-Request-13 behavior
        # exactly. There is no separate "malformed Mission" handling to
        # preserve; "no usable dated Missions" has only ever meant
        # started_at is None.
        dated_missions = [m for m in source.missions if m.started_at is not None]
        if not dated_missions:
            # Explicit fallback (SceneOps V2 Request 13): MISSION_BOUNDARY
            # with no usable dated Missions degrades to WHOLE_RUN, matching
            # the pre-Request-13 implicit builder behavior.
            return self._whole_run_windows()

        dated_missions.sort(key=lambda m: m.started_at)
        return [
            EpisodeWindow(
                segment_index=index,
                start_us=_datetime_to_us(mission.started_at),
                end_us=_datetime_to_us(mission.ended_at)
                if mission.ended_at is not None
                else None,
                mission=mission,
            )
            for index, mission in enumerate(dated_missions)
        ]

    def _whole_run_windows(self) -> list[EpisodeWindow]:
        # start_us=0/end_us=None covers every timestamp in the source
        # unconditionally — the actual boundary timestamps recorded on the
        # resulting EpisodeManifest still come from real frame/state data
        # (EpisodeBuilder derives start/end_timestamp_us from what actually
        # fell in-window), so this needs no source-derived number of its own.
        return [EpisodeWindow(segment_index=0, start_us=0, end_us=None, mission=None)]

    def _fixed_windows(
        self,
        source: EpisodeSource,
        config: EpisodeSegmentationConfig,
    ) -> list[EpisodeWindow]:
        time_range = _source_time_range(source)
        if time_range is None:
            # No usable timestamp source (no frames, no robot states) —
            # nothing to split into fixed windows.
            return []
        min_us, max_us = time_range

        duration_us = config.fixed_window_duration_ms * 1000

        windows: list[EpisodeWindow] = []
        window_start = min_us
        index = 0
        while window_start <= max_us:
            windows.append(
                EpisodeWindow(
                    segment_index=index,
                    start_us=window_start,
                    end_us=window_start + duration_us,
                    mission=None,
                )
            )
            window_start += duration_us
            index += 1
        return windows
