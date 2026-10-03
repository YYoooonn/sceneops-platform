"""Scene validation / profile run records.

A per-scene run record pins the exact manifest revision it assessed
(``manifest_artifact_id`` + ``manifest_checksum``). Run records are
append-only history; a record for an older revision never counts toward
the current revision's readiness (ADR-007 §13.4, §18.5). The job-level
aggregate record of a validate/profile job spans several scenes and has no
``scene_id`` and no pin.
"""

from __future__ import annotations

from pydantic import Field, model_validator

from sceneops_core.common.schemas import JsonDict
from sceneops_core.runs.schemas import BaseRunRecord, RunType


class _SceneRunRecord(BaseRunRecord):
    scene_id: str | None = None
    manifest_artifact_id: str | None = None
    manifest_checksum: str | None = None

    dataset_id: str | None = None
    dataset_version: str | None = None

    @model_validator(mode="after")
    def _check_revision_pin(self) -> _SceneRunRecord:
        pinned = (self.manifest_artifact_id, self.manifest_checksum)
        if self.scene_id is None and pinned != (None, None):
            raise ValueError("a job-level scene run record cannot pin a revision")
        if self.scene_id is not None and None in pinned:
            raise ValueError(
                "a per-scene run record must pin manifest_artifact_id and "
                "manifest_checksum"
            )
        return self

    def assessed(self, *, manifest_artifact_id: str, manifest_checksum: str) -> bool:
        return (
            self.manifest_artifact_id == manifest_artifact_id
            and self.manifest_checksum == manifest_checksum
        )


class SceneValidationRunRecord(_SceneRunRecord):
    type: RunType = RunType.SCENE_VALIDATION

    validation_status: str | None = None
    should_block_pipeline: bool = False

    validation_report_uri: str | None = None

    checked_observation_count: int | None = None
    checked_keyframe_count: int | None = None

    issue_count: int | None = None
    error_count: int | None = None
    warning_count: int | None = None

    missing_channel_count: int | None = None

    summary: JsonDict = Field(default_factory=dict)


class SceneProfileRunRecord(_SceneRunRecord):
    type: RunType = RunType.SCENE_PROFILE

    profile_report_uri: str | None = None

    observation_count: int | None = None
    keyframe_count: int | None = None
    annotation_count: int | None = None

    observed_channels: list[str] = Field(default_factory=list)

    coverage: JsonDict = Field(default_factory=dict)
    annotation_summary: JsonDict = Field(default_factory=dict)
