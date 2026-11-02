"""``restore_clock_instance``: the structural contract of a persisted clock
instance. It accepts every instance a source binding can issue, refuses every
other shape and never reads what a scope element names."""

from __future__ import annotations

import pytest

from sceneops_core.temporal import (
    ClockDomainError,
    ClockInstance,
    ClockOrigin,
    IncomparableClocksError,
    require_comparable,
    restore_clock_instance,
)

CLOCK = "mcap_log_time"


@pytest.mark.parametrize(
    ("origin", "scope"),
    [
        (ClockOrigin.UNIX_EPOCH, ()),
        (ClockOrigin.SOURCE, ("recording", "run-001")),
        (ClockOrigin.UNIT, ("content", "content-abc", "0")),
        # The alphabet of an element belongs to the source.
        (ClockOrigin.UNIT, ("anything", "a key/with spaces#and:punctuation")),
        # Nothing is assumed about how many elements a scope has.
        (ClockOrigin.SOURCE, ("one",)),
    ],
)
def test_a_valid_triple_restores_the_instance_it_describes(origin, scope):
    assert restore_clock_instance(CLOCK, origin, scope) == ClockInstance(
        clock=CLOCK, origin=origin, scope=scope
    )


def test_the_serialized_representation_restores_the_same_instance():
    assert restore_clock_instance(
        CLOCK, "source", ["recording", "run-001"]
    ) == ClockInstance(CLOCK, ClockOrigin.SOURCE, ("recording", "run-001"))


@pytest.mark.parametrize(
    ("clock", "origin", "scope"),
    [
        # clock id
        ("MCAP", "unix_epoch", ()),
        ("", "unix_epoch", ()),
        (None, "unix_epoch", ()),
        # origin
        (CLOCK, "bogus", ("a",)),
        (CLOCK, "UNIX_EPOCH", ()),
        (CLOCK, None, ("a",)),
        (CLOCK, ["source"], ("a",)),
        # origin / scope combination
        (CLOCK, "unix_epoch", ("a",)),
        (CLOCK, "source", ()),
        (CLOCK, "unit", ()),
        (CLOCK, "source", []),
        # scope shape: only an ordered sequence is a scope
        (CLOCK, "source", "recording"),
        (CLOCK, "source", {"recording", "run-001"}),
        (CLOCK, "source", None),
        (CLOCK, "source", {"recording": "run-001"}),
        # scope elements
        (CLOCK, "source", ("recording", "")),
        (CLOCK, "source", ("recording", 1)),
        (CLOCK, "source", ("recording", None)),
        (CLOCK, "source", ("recording", b"run-001")),
        (CLOCK, "source", (" recording",)),
        (CLOCK, "source", ("run\x00-001",)),
    ],
)
def test_an_invalid_triple_is_refused(clock, origin, scope):
    with pytest.raises(ClockDomainError):
        restore_clock_instance(clock, origin, scope)


def test_a_restored_instance_compares_like_an_issued_one():
    source = restore_clock_instance(CLOCK, "source", ["recording", "run-001"])
    require_comparable(
        source, ClockInstance(CLOCK, ClockOrigin.SOURCE, ("recording", "run-001"))
    )
    with pytest.raises(IncomparableClocksError):
        require_comparable(
            source, restore_clock_instance(CLOCK, "source", ["recording", "run-002"])
        )


def test_scope_order_is_part_of_the_instance():
    assert restore_clock_instance(CLOCK, "unit", ["a", "b"]) != restore_clock_instance(
        CLOCK, "unit", ["b", "a"]
    )
