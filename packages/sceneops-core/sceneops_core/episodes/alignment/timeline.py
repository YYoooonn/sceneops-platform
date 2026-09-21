from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from .errors import InvalidAlignmentConfigError, InvalidEpisodeBoundsError

_MICROSECONDS_PER_SECOND = Decimal(1_000_000)


def quantize_period_us(target_frequency_hz: float) -> int:
    """1_000_000 / target_frequency_hz, rounded ONCE to integer microseconds
    with ROUND_HALF_UP (not Python's implicit banker's rounding) — SceneOps
    V2 Request 2.2 §7. Never accumulated across steps; step timestamps are
    always start + n * dt_us (exact integer multiplication)."""
    period = _MICROSECONDS_PER_SECOND / Decimal(str(target_frequency_hz))
    dt_us = int(period.to_integral_value(rounding=ROUND_HALF_UP))
    if dt_us < 1:
        raise InvalidAlignmentConfigError(
            f"target_frequency_hz={target_frequency_hz!r} quantizes to "
            f"dt_us={dt_us} < 1 — frequency is too high to represent in "
            "integer microseconds"
        )
    return dt_us


@dataclass(frozen=True)
class Timeline:
    timestamps_us: tuple[int, ...]
    dt_us: int
    achieved_frequency_hz: float
    step_count: int


def generate_fixed_frequency_timeline(
    *,
    start_timestamp_us: int | None,
    end_timestamp_us: int | None,
    target_frequency_hz: float,
) -> Timeline:
    """Deterministic, integer-only fixed-frequency timeline generation
    (SceneOps V2 Request 2.2 §8). Timeline generation is deliberately
    separate from signal association (policies.py) — this function answers
    only "which timestamps exist," never "what value belongs at each one."

    start is always included; end is included only if it lands exactly
    on-grid; no timestamp ever exceeds end_timestamp_us.
    """
    if start_timestamp_us is None or end_timestamp_us is None:
        raise InvalidEpisodeBoundsError(
            "EpisodeManifest.start_timestamp_us/end_timestamp_us must both "
            "be present for alignment — the engine never infers replacement "
            "bounds (SceneOps V2 Request 2.1B §3)"
        )
    if start_timestamp_us > end_timestamp_us:
        raise InvalidEpisodeBoundsError(
            f"start_timestamp_us ({start_timestamp_us}) is after "
            f"end_timestamp_us ({end_timestamp_us})"
        )

    dt_us = quantize_period_us(target_frequency_hz)

    step_count = (end_timestamp_us - start_timestamp_us) // dt_us + 1
    timestamps = tuple(start_timestamp_us + n * dt_us for n in range(step_count))
    achieved_frequency_hz = float(_MICROSECONDS_PER_SECOND) / dt_us

    return Timeline(
        timestamps_us=timestamps,
        dt_us=dt_us,
        achieved_frequency_hz=achieved_frequency_hz,
        step_count=step_count,
    )
