from __future__ import annotations

from pydantic import Field

from sceneops_core.labels.schemas import LabelSetRef
from sceneops_core.sample_views.schemas import SampleViewPolicy

from .base import BaseJobParams


class BuildSceneSampleViewsJobParams(BaseJobParams):
    """Registered Scenes of one DatasetVersion + an explicit
    SampleViewPolicy + pinned label set revisions -> one SceneSampleView
    revision per Scene (ADR-007 §33.3).

    ``scene_ids`` empty means every registered Scene of the DatasetVersion.
    Each Scene is read at the revision its record points to when the job
    runs, and the view pins that revision.
    """

    dataset_id: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)

    scene_ids: list[str] = Field(default_factory=list)
    policy: SampleViewPolicy
    label_sets: list[LabelSetRef] = Field(default_factory=list)
