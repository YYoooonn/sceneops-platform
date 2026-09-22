from __future__ import annotations

from sceneops_core.common.schemas import SceneOpsBaseModel


class ExternalDatasetRef(SceneOpsBaseModel):
    """Reference to one dataset outside SceneOps' canonical domain model

    Import vs. export is a property of the *operation* that uses this ref,
    never of the ref's own shape -- there is deliberately one
    ``ExternalDatasetRef``, not a separate ``SourceDatasetRef``/
    ``ExportDatasetRef`` pair. Nothing here is, or is compared against,
    SceneOps' own canonical ``dataset_id``/``dataset_version``

    ``format``/``format_version`` are that external system's own identity
    (e.g. ``"nuscenes"``/``"v1.0-mini"``, ``"lerobot"``/``"2.1"``) --
    deliberately plain strings, not a closed enum, since the set of
    external formats is open-ended (matches
    ``ExternalDatasetAdapter.format_name`` in
    ``sceneops_analytics.external_adapters``, Request 3.1). ``uri`` is
    wherever that external dataset actually lives -- a dataroot for an
    import source, a target directory/container for an export.
    """

    format: str
    format_version: str
    uri: str

    external_name: str | None = None
    external_revision: str | None = None
    checksum: str | None = None


__all__ = ["ExternalDatasetRef"]
