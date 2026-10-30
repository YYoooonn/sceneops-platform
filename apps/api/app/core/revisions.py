"""How a read endpoint names one revision of a Scene or Episode unit.

A unit has no current revision (ADR-015 §6.4), so a revision-level read
names it either directly by its manifest checksum, or through a
DatasetVersion that pins one revision of the unit. Exactly one of the two.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Query

from app.core.errors import raise_bad_request


@dataclass(frozen=True)
class RevisionAddress:
    manifest_checksum: str | None = None
    dataset_id: str | None = None
    dataset_version: str | None = None

    def describe(self, unit_id: str) -> str:
        if self.manifest_checksum is not None:
            return f"{unit_id}@{self.manifest_checksum}"
        return f"{unit_id} in {self.dataset_id}:{self.dataset_version}"


def revision_address(
    manifest_checksum: Annotated[str | None, Query()] = None,
    dataset_id: Annotated[str | None, Query()] = None,
    dataset_version: Annotated[str | None, Query()] = None,
) -> RevisionAddress:
    by_version = (dataset_id, dataset_version)
    if manifest_checksum is not None and by_version == (None, None):
        return RevisionAddress(manifest_checksum=manifest_checksum)
    if manifest_checksum is None and None not in by_version:
        return RevisionAddress(dataset_id=dataset_id, dataset_version=dataset_version)
    raise_bad_request(
        "name the revision by manifest_checksum, or by the dataset_id and "
        "dataset_version that pin it; exactly one of the two"
    )
    raise AssertionError("unreachable")


RevisionAddressDep = Annotated[RevisionAddress, Depends(revision_address)]
