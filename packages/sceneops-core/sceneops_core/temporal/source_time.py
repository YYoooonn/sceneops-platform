"""Canonical source-time primitives (ADR-007 §27.3).

A canonical source timestamp is an integer count of nanoseconds in a
declared source clock domain. ``(timestamp_ns, source_clock)`` together
define temporal meaning; a timestamp without its clock is not interpretable.

- Nanosecond sources (ROS2, MCAP) stay nanosecond-exact.
- Lower-precision sources are promoted exactly by integer multiplication.
- No floating-point value ever participates in source-time identity.
- No UTC interpretation is imposed: whether a clock is a wall clock is a
  property of the declared clock, and rendering a datetime is presentation.

The range is bounded to signed 64-bit so a value round-trips through
PostgreSQL BIGINT, Parquet INT64 and Arrow ``timestamp[ns]`` unchanged.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Final

from pydantic import Field, StrictInt

INT64_MAX: Final = 2**63 - 1

SourceTimestampNs = Annotated[StrictInt, Field(ge=0, le=INT64_MAX)]


class SourceTimeUnit(StrEnum):
    SECONDS = "s"
    MILLISECONDS = "ms"
    MICROSECONDS = "us"
    NANOSECONDS = "ns"


_NS_PER_UNIT: Final = {
    SourceTimeUnit.SECONDS: 1_000_000_000,
    SourceTimeUnit.MILLISECONDS: 1_000_000,
    SourceTimeUnit.MICROSECONDS: 1_000,
    SourceTimeUnit.NANOSECONDS: 1,
}


def promote_to_ns(value: int, unit: SourceTimeUnit) -> int:
    """Exactly promote an integer source timestamp to nanoseconds.

    Only integers are accepted. A fractional-seconds float cannot be
    promoted without choosing a rounding, so the integration must decide
    how its source encodes time before calling this.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"source timestamp must be an int, got {type(value).__name__}")
    unit = SourceTimeUnit(unit)
    timestamp_ns = value * _NS_PER_UNIT[unit]
    if not 0 <= timestamp_ns <= INT64_MAX:
        raise ValueError(
            f"source timestamp {value}{unit.value} is outside the canonical "
            f"range [0, 2^63-1] ns"
        )
    return timestamp_ns


__all__ = [
    "INT64_MAX",
    "SourceTimeUnit",
    "SourceTimestampNs",
    "promote_to_ns",
]
