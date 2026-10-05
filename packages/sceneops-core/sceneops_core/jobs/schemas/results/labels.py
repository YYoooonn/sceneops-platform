from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict
from sceneops_core.labels.schemas import LabelSetRef
from sceneops_core.sample_views.schemas import SampleViewRef

from .base import BaseJobResult


class ImportLabelsJobResult(BaseJobResult):
    label_set: LabelSetRef
    manifest_uri: str

    label_count: int = 0
    covered_anchor_count: int = 0
    robot_run_ids: list[str] = Field(default_factory=list)
    provenance_kind: str | None = None

    # False when the identical revision was already registered.
    created: bool = True


class BuildSceneSampleViewsJobResult(BaseJobResult):
    dataset_id: str
    dataset_version: str

    views: list[SampleViewRef] = Field(default_factory=list)
    # Scenes that produced no view, each with the reason.
    skipped: list[JsonDict] = Field(default_factory=list)

    scene_count: int = 0
    sample_count: int = 0
    dropped_anchor_count: int = 0
    created_count: int = 0
    reused_count: int = 0
