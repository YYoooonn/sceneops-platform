"""Frame-grid time (ADR-015 §6.2, OQ-2).

A unit sampled on a frame grid at an integer rate ``fps`` places frame ``k``
at::

    t_ns(k) = round_half_even(k · 10⁹ / fps)

computed exactly in integers: no floating-point value takes part. Each tick
is computed from ``k``, never accumulated from the previous one, so the grid
never drifts from its nominal rate. The frame spacing is at least 10⁶ ns for
any ``fps`` up to 1,000 and far larger than the 0.5 ns rounding, so ``t_ns``
is strictly increasing and ``k`` is recovered exactly from ``t_ns``.

A frame rate is not a clock property: it describes how one unit sampled its
clock and is declared by that unit, never by a clock domain.
"""

from __future__ import annotations

from typing import Final

NS_PER_SECOND: Final = 1_000_000_000


def _check_fps(fps: int) -> None:
    if isinstance(fps, bool) or not isinstance(fps, int) or fps < 1:
        raise ValueError(f"fps must be a positive integer, got {fps!r}")


def frame_time_ns(frame_index: int, fps: int) -> int:
    """``round_half_even(frame_index · 10⁹ / fps)``, exactly."""
    _check_fps(fps)
    if isinstance(frame_index, bool) or not isinstance(frame_index, int):
        raise ValueError(f"frame_index must be an int, got {frame_index!r}")
    if frame_index < 0:
        raise ValueError(f"frame_index must be >= 0, got {frame_index}")
    quotient, remainder = divmod(frame_index * NS_PER_SECOND, fps)
    twice = 2 * remainder
    if twice > fps or (twice == fps and quotient % 2 == 1):
        quotient += 1
    return quotient


def frame_index_of(timestamp_ns: int, fps: int) -> int | None:
    """The frame ``k`` with ``frame_time_ns(k, fps) == timestamp_ns``, or None
    when ``timestamp_ns`` is not on the grid."""
    _check_fps(fps)
    if timestamp_ns < 0:
        return None
    candidate = (timestamp_ns * fps + NS_PER_SECOND // 2) // NS_PER_SECOND
    for k in (candidate - 1, candidate, candidate + 1):
        if k >= 0 and frame_time_ns(k, fps) == timestamp_ns:
            return k
    return None


__all__ = ["NS_PER_SECOND", "frame_index_of", "frame_time_ns"]
