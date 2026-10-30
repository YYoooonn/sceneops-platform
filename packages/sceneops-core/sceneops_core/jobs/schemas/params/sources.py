from __future__ import annotations

from pydantic import Field

from .base import BaseJobParams


class RegisterSourceSnapshotJobParams(BaseJobParams):
    """REGISTER_SOURCE_SNAPSHOT: verify a published SourceSnapshot descriptor,
    its content inventory and, on the content's first registration, every
    preserved file, then insert the SourceContent / SourceSnapshot records
    (ADR-015 §5). The URI must be
    ``{source root}/snapshots/{snapshot_id}/descriptor.json`` under the
    worker's configured source root; the inventory location is derived from
    the same root. Not tied to a Dataset/DatasetVersion or a RobotRun."""

    descriptor_uri: str = Field(min_length=1)
