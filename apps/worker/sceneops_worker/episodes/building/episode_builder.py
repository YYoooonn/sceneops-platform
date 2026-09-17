from __future__ import annotations

from dataclasses import dataclass

from sceneops_core.episodes.schemas import (
    EpisodeActionFrame,
    EpisodeLineage,
    EpisodeManifest,
    EpisodeObservationFrame,
    EpisodeOutcome,
    EpisodeSource,
)
from sceneops_core.observations.schemas import RawSensorFrameManifest
from sceneops_core.robots.schemas import MissionRecord, MissionStatus, RobotStateRecord

# RobotState fields that represent observations vs. actions
# (docs/robot-data-model.md §2). Fields not listed here (e.g. rotation_format)
# are not per-timestep signal and are skipped.
_OBSERVATION_STATE_FIELDS = (
    "position",
    "orientation",
    "velocity",
    "acceleration",
    "battery",
)
_ACTION_STATE_FIELDS = ("steering", "throttle", "brake")

_OUTCOME_BY_MISSION_STATUS: dict[MissionStatus, EpisodeOutcome] = {
    MissionStatus.COMPLETED: EpisodeOutcome.SUCCESS,
    MissionStatus.FAILED: EpisodeOutcome.FAILURE,
    MissionStatus.ABORTED: EpisodeOutcome.FAILURE,
}


@dataclass(frozen=True)
class _EpisodeWindow:
    """A [start_us, end_us) time window that becomes one episode.

    end_us is None for an in-progress mission with no ended_at yet — treated
    as "open", i.e. extends to the end of the raw log.
    """

    mission: MissionRecord | None
    start_us: int
    end_us: int | None


@dataclass(frozen=True)
class EpisodeBuildResult:
    episodes: list[EpisodeManifest]
    episode_count: int
    observation_frame_count: int
    action_frame_count: int


def _datetime_to_us(value) -> int:  # noqa: ANN001 - datetime, kept loose to avoid an import-only-for-typing
    return int(value.timestamp() * 1_000_000)


