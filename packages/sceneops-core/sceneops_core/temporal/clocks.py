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

What "the same source" and "the same unit" are is not a temporal decision:
a unit's source binding asserts it as an ``OriginScope``, and the binding
also says which origins its clocks may declare
(``sceneops_core.provenance.sources``). Nothing here knows a source kind.

A sampling rate is not a clock property: a frame rate says how one unit
sampled its clock and is never declared here. Normalization changes the
representation of a timestamp, never its domain: nothing adds an epoch to a
relative time, shifts a clock or merges two clocks.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
)

from sceneops_core.common.identifiers import validate_source_clock


class ClockOrigin(StrEnum):
    UNIX_EPOCH = "unix_epoch"
    SOURCE = "source"
    UNIT = "unit"


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
class OriginScope:
    """The origin instances a unit's clocks resolve to, as the unit's source
    binding asserts them: ``source`` is shared by every unit of one source,
    ``unit`` by this unit alone. Both are opaque, non-empty keys; equal keys
    mean one origin instance, so a key never names an origin instance it does
    not own."""

    source: tuple[str, ...]
    unit: tuple[str, ...]

    def __post_init__(self) -> None:
        # An empty key would equal the unix_epoch instance's and make
        # source- or unit-relative timestamps globally comparable.
        if not self.source or not self.unit:
            raise ValueError("origin scope keys must be non-empty")


@dataclass(frozen=True)
class ClockInstance:
    """One origin instance of a clock. Timestamps are comparable exactly
    when their instances are equal.

    ``scope`` is ``()`` for ``unix_epoch``, the unit's source key
    (``OriginScope.source``) for ``source`` and its unit key
    (``OriginScope.unit``) for ``unit``."""

    clock: str
    origin: ClockOrigin
    scope: tuple[str, ...]


def clock_instance(domain: ClockDomain, scope: OriginScope) -> ClockInstance:
    """The origin instance of ``domain`` as declared by a unit whose binding
    asserts ``scope``."""
    if domain.origin == ClockOrigin.UNIX_EPOCH:
        key: tuple[str, ...] = ()
    elif domain.origin == ClockOrigin.SOURCE:
        key = scope.source
    else:
        key = scope.unit
    return ClockInstance(clock=domain.clock, origin=domain.origin, scope=key)


def require_comparable(a: ClockInstance, b: ClockInstance) -> None:
    """Refuse to compare timestamps across clocks or origin instances; no
    mapping between them is ever inferred."""
    if a != b:
        raise IncomparableClocksError(
            f"timestamps on {a.clock!r} ({a.origin.value} {list(a.scope)}) and "
            f"{b.clock!r} ({b.origin.value} {list(b.scope)}) are not comparable"
        )


def check_clock_domains(
    domains: list[ClockDomain],
    *,
    permitted_origins: Collection[ClockOrigin],
    referenced: Iterable[str],
    timestamps: Iterable[tuple[str, int]],
) -> None:
    """The clock-domain rules every canonical manifest obeys:

    - ``domains`` is sorted by clock id, and each clock is declared once;
    - every clock a structure references is declared, and every declared
      clock is referenced (by the window or by a timestamped structure);
    - every clock has one of ``permitted_origins``, which the unit's source
      binding states;
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
    wrong = sorted(d.clock for d in domains if d.origin not in permitted_origins)
    if wrong:
        allowed = " or ".join(
            repr(o.value) for o in ClockOrigin if o in permitted_origins
        )
        raise ClockDomainError(
            f"the unit's source declares every clock with origin {allowed}; "
            f"{wrong} do not"
        )
    for clock, timestamp_ns in timestamps:
        resolution = declared[clock].resolution_ns
        if timestamp_ns % resolution:
            raise ClockDomainError(
                f"timestamp {timestamp_ns} ({clock}) is not a multiple of the "
                f"clock's resolution {resolution} ns"
            )


__all__ = [
    "ClockDomain",
    "ClockDomainError",
    "ClockInstance",
    "ClockOrigin",
    "IncomparableClocksError",
    "OriginScope",
    "check_clock_domains",
    "clock_instance",
    "require_comparable",
]
