from __future__ import annotations

from pydantic import Field, model_validator

from sceneops_core.common.schemas import JsonDict
from sceneops_core.datasets.composition import composition_members
from sceneops_core.datasets.schemas import RevisionSelector
from sceneops_core.provenance import RevisionPin

from .base import BaseJobParams


class ComposeDatasetVersionJobParams(BaseJobParams):
    """Compose one immutable DatasetVersion (ADR-015 §7).

    ``dataset_version`` is the new version's name in the existing Dataset
    ``dataset_id``. The members are given as exactly one of:

        pins       the complete list of revision pins, at most one per unit;
                   kept in canonical order, so the request's order does not
                   change its execution key
        selector   resolved to pins once, by the composing Job
                   (``sceneops_core.datasets.schemas.selectors``)

    Either way the composition is the resolved pins, and a name is bound to
    one composition only: composing an existing name converges when the
    pins are the same and fails when they differ.
    """

    dataset_id: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)

    pins: list[RevisionPin] | None = None
    selector: RevisionSelector | None = None

    metadata: JsonDict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _one_member_source(self) -> ComposeDatasetVersionJobParams:
        if (self.pins is None) == (self.selector is None):
            raise ValueError("exactly one of pins or selector is required")
        if self.pins is not None:
            members = composition_members(self.pins)
            self.pins = [RevisionPin.model_validate(m.model_dump()) for m in members]
        return self


class ExportAnalyticsSnapshotJobParams(BaseJobParams):
    dataset_id: str
    dataset_version: str

    # None → export all known tables (scenes, observations, keyframes)
    tables: list[str] | None = None

    metadata: JsonDict = Field(default_factory=dict)
