"""DatasetVersion composition rules (ADR-015 §3, §7; MS-6, MS-8).

A DatasetVersion is a name bound once to an immutable composition: a
non-empty set of revision pins with at most one revision per unit. These
functions are the pure part of COMPOSE_DATASET_VERSION; resolving pins
against registered revisions and committing them is the worker's.

    composition_checksum = "sha256:" + sha256(canonical_json({
        "composition_schema": "sceneops.dataset_version_composition/v1",
        "members":            sorted [[domain, unit_id, manifest_checksum], ...]
    }))

The checksum depends on the set of pins only: not on their input order,
the selector that chose them, the version name or anything mutable.
``manifest_artifact_id`` is a function of ``(unit_id, manifest_checksum)``
(``sceneops_core.provenance.revisions``), so it adds nothing to identity.

Summaries are computed once, from the projections of the member revisions,
and are stored with the version; a domain with no member has no summary.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from typing import Final

from sceneops_core.common.canonical_json import canonical_json_bytes
from sceneops_core.episodes.schemas import EpisodeRevisionRecord
from sceneops_core.provenance import RevisionPin
from sceneops_core.scenes.schemas import SceneRevisionRecord

from .schemas.members import DatasetVersionMember
from .schemas.summaries import EpisodeVersionSummary, SceneVersionSummary

COMPOSITION_SCHEMA_V1: Final = "sceneops.dataset_version_composition/v1"


class CompositionError(ValueError):
    """The pins do not form a valid DatasetVersion composition."""


class EmptyCompositionError(CompositionError):
    """A DatasetVersion has at least one member; there is no empty version."""


class DuplicateMemberUnitError(CompositionError):
    """A DatasetVersion pins at most one revision of each unit."""


def _member_key(pin: RevisionPin) -> tuple[str, str, str]:
    return (pin.domain, pin.unit_id, pin.manifest_checksum)


def composition_members(pins: Iterable[RevisionPin]) -> list[DatasetVersionMember]:
    """The members of a composition of ``pins``, in canonical order. Fails
    on no pin and on more than one pin of a unit, identical or not: a
    composition names each member exactly once."""
    members = sorted(
        (DatasetVersionMember.model_validate(pin.model_dump()) for pin in pins),
        key=_member_key,
    )
    if not members:
        raise EmptyCompositionError("a DatasetVersion composition has no member")
    units = [m.unit_id for m in members]
    duplicates = sorted({u for u in units if units.count(u) > 1})
    if duplicates:
        raise DuplicateMemberUnitError(
            f"a DatasetVersion pins at most one revision per unit; "
            f"more than one pin of {duplicates}"
        )
    return members


def composition_checksum(pins: Iterable[RevisionPin]) -> str:
    members = composition_members(pins)
    document = {
        "composition_schema": COMPOSITION_SCHEMA_V1,
        "members": [list(_member_key(m)) for m in members],
    }
    return "sha256:" + hashlib.sha256(canonical_json_bytes(document)).hexdigest()


def summarize_composition(
    *,
    scenes: Sequence[SceneRevisionRecord],
    episodes: Sequence[EpisodeRevisionRecord],
) -> tuple[SceneVersionSummary | None, EpisodeVersionSummary | None]:
    """Per-domain summaries of the member revisions ``scenes`` and
    ``episodes`` (each revision once)."""
    scene = (
        SceneVersionSummary(
            scene_count=len(scenes),
            keyframe_count=sum(r.keyframe_count for r in scenes),
            observation_count=sum(r.observation_count for r in scenes),
            observed_channels=sorted({c for r in scenes for c in r.observed_channels}),
        )
        if scenes
        else None
    )
    episode = EpisodeVersionSummary(episode_count=len(episodes)) if episodes else None
    return scene, episode


__all__ = [
    "COMPOSITION_SCHEMA_V1",
    "CompositionError",
    "DuplicateMemberUnitError",
    "EmptyCompositionError",
    "composition_checksum",
    "composition_members",
    "summarize_composition",
]
