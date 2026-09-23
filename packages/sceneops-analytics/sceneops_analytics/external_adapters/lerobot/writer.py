"""LeRobotDatasetWriter: the concrete ExternalDatasetWriter (Request 3.1A)
for LeRobot's official dataset API (SceneOps V2 Request 3.3).

::

    ExternalDatasetWriter.initialize()
            -> (no-op -- see below)
    ExternalDatasetWriter.write_episode() x N
            -> first call: derive dataset-level fps from step spacing,
               derive observation/action dims from the first step, then
               LeRobotDataset.create(...)
            -> every call: LeRobotDataset.add_frame() x len(steps),
               then LeRobotDataset.save_episode()
    ExternalDatasetWriter.finalize()
            -> LeRobotDataset.finalize()

Why initialize() is a no-op: lerobot.datasets.lerobot_dataset.
LeRobotDatasetMetadata.create() requires ``fps`` and ``features`` up front
and creates ``root`` with ``mkdir(..., exist_ok=False)`` -- it cannot be
called twice, and there is no "reopen and add features later" path. But
ExternalDatasetAdapter.export() (Request 3.1) calls
``writer.initialize()`` *before* it resolves any Episode's FeatureSchema or
projects a single step (see adapter.py's ``export()``), and
ExternalDatasetWriter.write_episode() (Request 3.1A) only ever receives an
already-dense ExternalEpisode/ExternalStep -- never a FeatureSchema, never
an EpisodeMetadata, never the source SceneOpsDataset. So neither ``fps``
nor per-channel dimensions are available at ``initialize()`` time under the
frozen contract; this is a genuine mismatch between Request 3.1/3.1A's
lifecycle and LeRobot's actual creation API (reported per Request 3.3 §1),
not a limitation specific to this implementation. The fix that stays
inside this writer's own state (nothing upstream changes): defer the real
``LeRobotDataset.create()`` call to the first ``write_episode()``, once
real step data makes fps/dims derivable.

fps is derived, never configured: SceneOps Episodes are fixed-frequency by
construction (sceneops_core.episodes.alignment.timeline builds every
Episode's on-grid timestamps from one constant ``dt_us``), so the first
non-empty Episode's own step spacing already carries the dataset's
frequency -- there is no need (or frozen-contract path) to accept an
explicit fps from the caller. See errors.py for what happens when that
spacing is irregular, non-integer-Hz, or disagrees across Episodes.

Per-frame "timestamp" is deliberately never set explicitly: LeRobotDataset.
add_frame() defaults an omitted "timestamp" to ``frame_index / self.fps``.
Once this writer has validated that an Episode's steps really do sit on a
uniform ``dt_us`` grid (see errors.NonUniformTimelineError), LeRobot's own
frame_index/fps default reproduces the exact same relative time that
manually computing ``(timestamp_us - episode_start_us) / 1e6`` would --
without risking a floating-point mismatch against LeRobot's own internal
fps-based expectations elsewhere in its API.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import numpy as np
from lerobot.datasets.lerobot_dataset import LeRobotDataset

from sceneops_analytics.external_adapters.schemas import ExternalEpisode, ExternalStep
from sceneops_analytics.external_adapters.writer import ExternalDatasetWriter
from sceneops_core.episodes.learning import EpisodeRef

from .errors import (
    EmptyEpisodeUnsupportedError,
    EmptyLeRobotExportError,
    InconsistentFrequencyError,
    LeRobotTaskRequiredError,
    NonIntegerFrequencyError,
    NonUniformTimelineError,
    UndeterminableFrequencyError,
)


def _derive_fps(episode_ref: EpisodeRef, steps: list[ExternalStep]) -> int | None:
    """The dataset-level fps this Episode's own step spacing implies, or
    None if this Episode has too few steps (<2) to derive one from (SceneOps
    V2 Request 3.3 §5). Never resamples -- an irregular or non-integer-Hz
    spacing raises rather than approximating."""
    if len(steps) < 2:
        return None

    gaps = {b.timestamp_us - a.timestamp_us for a, b in zip(steps, steps[1:])}
    if len(gaps) != 1:
        raise NonUniformTimelineError(
            f"{episode_ref!r} step timestamps are not evenly spaced "
            f"(distinct gaps={sorted(gaps)} us) -- LeRobot's per-frame "
            "timestamp is a relative fixed-frequency timeline "
            "(frame_index / fps); v1 requires a uniform grid rather than "
            "resampling an irregular one"
        )
    dt_us = next(iter(gaps))
    if dt_us <= 0 or 1_000_000 % dt_us != 0:
        raise NonIntegerFrequencyError(
            f"{episode_ref!r} step spacing dt_us={dt_us} does not "
            "correspond to an integer Hz frequency (1_000_000 must be "
            "evenly divisible by dt_us) -- LeRobot requires one integer "
            "fps for the whole dataset"
        )
    return 1_000_000 // dt_us


def _build_features(observation_dim: int, action_dim: int) -> dict[str, dict]:
    """SceneOps V2 Request 3.3 §4's v1 numeric mapping: the projected
    observation vector becomes LeRobot's ``observation.state`` feature, the
    projected action vector becomes LeRobot's ``action`` feature -- the
    exact convention lerobot.datasets.utils.hw_to_dataset_features itself
    uses for non-visual (joint-state) features. ``names`` is left None:
    ExternalStep only ever carries an already-flattened dense vector (never
    a FeatureSchema, see this module's docstring), so this writer cannot
    recover which flattened index came from which SceneOps channel --
    that per-channel offset/kind mapping is only ever available from
    ExternalExportReport.feature_schema (Request 3.1 §7), returned to the
    caller once export() completes."""
    features: dict[str, dict] = {}
    if observation_dim > 0:
        features["observation.state"] = {
            "dtype": "float32",
            "shape": (observation_dim,),
            "names": None,
        }
    if action_dim > 0:
        features["action"] = {
            "dtype": "float32",
            "shape": (action_dim,),
            "names": None,
        }
    return features


class LeRobotDatasetWriter(ExternalDatasetWriter):
    """One export's LeRobot write session (SceneOps V2 Request 3.3),
    fulfilling ExternalDatasetWriter's initialize/write_episode x N/finalize
    lifecycle (Request 3.1A) over the real ``lerobot.datasets.
    lerobot_dataset.LeRobotDataset`` API. Construct one per export via
    ``LeRobotDatasetAdapter.open_writer()`` -- never reused across exports.
    """

    def __init__(self, *, repo_id: str, root: str | Path) -> None:
        self._repo_id = repo_id
        self._root = Path(root)
        self._fps: int | None = None
        self._dataset: LeRobotDataset | None = None

    async def initialize(self) -> None:
        # Deliberately a no-op -- see module docstring for why the real
        # LeRobotDataset.create() call is deferred to the first
        # write_episode().
        pass

    async def write_episode(self, episode: ExternalEpisode) -> None:
        if not episode.steps:
            raise EmptyEpisodeUnsupportedError(
                f"{episode.episode_ref!r} has zero steps; LeRobot has no "
                "representation for an empty episode"
            )
        if episode.task is None:
            raise LeRobotTaskRequiredError(
                f"{episode.episode_ref!r} has task=None, but LeRobot's "
                "add_frame() requires a non-null per-frame task string"
            )

        fps = _derive_fps(episode.episode_ref, episode.steps)
        if fps is not None:
            if self._fps is None:
                self._fps = fps
            elif fps != self._fps:
                raise InconsistentFrequencyError(
                    f"{episode.episode_ref!r} resolves to fps={fps}, but "
                    f"an earlier Episode in this export already established "
                    f"fps={self._fps} -- LeRobot requires one dataset-level "
                    "fps across every exported Episode"
                )

        if self._dataset is None:
            if self._fps is None:
                raise UndeterminableFrequencyError(
                    f"{episode.episode_ref!r} is the first Episode written "
                    "to this export and has fewer than 2 steps, so no "
                    "dataset-level fps can be derived from it -- v1 "
                    "requires the first exported Episode to have at least "
                    "2 steps"
                )
            self._dataset = self._create_dataset(episode.steps[0])

        for step in episode.steps:
            frame: dict[str, Any] = {"task": episode.task}
            if step.observation:
                frame["observation.state"] = np.asarray(
                    step.observation, dtype=np.float32
                )
            if step.action:
                frame["action"] = np.asarray(step.action, dtype=np.float32)
            self._dataset.add_frame(frame)

        await asyncio.to_thread(self._dataset.save_episode)

    async def finalize(self) -> None:
        if self._dataset is None:
            raise EmptyLeRobotExportError(
                "no Episode was ever written to this export -- there is no "
                "LeRobot dataset to finalize"
            )

        await asyncio.to_thread(self._dataset.finalize)

    def _create_dataset(self, first_step: ExternalStep) -> LeRobotDataset:
        features = _build_features(len(first_step.observation), len(first_step.action))
        assert self._fps is not None
        return LeRobotDataset.create(
            repo_id=self._repo_id,
            fps=self._fps,
            features=features,
            root=self._root,
            use_videos=False,
        )


__all__ = ["LeRobotDatasetWriter"]
