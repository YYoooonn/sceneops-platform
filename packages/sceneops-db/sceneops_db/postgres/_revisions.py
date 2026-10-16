"""Insert-if-absent registration of one unit revision, shared by the Scene
and Episode repositories (contract: ``sceneops_db.repositories.revisions``).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol, TypeVar

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_core.provenance import (
    InconsistentRevisionsError,
    RevisionDecision,
    UnitRevisionFacts,
    decide_revision_registration,
)

from sceneops_db.repositories.revisions import (
    RevisionRegistration,
    UnitIdentityConflictError,
)

_UNIT_BINDING = ("source_kind", "robot_run_id", "unit_key")


class _Revision(UnitRevisionFacts, Protocol):
    def projection(self) -> dict[str, Any]: ...


RevisionT = TypeVar("RevisionT", bound=_Revision)


async def register_unit_revision(
    session: AsyncSession,
    *,
    unit_model: type,
    revision_model: type,
    id_column: str,
    unit_values: dict[str, Any],
    revision_values: dict[str, Any],
    candidate: RevisionT,
    list_revisions: Callable[[str], Awaitable[list[RevisionT]]],
) -> RevisionRegistration[RevisionT]:
    unit_id = unit_values[id_column]
    await session.execute(
        insert(unit_model).values(**unit_values).on_conflict_do_nothing()
    )
    stored_unit = (
        await session.execute(
            select(unit_model).where(getattr(unit_model, id_column) == unit_id)
        )
    ).scalar_one_or_none()
    if stored_unit is None:
        # The insert conflicted on (robot_run_id, unit_key): this source unit
        # is registered under another id.
        raise UnitIdentityConflictError(
            f"source unit ({unit_values['robot_run_id']}, {unit_values['unit_key']!r}) "
            f"is registered under an id other than {unit_id}"
        )
    for name in _UNIT_BINDING:
        if getattr(stored_unit, name) != unit_values[name]:
            raise UnitIdentityConflictError(
                f"unit {unit_id} is registered with {name}="
                f"{getattr(stored_unit, name)!r}, the candidate has "
                f"{unit_values[name]!r}"
            )

    # Two rounds: a lost insert race is decided again against the winner's
    # committed row, which then is either this revision or a conflict.
    for _ in range(2):
        registered = await list_revisions(unit_id)
        decision = decide_revision_registration(
            unit_id=unit_id, candidate=candidate, registered=registered
        )
        if decision == RevisionDecision.EXISTING_REVISION:
            existing = next(
                r
                for r in registered
                if r.manifest_checksum == candidate.manifest_checksum
            )
            if existing.projection() != candidate.projection():
                raise InconsistentRevisionsError(
                    f"unit {unit_id}: revision {candidate.manifest_checksum} is "
                    "registered with a projection other than the candidate's"
                )
            return RevisionRegistration(decision=decision, revision=existing)
        inserted = (
            await session.execute(
                insert(revision_model)
                .values(**revision_values)
                .on_conflict_do_nothing()
                .returning(revision_model.manifest_checksum)
            )
        ).scalar_one_or_none()
        if inserted is not None:
            stored = next(
                r
                for r in await list_revisions(unit_id)
                if r.manifest_checksum == candidate.manifest_checksum
            )
            return RevisionRegistration(decision=decision, revision=stored)
    raise RuntimeError(
        f"unit {unit_id}: registration of revision {candidate.manifest_checksum} "
        "did not converge"
    )


__all__ = ["register_unit_revision"]