class EpisodeBuilder:
    """Assembles ``EpisodeManifest``s from one raw log's sensor frames, robot
    state samples, and mission boundaries.

    Plays the same role ``SceneBuilder`` plays for Scene (raw inputs -> domain
    manifest), but segmentation is driven by ``Mission`` start/end boundaries
    rather than a generic gap/anchor scene segmenter — a Mission already *is*
    a task-execution window, which is exactly what an Episode represents. A
    raw log with no missions becomes a single episode spanning the whole log
    (fallback for bags recorded without ``/mission/status``).

    Sensor frames, robot state samples, and mission boundaries all come from
    one ``EpisodeSource`` (built by ``RosbagAdapter.extract_episode_source()``
    — the Episode-domain counterpart to ``build_raw_log()``, see SceneOps V2
    Request 12). Robot state samples classify into observation vs. action
    fields per ``docs/robot-data-model.md`` §2 — position/orientation/
    velocity/acceleration/battery are observations, steering/throttle/brake
    are actions.
    """

    def build(
        self,
        *,
        dataset_id: str,
        dataset_version: str,
        raw_log_id: str,
        robot_id: str,
        robot_run_id: str | None,
        source: EpisodeSource,
        task: str | None = None,
    ) -> EpisodeBuildResult:
        windows = self._resolve_windows(source.missions)

        episodes: list[EpisodeManifest] = []
        observation_frame_count = 0
        action_frame_count = 0
        for index, window in enumerate(windows):
            manifest = self._build_episode(
                dataset_id=dataset_id,
                dataset_version=dataset_version,
                raw_log_id=raw_log_id,
                robot_id=robot_id,
                robot_run_id=robot_run_id,
                episode_index=index,
                window=window,
                frames=source.frames,
                robot_states=source.robot_states,
                task=task,
            )
            if manifest.frame_count == 0:
                continue
            episodes.append(manifest)
            observation_frame_count += len(manifest.observation_frames)
            action_frame_count += len(manifest.action_frames)

        return EpisodeBuildResult(
            episodes=episodes,
            episode_count=len(episodes),
            observation_frame_count=observation_frame_count,
            action_frame_count=action_frame_count,
        )

    def _resolve_windows(self, missions: list[MissionRecord]) -> list[_EpisodeWindow]:
        dated_missions = [m for m in missions if m.started_at is not None]
        if not dated_missions:
            # Fallback: no mission boundaries available — treat the whole raw
            # log as a single episode.
            return [_EpisodeWindow(mission=None, start_us=0, end_us=None)]

        dated_missions.sort(key=lambda m: m.started_at)
        return [
            _EpisodeWindow(
                mission=mission,
                start_us=_datetime_to_us(mission.started_at),
                end_us=_datetime_to_us(mission.ended_at)
                if mission.ended_at is not None
                else None,
            )
            for mission in dated_missions
        ]

    def _build_episode(
        self,
        *,
        dataset_id: str,
        dataset_version: str,
        raw_log_id: str,
        robot_id: str,
        robot_run_id: str | None,
        episode_index: int,
        window: _EpisodeWindow,
        frames: list[RawSensorFrameManifest],
        robot_states: list[RobotStateRecord],
        task: str | None,
    ) -> EpisodeManifest:
        mission = window.mission
        episode_id = (
            f"{raw_log_id}-{mission.mission_id}"
            if mission is not None
            else f"{raw_log_id}-episode{episode_index:04d}"
        )

        observation_frames: list[EpisodeObservationFrame] = []
        for frame in frames:
            if not self._in_window(frame.timestamp_us, window):
                continue
            observation_frames.append(
                EpisodeObservationFrame(
                    timestamp_us=frame.timestamp_us,
                    channel=frame.channel,
                    modality=frame.modality,
                    uri=frame.uri or None,
                    metadata=frame.metadata,
                )
            )

        action_frames: list[EpisodeActionFrame] = []
        for state in robot_states:
            if not self._in_window(state.timestamp_us, window):
                continue
            for field_name in _OBSERVATION_STATE_FIELDS:
                value = getattr(state, field_name)
                if value is None:
                    continue
                values = value if isinstance(value, list) else [float(value)]
                observation_frames.append(
                    EpisodeObservationFrame(
                        timestamp_us=state.timestamp_us,
                        channel=f"state.{field_name}",
                        values=values,
                    )
                )
            for field_name in _ACTION_STATE_FIELDS:
                value = getattr(state, field_name)
                if value is None:
                    continue
                action_frames.append(
                    EpisodeActionFrame(
                        timestamp_us=state.timestamp_us,
                        channel=field_name,
                        value=float(value),
                    )
                )

        observation_frames.sort(key=lambda f: f.timestamp_us)
        action_frames.sort(key=lambda f: f.timestamp_us)

        observation_channels = sorted({f.channel for f in observation_frames})
        action_channels = sorted({f.channel for f in action_frames})

        timestamps = [f.timestamp_us for f in observation_frames] + [
            f.timestamp_us for f in action_frames
        ]
        start_timestamp_us = min(timestamps) if timestamps else None
        end_timestamp_us = max(timestamps) if timestamps else None

        control_frequency_hz = None
        state_sample_count = sum(
            1 for s in robot_states if self._in_window(s.timestamp_us, window)
        )
        if (
            state_sample_count > 1
            and start_timestamp_us is not None
            and end_timestamp_us is not None
            and end_timestamp_us > start_timestamp_us
        ):
            duration_seconds = (end_timestamp_us - start_timestamp_us) / 1_000_000
            control_frequency_hz = state_sample_count / duration_seconds

        outcome = (
            _OUTCOME_BY_MISSION_STATUS.get(mission.status, EpisodeOutcome.UNKNOWN)
            if mission is not None
            else EpisodeOutcome.UNKNOWN
        )

        return EpisodeManifest(
            episode_id=episode_id,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            lineage=EpisodeLineage(
                raw_log_id=raw_log_id,
                robot_id=robot_id,
                robot_run_id=robot_run_id,
                mission_id=mission.mission_id if mission is not None else None,
                source_dataset_id=dataset_id,
                source_dataset_version=dataset_version,
            ),
            task=task,
            outcome=outcome,
            observation_frames=observation_frames,
            action_frames=action_frames,
            observation_channels=observation_channels,
            action_channels=action_channels,
            control_frequency_hz=control_frequency_hz,
            start_timestamp_us=start_timestamp_us,
            end_timestamp_us=end_timestamp_us,
            frame_count=len(observation_frames) + len(action_frames),
        )

    @staticmethod
    def _in_window(timestamp_us: int, window: _EpisodeWindow) -> bool:
        if timestamp_us < window.start_us:
            return False
        if window.end_us is not None and timestamp_us >= window.end_us:
            return False
        return True
