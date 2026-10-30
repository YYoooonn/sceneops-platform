"""The one label-anchor resolver (ADR-015 §9, M3.0 OQ-6).

An anchor names a source observation; a Scene manifest holds observations of
exactly one source binding. Resolving an anchor on a Scene manifest answers
which observation, and with ``group_ref`` which source-defined group, the
label annotates in that Scene:

    another source                    the anchor's (source_kind, source_id) is not
                                      the Scene's binding -> not in this Scene
    channel not in the Scene          the Scene cannot hold the observation -> not
                                      in this Scene
    no group_ref                      the one observation at (channel,
                                      source_clock, timestamp_ns), or not in this
                                      Scene
    group_ref                         the observation at the anchor and the group
                                      whose native_ref is group_ref; both absent ->
                                      not in this Scene; the observation must be a
                                      member of the group, and anything else (one
                                      of them missing, a group that does not hold
                                      the observation) is an error, never an
                                      unattached label

Resolution reads the Scene manifest only. It never reads a sample view, never
associates by time and never chooses a camera image, so every consumer that
resolves the same anchor on the same Scene revision gets the same answer.
Which sample of a derived view presents a resolved label is that view's
policy (``sceneops_core.sample_views``), not ownership.

An anchor on a clock with origin ``unit`` would be ambiguous between the
units of one source, and the anchor carries no unit key; such an anchor is
refused rather than resolved.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from sceneops_core.provenance import ClockOrigin
from sceneops_core.scenes.schemas.manifests import (
    SceneManifest,
    SceneObservationGroup,
)

from .schemas import ObservationAnchor


class AnchorResolutionError(ValueError):
    """An anchor cannot be resolved consistently on a Scene manifest."""


class AmbiguousAnchorError(AnchorResolutionError):
    """An anchor matches more than one observation of the Scene."""


class GroupRefMismatchError(AnchorResolutionError):
    """An anchor's ``group_ref`` disagrees with the Scene: the group or the
    observation is missing, or the group does not hold the observation."""


class UnsupportedAnchorClockError(AnchorResolutionError):
    """The anchor's channel is on a unit-origin clock, where (source,
    channel, timestamp) does not name one observation."""


@dataclass(frozen=True, order=True)
class ResolvedAnchor:
    """Where an anchor lands in one Scene: the observation, and the group
    when the anchor names one."""

    observation_id: str
    group_id: str | None = None


class SceneAnchorResolver:
    """Resolves anchors on one Scene manifest; build once per Scene."""

    def __init__(self, manifest: SceneManifest) -> None:
        source = manifest.lineage.source
        self._source = (source.source_kind, source.source_id)
        self._channel_clock = {c.channel: c.source_clock for c in manifest.channels}
        self._origin = {d.clock: d.origin for d in manifest.clock_domains}
        self._observations: dict[tuple[str, str, int], list[str]] = defaultdict(list)
        for observation in manifest.observations:
            clock = self._channel_clock[observation.channel]
            self._observations[
                (observation.channel, clock, observation.timestamp_ns)
            ].append(observation.observation_id)
        self._groups: dict[str, SceneObservationGroup] = {
            g.native_ref: g for g in manifest.groups if g.native_ref is not None
        }

    def resolve(self, anchor: ObservationAnchor) -> ResolvedAnchor | None:
        """Where ``anchor`` lands in this Scene, or None when the Scene does
        not hold what it names (another source, unit or channel selection)."""
        if (anchor.source_kind, anchor.source_id) != self._source:
            return None
        clock = self._channel_clock.get(anchor.channel)
        if clock is None:
            return None
        if self._origin[clock] == ClockOrigin.UNIT:
            raise UnsupportedAnchorClockError(
                f"anchor {anchor.channel}@{anchor.timestamp_ns}: channel "
                f"{anchor.channel!r} is on the unit-origin clock {clock!r}; an "
                "anchor names no unit, so it cannot name one observation"
            )
        ids = self._observations.get(
            (anchor.channel, anchor.source_clock, anchor.timestamp_ns), []
        )
        if len(ids) > 1:
            raise AmbiguousAnchorError(
                f"anchor {anchor.channel}@{anchor.timestamp_ns} matches "
                f"observations {sorted(ids)}"
            )
        observation_id = ids[0] if ids else None
        if anchor.group_ref is None:
            return None if observation_id is None else ResolvedAnchor(observation_id)

        group = self._groups.get(anchor.group_ref)
        where = (
            f"anchor {anchor.channel}@{anchor.timestamp_ns} "
            f"({anchor.source_clock}) with group_ref {anchor.group_ref!r}"
        )
        if observation_id is None and group is None:
            return None
        if group is None:
            raise GroupRefMismatchError(
                f"{where} lands on observation {observation_id}, but no group of "
                "the Scene has that native_ref"
            )
        if observation_id is None:
            raise GroupRefMismatchError(
                f"{where} names group {group.group_id}, but the Scene has no "
                f"{anchor.channel!r} observation at the anchor"
            )
        if observation_id not in group.observation_ids:
            raise GroupRefMismatchError(
                f"{where} lands on observation {observation_id}, which is not a "
                f"member of group {group.group_id}"
            )
        return ResolvedAnchor(observation_id, group.group_id)


def resolve_anchor(
    manifest: SceneManifest, anchor: ObservationAnchor
) -> ResolvedAnchor | None:
    """Resolve one anchor; see :class:`SceneAnchorResolver` for many."""
    return SceneAnchorResolver(manifest).resolve(anchor)


__all__ = [
    "AmbiguousAnchorError",
    "AnchorResolutionError",
    "GroupRefMismatchError",
    "ResolvedAnchor",
    "SceneAnchorResolver",
    "UnsupportedAnchorClockError",
    "resolve_anchor",
]
