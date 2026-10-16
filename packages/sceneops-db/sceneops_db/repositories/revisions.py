"""The persistence contract of canonical unit revisions (ADR-015 §6.4, MS-7).

``register_revision(candidate)`` on the Scene and Episode repositories
applies ``decide_revision_registration`` against the committed revisions of
the candidate's unit, inside the caller's transaction:

    unit       inserted if absent; a present unit must have the candidate's
               source binding (UnitIdentityConflictError otherwise)
    revision   NEW_UNIT / NEW_REVISION      inserted
               EXISTING_REVISION            nothing written; the stored row
                                            must project exactly what the
                                            candidate projects
               NonDeterministicProducerError, InconsistentRevisionsError
                                            raised; nothing is decided on top

It only adds rows: no unit or revision is ever updated, deleted or
repointed, and nothing here touches DatasetVersion membership.

Concurrency is decided by the unique keys (unit id, (robot_run_id,
unit_key), revision pin, (unit id, producer fingerprint)), not by locks.
Inserts are ``ON CONFLICT DO NOTHING``, which waits for a concurrent
uncommitted insert of a conflicting key; the loser re-reads the committed
revisions (READ COMMITTED) and decides again, so identical registrations
converge on one row and a conflicting one fails.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

from sceneops_core.provenance import RevisionDecision

RevisionT = TypeVar("RevisionT")


class UnitIdentityConflictError(RuntimeError):
    """A unit id is registered with another source binding, or the source
    unit is registered under another id: the stored identity contradicts
    the identity rules and is reported, never repaired."""


@dataclass(frozen=True)
class RevisionRegistration(Generic[RevisionT]):
    decision: RevisionDecision
    # The stored revision (for EXISTING_REVISION, the one already there).
    revision: RevisionT


__all__ = ["RevisionRegistration", "UnitIdentityConflictError"]
