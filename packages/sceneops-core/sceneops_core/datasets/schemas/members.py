"""DatasetVersion membership: a member pins exactly one registered revision
(ADR-015 §7).

    member = (domain, unit_id, manifest_artifact_id, manifest_checksum)

A member is a ``RevisionPin`` placed in a DatasetVersion, so the pins
canonical registration returns are exactly what a composition admits. A
DatasetVersion has at most one member per unit, so it pins at most one
revision of each unit, and members may mix domains and sources. The pin is
the revision itself, not "the unit's current revision": a unit has no
current revision, and registering another revision never changes a member.
A member never names a RobotRun, SourceContent or SourceSnapshot.
"""

from __future__ import annotations

from sceneops_core.provenance import RevisionPin


class DatasetVersionMember(RevisionPin):
    pass


__all__ = ["DatasetVersionMember"]
