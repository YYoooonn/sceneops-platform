from __future__ import annotations

from sceneops_analytics.external_adapters.errors import ExternalAdapterError


class LeRobotAdapterError(ExternalAdapterError):
    """Base class for LeRobotDatasetAdapter/LeRobotDatasetWriter-specific
    errors (SceneOps V2 Request 3.3) -- distinct from the format-independent
    ExternalAdapterError hierarchy (Request 3.1), which this module reuses
    unchanged for anything the shared adapter contract already checks."""


class EmptyEpisodeUnsupportedError(LeRobotAdapterError):
    """A SceneOps Episode with zero steps was handed to
    LeRobotDatasetWriter.write_episode(). LeRobot has no native
    representation for a zero-frame episode -- lerobot.datasets.utils.
    validate_episode_buffer raises if `episode_buffer["size"] == 0`, so
    there is no official API path that would let this write silently
    succeed. Reported explicitly rather than skipped, since skipping would
    silently break ExternalExportReport.exported_episode_count's already-
    validated invariant (validate_episode_count, Request 3.1 §8)."""


class LeRobotTaskRequiredError(LeRobotAdapterError):
    """An Episode's ``task`` is None. LeRobot's per-frame ``add_frame``
    contract requires a non-null task string (lerobot.datasets.utils.
    validate_frame) -- there is no LeRobot-native "no task" representation
    to fall back to, so this is rejected rather than substituted with an
    invented placeholder string."""


class NonUniformTimelineError(LeRobotAdapterError):
    """An Episode's step timestamps are not evenly spaced. SceneOps V2
    Request 3.3 §5 requires a fixed-frequency-compatible Episode timeline
    for LeRobot export (LeRobot's own per-frame timestamp is derived from
    frame_index/fps, i.e. is structurally a relative fixed-frequency
    timeline) -- an irregular timeline is rejected rather than resampled."""


class NonIntegerFrequencyError(LeRobotAdapterError):
    """An Episode's step spacing does not correspond to an integer Hz
    frequency (SceneOps V2 Request 3.3 §5). LeRobot's
    LeRobotDatasetMetadata.create(fps: int, ...) requires one integer
    frames-per-second value for the whole dataset; a non-integer-Hz
    spacing is rejected rather than rounded."""


class InconsistentFrequencyError(LeRobotAdapterError):
    """Two Episodes in the same export resolve to different fixed
    frequencies (SceneOps V2 Request 3.3 §5). LeRobot requires exactly one
    dataset-level fps across every episode it writes -- resampling either
    episode to reconcile them would silently alter SceneOps temporal
    semantics, so the export is rejected instead."""


class UndeterminableFrequencyError(LeRobotAdapterError):
    """The first non-empty Episode this writer would create the underlying
    LeRobot dataset from has fewer than 2 steps, so no step-to-step spacing
    exists to derive a dataset-level fps from (SceneOps V2 Request 3.3 §5).
    LeRobotDatasetMetadata.create() requires fps upfront and cannot be
    called twice, so this writer must know fps before creating anything --
    v1 does not guess or default a frequency in this case."""


class EmptyLeRobotExportError(LeRobotAdapterError):
    """finalize() was reached without ever writing a non-empty Episode, so
    the underlying LeRobotDataset was never created (LeRobot's fps/features
    are only knowable from real step data, see UndeterminableFrequencyError)
    -- there is nothing to finalize."""


__all__ = [
    "EmptyEpisodeUnsupportedError",
    "EmptyLeRobotExportError",
    "InconsistentFrequencyError",
    "LeRobotAdapterError",
    "LeRobotTaskRequiredError",
    "NonIntegerFrequencyError",
    "NonUniformTimelineError",
    "UndeterminableFrequencyError",
]
