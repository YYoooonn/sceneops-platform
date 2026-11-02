"""Canonical time semantics (ADR-007 §27.3, ADR-015 §6.2).

What a canonical timestamp means, independent of where it came from:

    source_time  the integer-nanosecond timestamp value and its exact
                 promotion from coarser source units
    clocks       clock domains, their origins, origin instances and the
                 comparability rule between timestamps
    frame_time   the exact integer frame grid of a unit sampled at a rate

Nothing here knows a source binding, a source kind, a Scene or an Episode.
Which units share an origin instance, and which origins a unit's clocks may
declare, is asserted by the unit's source binding
(``sceneops_core.provenance.sources``), which depends on this package and
never the reverse.
"""

from .clocks import (
    ClockDomain,
    ClockDomainError,
    ClockInstance,
    ClockOrigin,
    IncomparableClocksError,
    OriginScope,
    check_clock_domains,
    clock_instance,
    require_comparable,
    restore_clock_instance,
)
from .frame_time import NS_PER_SECOND, frame_index_of, frame_time_ns
from .source_time import INT64_MAX, SourceTimestampNs, SourceTimeUnit, promote_to_ns

__all__ = [
    "INT64_MAX",
    "NS_PER_SECOND",
    "ClockDomain",
    "ClockDomainError",
    "ClockInstance",
    "ClockOrigin",
    "IncomparableClocksError",
    "OriginScope",
    "SourceTimeUnit",
    "SourceTimestampNs",
    "check_clock_domains",
    "clock_instance",
    "frame_index_of",
    "frame_time_ns",
    "promote_to_ns",
    "require_comparable",
    "restore_clock_instance",
]
