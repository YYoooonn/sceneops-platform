from __future__ import annotations


class AlignmentError(Exception):
    """Base class for pure Episode-alignment errors (sceneops-core, no I/O)."""


class InvalidEpisodeBoundsError(AlignmentError):
    """The aligned timeline has no bounds: no alignable occurrence, or
    inverted bounds."""


class ClockMismatchError(AlignmentError):
    """A stream to align is timestamped in another clock than the one the
    alignment runs on. Timestamps of unrelated clocks are never compared or
    converted."""


class InvalidAlignmentConfigError(AlignmentError):
    """A resolved per-channel policy is missing configuration it requires
    (e.g. nearest without a tolerance, linear_interpolation without a
    max_gap_us, or an interpolation-ineligible value kind)."""


class UnknownChannelError(AlignmentError):
    """A channel has no explicit override and no known semantic default.
    Deliberately not silently defaulted to a generic policy — see SceneOps
    V2 Request 2.1B §9/§20."""


class InterpolationShapeError(AlignmentError):
    """A linear_interpolation bracket's before/after values can't be
    interpolated (mismatched vector dimensions, non-numeric kind)."""
