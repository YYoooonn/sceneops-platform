"""Clock domains (ADR-015 §6.2, MS-5).

Every canonical timestamp is an integer nanosecond count in one declared
clock domain. A canonical manifest declares each clock it uses exactly once
and binds every timestamped structure to it by clock id:

    ClockDomain
      clock          open identifier (``validate_source_clock``); a clock an
                     external format defines is ``<format>.<clock>``
      origin         what zero means, and so which timestamps are comparable:
                       unix_epoch  absolute; comparable wherever the clock is used
                       source      absolute within one source (one RobotRun,
                                   one SourceContent); its epoch is unknown or
                                   not asserted
                       unit        relative; zero is the unit's own origin
      resolution_ns  the source's native resolution (1,000 for a µs source);
                     every timestamp on the clock is a multiple of it

Two timestamps are comparable only on the same clock id *and* the same
origin instance (``ClockInstance``): any instance for ``unix_epoch``, the
same source for ``source``, the same unit for ``unit``. Two RobotRuns both on
``mcap_log_time`` are not comparable; neither are two Episodes of one
content on a ``unit`` clock.

A sampling rate is not a clock property: a frame rate says how one unit
sampled its clock and is never declared here. Normalization changes the
representation of a timestamp, never its domain: nothing adds an epoch to a
relative time, shifts a clock or merges two clocks.

Every recording clock has origin ``source`` (OQ-9). Capture's log time is a
host clock and a header stamp's epoch is a property of the robot; the
platform cannot verify either from the recording, so recording builders set
the origin and no build configuration can change it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Final

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
)

from sceneops_core.common.identifiers import validate_source_clock

from .sources import SourceBinding


class ClockOrigin(StrEnum):
    UNIX_EPOCH = "unix_epoch"
    SOURCE = "source"
    UNIT = "unit"


# OQ-9: the origin of every clock a recording source declares.
RECORDING_CLOCK_ORIGIN: Final = ClockOrigin.SOURCE
# Recording timestamps (MCAP log / publish time, ROS 2 header stamps,
# integer-nanosecond payload fields) are native nanoseconds.
RECORDING_CLOCK_RESOLUTION_NS: Final = 1


class ClockDomainError(ValueError):
    """Clock-domain declarations or references are inconsistent."""


class IncomparableClocksError(ClockDomainError):
    """Two timestamps are on different clocks or origin instances."""


class ClockDomain(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    clock: Annotated[StrictStr, AfterValidator(validate_source_clock)]
    origin: ClockOrigin
    resolution_ns: StrictInt = Field(ge=1)


@dataclass(frozen=True)
class ClockInstance:
    """One origin instance of a clock. Timestamps are comparable exactly
    when their instances are equal.

    ``scope`` is ``()`` for ``unix_epoch``, ``(source_kind, source_id)`` for
    ``source`` and ``(source_kind, source_id, unit_key)`` for ``unit``."""

    clock: str
    origin: ClockOrigin
    scope: tuple[str, ...]


def clock_instance(domain: ClockDomain, source: SourceBinding) -> ClockInstance:
    """The origin instance of ``domain`` as declared by a unit bound to
    ``source``."""
    if domain.origin == ClockOrigin.UNIX_EPOCH:
        scope: tuple[str, ...] = ()
    elif domain.origin == ClockOrigin.SOURCE:
        scope = (source.source_kind, source.source_id)
    else:
        scope = (source.source_kind, source.source_id, source.unit_key)
    return ClockInstance(clock=domain.clock, origin=domain.origin, scope=scope)


def require_comparable(a: ClockInstance, b: ClockInstance) -> None:
    """Refuse to compare timestamps across clocks or origin instances; no
    mapping between them is ever inferred."""
    if a != b:
        raise IncomparableClocksError(
            f"timestamps on {a.clock!r} ({a.origin.value} {list(a.scope)}) and "
            f"{b.clock!r} ({b.origin.value} {list(b.scope)}) are not comparable"
        )


def recording_clock_domains(clocks: Iterable[str]) -> list[ClockDomain]:
    """The declarations of the recording clocks one unit uses, in canonical
    order, as the recording builders set them: origin ``source`` (OQ-9) and
    nanosecond resolution."""
    return [
        ClockDomain(
            clock=clock,
            origin=RECORDING_CLOCK_ORIGIN,
            resolution_ns=RECORDING_CLOCK_RESOLUTION_NS,
        )
        for clock in sorted(set(clocks))
    ]


def check_clock_domains(
    domains: list[ClockDomain],
    *,
    source: SourceBinding,
    referenced: Iterable[str],
    timestamps: Iterable[tuple[str, int]],
) -> None:
    """The clock-domain rules every canonical manifest obeys:

    - ``domains`` is sorted by clock id, and each clock is declared once;
    - every clock a structure references is declared, and every declared
      clock is referenced (by the window or by a timestamped structure);
    - a recording source declares every clock with origin ``source``;
    - every timestamp is a multiple of its clock's resolution.
    """
    clocks = [d.clock for d in domains]
    if clocks != sorted(set(clocks)):
        raise ClockDomainError(
            f"clock_domains must be sorted by clock and declare each clock once, "
            f"got {clocks}"
        )
    declared = {d.clock: d for d in domains}
    used = set(referenced)
    undeclared = sorted(used - set(declared))
    if undeclared:
        raise ClockDomainError(f"clocks {undeclared} are used but not declared")
    unused = sorted(set(declared) - used)
    if unused:
        raise ClockDomainError(f"clocks {unused} are declared but not used")
    if source.source_kind == "recording":
        wrong = sorted(d.clock for d in domains if d.origin != RECORDING_CLOCK_ORIGIN)
        if wrong:
            raise ClockDomainError(
                f"a recording source declares every clock with origin "
                f"{RECORDING_CLOCK_ORIGIN.value!r}; {wrong} do not"
            )
    for clock, timestamp_ns in timestamps:
        resolution = declared[clock].resolution_ns
        if timestamp_ns % resolution:
            raise ClockDomainError(
                f"timestamp {timestamp_ns} ({clock}) is not a multiple of the "
                f"clock's resolution {resolution} ns"
            )


__all__ = [
    "RECORDING_CLOCK_ORIGIN",
    "RECORDING_CLOCK_RESOLUTION_NS",
    "ClockDomain",
    "ClockDomainError",
    "ClockInstance",
    "ClockOrigin",
    "IncomparableClocksError",
    "check_clock_domains",
    "clock_instance",
    "recording_clock_domains",
    "require_comparable",
]
