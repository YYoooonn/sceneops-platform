"""Revision selectors: a declarative way to name the revisions a
DatasetVersion composition pins, instead of listing every pin (ADR-015 §7).

A selector is resolved to pins once, by the composing Job, before the
composition is committed. The composition is the resolved pins; the
selector is recorded beside them as how they were chosen and is never
re-resolved, so a version never changes when revisions are registered
later.

    RevisionSourceClause   every revision registered for one source under
                           one producer fingerprint, in one domain:
                             units of domain whose source is robot_run_id
                             revision.producer_fingerprint == producer_fingerprint
                           One unit and one fingerprint have at most one
                           revision (MS-7), so a clause selects at most one
                           revision per unit; a clause that selects nothing
                           fails the composition.
    RevisionSelector       the union of its clauses; clauses are unique and
                           kept in canonical order
"""

from __future__ import annotations

from pydantic import ConfigDict, Field, StrictStr, field_validator

from sceneops_core.common.checksums import SHA256_CHECKSUM_PATTERN
from sceneops_core.common.schemas import SceneOpsBaseModel, to_camel
from sceneops_core.provenance import CanonicalDomain

_FROZEN = ConfigDict(
    populate_by_name=True, alias_generator=to_camel, extra="forbid", frozen=True
)


class RevisionSourceClause(SceneOpsBaseModel):
    model_config = _FROZEN

    domain: CanonicalDomain
    robot_run_id: StrictStr = Field(min_length=1)
    producer_fingerprint: StrictStr = Field(pattern=SHA256_CHECKSUM_PATTERN)

    def sort_key(self) -> tuple[str, str, str]:
        return (self.domain, self.robot_run_id, self.producer_fingerprint)


class RevisionSelector(SceneOpsBaseModel):
    model_config = _FROZEN

    sources: list[RevisionSourceClause] = Field(min_length=1)

    @field_validator("sources")
    @classmethod
    def _canonical_clauses(
        cls, sources: list[RevisionSourceClause]
    ) -> list[RevisionSourceClause]:
        keys = [clause.sort_key() for clause in sources]
        if len(set(keys)) != len(keys):
            raise ValueError("selector contains a duplicate source clause")
        return sorted(sources, key=RevisionSourceClause.sort_key)


__all__ = ["RevisionSelector", "RevisionSourceClause"]
