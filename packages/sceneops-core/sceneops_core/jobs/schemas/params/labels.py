from __future__ import annotations

from typing import Any

from pydantic import Field, model_validator

from sceneops_core.common.identifiers import validate_local_id
from sceneops_core.sources import validate_snapshot_id

from .base import BaseJobParams, _dataset_version_fields, _rejecting

_NATIVE_IMPORT_REASON = (
    "a label set is a function of its source content only; DatasetVersion "
    "membership never decides which labels exist"
)


class ImportLabelsJobParams(BaseJobParams):
    """Labels -> one immutable, registered label set revision (ADR-007 §33.2,
    ADR-015 §9). Exactly one input:

    ``document_uri``  a label set document produced by a format adapter
                      outside core (``sceneops.label_set/v2`` shape), which
                      names its own ``label_set_id``; ``expected_checksum``
                      pins the document bytes when the caller must be sure
                      which file was read.
    ``snapshot_id``   a registered SourceSnapshot whose content carries the
                      source's own annotations (native import). It is
                      resolved to its content once and recorded as execution
                      provenance; the labels are read from the content alone,
                      so every snapshot of the same bytes yields the same
                      revision. ``label_set_id`` names the set; ``unit_keys``
                      limits it to those native units (default: every unit of
                      the content), kept unique and sorted.

    The importer never modifies a Scene, an Episode, a RobotRun or a
    DatasetVersion.
    """

    document_uri: str | None = Field(default=None, min_length=1)
    expected_checksum: str | None = None

    snapshot_id: str | None = None
    label_set_id: str | None = None
    unit_keys: list[str] | None = Field(default=None, min_length=1)

    @model_validator(mode="before")
    @classmethod
    def _reject_dataset_version(cls, data: Any) -> Any:
        if isinstance(data, dict) and (
            data.get("snapshot_id") is not None or data.get("snapshotId") is not None
        ):
            return _rejecting(data, _dataset_version_fields(_NATIVE_IMPORT_REASON))
        return data

    @model_validator(mode="after")
    def _check_input(self) -> ImportLabelsJobParams:
        if (self.document_uri is None) == (self.snapshot_id is None):
            raise ValueError("exactly one of document_uri or snapshot_id is required")
        if self.document_uri is not None:
            native = {
                "label_set_id": self.label_set_id,
                "unit_keys": self.unit_keys,
            }
            given = sorted(k for k, v in native.items() if v is not None)
            if given:
                raise ValueError(
                    f"{given} apply to a native import (snapshot_id); a label "
                    "document names its own label set and anchors"
                )
            return self
        assert self.snapshot_id is not None
        validate_snapshot_id(self.snapshot_id)
        if self.expected_checksum is not None:
            raise ValueError("expected_checksum pins a document; use document_uri")
        if self.label_set_id is None:
            raise ValueError("a native import (snapshot_id) needs label_set_id")
        validate_local_id(self.label_set_id, field="label_set_id")
        if self.unit_keys is not None:
            if len(set(self.unit_keys)) != len(self.unit_keys):
                raise ValueError("unit_keys must be unique")
            self.unit_keys = sorted(self.unit_keys)
        return self
