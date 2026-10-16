"""DatasetVersion membership: a member pins exactly one registered revision
(ADR-015 §7).

    member = (domain, unit_id, manifest_artifact_id, manifest_checksum)

A DatasetVersion has at most one member per unit, so it pins at most one
revision of each unit, and members may mix domains and sources. The pin is
the revision itself, not "the unit's current revision": a unit has no
current revision, and registering another revision never changes a member.
A member never names a RobotRun, SourceContent or SourceSnapshot.
"""

from __future__ import annotations

from pydantic import ConfigDict, StrictStr, model_validator

from sceneops_core.common.schemas import SceneOpsBaseModel, to_camel
from sceneops_core.provenance import (
    CanonicalDomain,
    check_revision_pin,
    unit_id_domain,
)


class DatasetVersionMember(SceneOpsBaseModel):
    model_config = ConfigDict(
        populate_by_name=True, alias_generator=to_camel, extra="forbid", frozen=True
    )

    domain: CanonicalDomain
    unit_id: StrictStr
    manifest_artifact_id: StrictStr
    manifest_checksum: StrictStr

    @model_validator(mode="after")
    def _check_pin(self) -> DatasetVersionMember:
        if unit_id_domain(self.unit_id) != self.domain:
            raise ValueError(f"unit {self.unit_id!r} is not a {self.domain} unit")
        check_revision_pin(
            unit_id=self.unit_id,
            manifest_artifact_id=self.manifest_artifact_id,
            manifest_checksum=self.manifest_checksum,
        )
        return self


__all__ = ["DatasetVersionMember"]
