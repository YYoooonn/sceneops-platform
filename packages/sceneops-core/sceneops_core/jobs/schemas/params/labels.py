from __future__ import annotations

from pydantic import Field

from .base import BaseJobParams


class ImportLabelsJobParams(BaseJobParams):
    """A label set document -> one immutable, registered label set revision
    (ADR-007 §33.2).

    ``document_uri`` locates a label set document produced by a format
    adapter outside core (``sceneops.label_set/v1`` shape). The importer
    validates and canonicalizes it and registers the revision; it never
    modifies a Scene, an Episode or a RobotRun. ``expected_checksum`` pins
    the document bytes when the caller must be sure which file was read.
    """

    document_uri: str = Field(min_length=1)
    expected_checksum: str | None = None
