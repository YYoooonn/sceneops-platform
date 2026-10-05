from __future__ import annotations

from pydantic import ConfigDict, Field, field_validator

from sceneops_core.common.identifiers import validate_external_format
from sceneops_core.common.schemas import SceneOpsBaseModel


class ExternalDatasetRef(SceneOpsBaseModel):
    """Where an interoperability runtime reads or writes one external
    dataset representation (ADR-007 §29.9).

    An integration-runtime locator only: it is execution input, never
    canonical provenance, identity or fingerprint input. ``format`` is a
    canonical open identifier (``lerobot``); ``format_version``,
    ``external_revision`` and ``checksum`` identify the external revision;
    ``uri`` is a location and ``external_name`` a display label.
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
