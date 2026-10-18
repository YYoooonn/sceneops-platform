"""A revision-level read names exactly one revision: by manifest checksum, or
by the DatasetVersion that pins it (app.core.revisions)."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.core.revisions import RevisionAddress, revision_address

CHECKSUM = "sha256:" + "a" * 64


def test_a_revision_is_named_by_its_checksum_or_by_a_dataset_version():
    assert revision_address(manifest_checksum=CHECKSUM) == RevisionAddress(
        manifest_checksum=CHECKSUM
    )
    assert revision_address(dataset_id="d", dataset_version="v") == RevisionAddress(
        dataset_id="d", dataset_version="v"
    )


@pytest.mark.parametrize(
    "query",
    [
        {},
        {"dataset_id": "d"},
        {"dataset_version": "v"},
        {"manifest_checksum": CHECKSUM, "dataset_id": "d", "dataset_version": "v"},
        {"manifest_checksum": CHECKSUM, "dataset_id": "d"},
    ],
)
def test_anything_but_exactly_one_address_is_refused(query):
    with pytest.raises(HTTPException) as refused:
        revision_address(**query)
    assert refused.value.status_code == 400
