"""COMPOSE_DATASET_VERSION: the only writer of DatasetVersions and their
members (ADR-015 §7, MS-8).

Input is a Dataset, a new version name and either the complete list of
revision pins or a selector. Composition only reads registered revisions
and writes database rows: it never builds or registers a revision and
never touches the ArtifactStore. Every artifact a member references was
verified present and checksum-consistent by revision registration (the
MS-7 gate) before its revision row existed; composition relies on that
gate, not on the members' foreign keys.

    C1  stage, reading only:
          the Dataset exists
          resolve the input to revisions once:
            pins      each pin names a registered revision exactly
                      (unit, manifest checksum and manifest artifact id)
            selector  each clause selects at least one registered revision
          at most one revision per unit; at least one member
          composition_checksum and per-domain summaries from the revisions
    C2  converge: the name is already bound
          same composition_checksum       -> no-op (created = False)
          another composition_checksum    -> conflict
    C3  one transaction: the version row (checksum, selector, summaries)
        and every member row; commit

Nothing is persisted before C3 commits, so a failure or crash at any step
leaves no DatasetVersion row and no member. Concurrent compositions of one
name are decided by its unique key: the loser's insert waits for the
winner's commit, fails, and re-runs C2 against the committed version. A
re-run of a Job whose composition committed but whose success was never
recorded (a reclaimed Job, ADR-010) converges in C2; that convergence, not
a fence, is what makes duplicate execution safe. No row lock is taken:
nothing writes a version after it commits.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sceneops_core.datasets.composition import (
    CompositionError,
    composition_checksum,
    composition_members,
    summarize_composition,
)
from sceneops_core.datasets.schemas import (
    DatasetVersionMember,
    DatasetVersionRecord,
    RevisionSelector,
)
from sceneops_core.episodes.schemas import EpisodeRevisionRecord
from sceneops_core.jobs.schemas import ComposeDatasetVersionJobParams
from sceneops_core.provenance import RevisionPin
from sceneops_core.scenes.schemas import SceneRevisionRecord
from sceneops_db.repositories import DatasetVersionExistsError

from sceneops_worker.core.context import WorkerContext


class DatasetCompositionError(RuntimeError):
    """Base class. No COMPOSE_DATASET_VERSION failure writes anything."""


class CompositionInputError(DatasetCompositionError):
    """The input does not name a valid composition: an unknown Dataset, a
    pin that names no registered revision, a selector clause that selects
    nothing, or more than one revision of a unit."""


class DatasetVersionConflictError(DatasetCompositionError):
    """The version name is already bound to a different composition."""


@dataclass(frozen=True)
class DatasetVersionComposition:
    version: DatasetVersionRecord
    member_count: int
    # False when this exact composition was already committed under the name.
    created: bool


@dataclass(frozen=True)
class _StagedComposition:
    version: DatasetVersionRecord
    members: list[DatasetVersionMember]


async def compose_dataset_version(
    *, context: WorkerContext, params: ComposeDatasetVersionJobParams
) -> DatasetVersionComposition:
    # C1, reads only.
    staged = await _stage(context, params)

    # C2: the common retry path costs no write.
    existing = await context.dataset_store.get_version(
        dataset_id=params.dataset_id, version=params.dataset_version
    )
    if existing is not None:
        return _converge(existing, staged)

    # C3, one transaction.
    try:
        committed = await context.dataset_store.insert_version(
            staged.version, staged.members
        )
        await context.commit()
    except DatasetVersionExistsError:
        # A concurrent composition of this name committed first.
        await context.rollback()
        existing = await context.dataset_store.get_version(
            dataset_id=params.dataset_id, version=params.dataset_version
        )
        if existing is None:
            raise
        return _converge(existing, staged)
    except BaseException:
        await context.rollback()
        raise
    return DatasetVersionComposition(
        version=committed, member_count=len(staged.members), created=True
    )


# --- C1 ----------------------------------------------------------------------------


async def _stage(
    context: WorkerContext, params: ComposeDatasetVersionJobParams
) -> _StagedComposition:
    if await context.dataset_store.get_dataset(params.dataset_id) is None:
        raise CompositionInputError(f"Dataset not found: {params.dataset_id}")

    if params.pins is not None:
        scenes, episodes = await _resolve_pins(context, params.pins)
    else:
        assert params.selector is not None
        scenes, episodes = await _resolve_selector(context, params.selector)

    pins = [r.pin() for r in scenes] + [r.pin() for r in episodes]
    try:
        members = composition_members(pins)
    except CompositionError as exc:
        raise CompositionInputError(str(exc)) from exc
    scene_summary, episode_summary = summarize_composition(
        scenes=scenes, episodes=episodes
    )
    version = DatasetVersionRecord(
        dataset_id=params.dataset_id,
        version=params.dataset_version,
        composition_checksum=composition_checksum(members),
        selector=params.selector,
        scene=scene_summary,
        episode=episode_summary,
    )
    return _StagedComposition(version=version, members=members)


async def _resolve_pins(
    context: WorkerContext, pins: Sequence[RevisionPin]
) -> tuple[list[SceneRevisionRecord], list[EpisodeRevisionRecord]]:
    """The registered revision of every pin. A pin must match its revision
    row exactly; RevisionPin already derives its artifact id from the
    other fields, and the row is checked as well, so a pin that names no
    gate-verified revision is rejected before anything is written."""

    def keys(domain: str) -> list[tuple[str, str]]:
        return [(p.unit_id, p.manifest_checksum) for p in pins if p.domain == domain]

    scene_keys, episode_keys = keys("scene"), keys("episode")
    scenes = await context.scene_store.get_revisions(scene_keys) if scene_keys else []
    episodes = (
        await context.episode_store.get_revisions(episode_keys) if episode_keys else []
    )
    registered = {r.pin(): r for r in scenes} | {r.pin(): r for r in episodes}
    unregistered = [p for p in pins if p not in registered]
    if unregistered:
        raise CompositionInputError(
            "pins name no registered revision: "
            + ", ".join(
                f"{p.domain} {p.unit_id}@{p.manifest_checksum}" for p in unregistered
            )
        )
    return scenes, episodes


async def _resolve_selector(
    context: WorkerContext, selector: RevisionSelector
) -> tuple[list[SceneRevisionRecord], list[EpisodeRevisionRecord]]:
    scenes: list[SceneRevisionRecord] = []
    episodes: list[EpisodeRevisionRecord] = []
    for clause in selector.sources:
        store = (
            context.scene_store if clause.domain == "scene" else context.episode_store
        )
        selected = await store.list_source_revisions(
            robot_run_id=clause.robot_run_id,
            producer_fingerprint=clause.producer_fingerprint,
        )
        if not selected:
            raise CompositionInputError(
                f"selector clause selects no registered {clause.domain} revision: "
                f"robot_run_id={clause.robot_run_id}, "
                f"producer_fingerprint={clause.producer_fingerprint}"
            )
        (scenes if clause.domain == "scene" else episodes).extend(selected)
    return scenes, episodes


# --- C2 ----------------------------------------------------------------------------


def _converge(
    existing: DatasetVersionRecord, staged: _StagedComposition
) -> DatasetVersionComposition:
    if existing.composition_checksum != staged.version.composition_checksum:
        raise DatasetVersionConflictError(
            f"DatasetVersion {existing.dataset_id}/{existing.version} is bound to "
            f"composition {existing.composition_checksum}; this request composes "
            f"{staged.version.composition_checksum}. A version name is never rebound"
        )
    return DatasetVersionComposition(
        version=existing, member_count=len(staged.members), created=False
    )


__all__ = [
    "CompositionInputError",
    "DatasetCompositionError",
    "DatasetVersionComposition",
    "DatasetVersionConflictError",
    "compose_dataset_version",
]
