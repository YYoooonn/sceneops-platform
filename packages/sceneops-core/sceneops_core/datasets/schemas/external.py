from __future__ import annotations

from pydantic import ConfigDict, Field, field_validator

from sceneops_core.common.identifiers import validate_external_format
from sceneops_core.common.schemas import SceneOpsBaseModel


class ExternalDatasetRef(SceneOpsBaseModel):
    """Reference to one dataset outside SceneOps' canonical domain model

    Import vs. export is a property of the *operation* that uses this ref,
    never of the ref's own shape -- there is deliberately one
    ``ExternalDatasetRef``, not a separate ``SourceDatasetRef``/
    ``ExportDatasetRef`` pair. Nothing here is, or is compared against,
    SceneOps' own canonical ``dataset_id``/``dataset_version``

    ``format``/``format_version`` are that external system's own identity
    (e.g. ``"nuscenes"``/``"v1.0-mini"``, ``"lerobot"``/``"2.1"``).
    ``format`` is a canonical open identifier, not a closed enum, since the
    set of external formats is open-ended (matches
    ``ExternalDatasetAdapter.format_name`` in
    ``sceneops_analytics.external_adapters``). It is validated and never
    normalized, because it becomes part of canonical unit identity and of
    the producer fingerprint (ADR-007 §27.6). ``format_version``,
    ``external_revision`` and ``checksum`` identify the source revision and
    stay separate from ``format``. ``uri`` is wherever that external
    dataset actually lives -- a dataroot for an import source, a target
    directory/container for an export. It is location, never identity.
    """

    model_config = ConfigDict(extra="forbid")

    format: str
    format_version: str = Field(min_length=1)
    uri: str = Field(min_length=1)

    external_name: str | None = None
    external_revision: str | None = None
    checksum: str | None = None

    @field_validator("format")
    @classmethod
    def _check_format(cls, value: str) -> str:
        return validate_external_format(value)


__all__ = ["ExternalDatasetRef"]
