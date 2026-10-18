from __future__ import annotations

from typing import Any, ClassVar

from pydantic import Field, model_validator

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel
from sceneops_core.provenance import CanonicalDomain, RevisionPin


class BaseJobParams(SceneOpsBaseModel):
    metadata: JsonDict = Field(default_factory=dict)


_RECORDING_URI_REASON = (
    "a recording is identified by robot_run_id and resolved from its "
    "registered recording artifact"
)
_ROBOT_ID_REASON = (
    "the robot that produced a recording is the RobotRun's robot_id and "
    "cannot be supplied by the caller"
)
_REJECTED_FIELDS = {
    "mcap_uri": _RECORDING_URI_REASON,
    "mcapUri": _RECORDING_URI_REASON,
    "rosbag_uri": _RECORDING_URI_REASON,
    "rosbagUri": _RECORDING_URI_REASON,
    "robot_id": _ROBOT_ID_REASON,
    "robotId": _ROBOT_ID_REASON,
}


class RecordingConsumerJobParams(BaseJobParams):
    """Params of a job that reads a registered robot recording.

    The recording is identified only by ``robot_run_id`` and read through
    the verified recording resolver (ADR-007 §12.4), and the RobotRunRecord
    is authoritative for which robot produced it. Recording URIs and
    ``robot_id`` are rejected rather than silently ignored like other
    unknown fields, so a stale caller fails loudly instead of believing it
    selected a different recording or robot.
    """

    robot_run_id: str = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def _reject_caller_recording_identity(cls, data: Any) -> Any:
        if isinstance(data, dict):
            for name, reason in _REJECTED_FIELDS.items():
                if name in data:
                    raise ValueError(f"{name} is not accepted: {reason}")
        return data


def _rejecting(data: Any, rejected: dict[str, str]) -> Any:
    if isinstance(data, dict):
        for name, reason in rejected.items():
            if name in data:
                raise ValueError(f"{name} is not accepted: {reason}")
    return data


def _dataset_version_fields(reason: str) -> dict[str, str]:
    return {
        name: reason
        for name in ("dataset_id", "datasetId", "dataset_version", "datasetVersion")
    }


_BUILD_REASON = (
    "a canonical build is a function of the recording and build_config only; "
    "DatasetVersion membership is a separate composition"
)


class RecordingBuildJobParams(RecordingConsumerJobParams):
    """Params of a canonical build from one registered recording (ADR-015
    §6.7, MS-4).

    A build names no DatasetVersion: its manifests are unit-scoped and their
    bytes depend only on the recording and ``build_config``. DatasetVersion
    fields are rejected rather than silently ignored, so a stale caller fails
    loudly instead of believing it built into a DatasetVersion.
    """

    @model_validator(mode="before")
    @classmethod
    def _reject_dataset_version(cls, data: Any) -> Any:
        return _rejecting(data, _dataset_version_fields(_BUILD_REASON))


_MEMBERSHIP_REASON = (
    "canonical registration only registers revisions; DatasetVersion "
    "membership is a separate composition"
)
_REJECTED_REGISTRATION_FIELDS = {
    **_dataset_version_fields(_MEMBERSHIP_REASON),
    "replace": _MEMBERSHIP_REASON,
}


class RevisionRegistrationJobParams(BaseJobParams):
    """Params of a canonical revision registration (ADR-015 §6.4).

    ``manifest_artifact_ids`` name the manifest ArtifactRecords of one
    build. Membership fields are rejected rather than silently ignored, so
    a stale caller fails loudly instead of believing it changed a
    DatasetVersion.
    """

    manifest_artifact_ids: list[str] = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def _reject_membership(cls, data: Any) -> Any:
        return _rejecting(data, _REJECTED_REGISTRATION_FIELDS)


_REVISION_REASON = (
    "a revision is addressed by its pin; a DatasetVersion is resolved to "
    "pins before the job, never inside it"
)


class RevisionAssessmentJobParams(BaseJobParams):
    """Params of a job that assesses registered revisions of one domain
    (validation, profiling): ``revisions`` are their complete pins, each
    assessed exactly, at most one per unit (a job's per-unit run record is
    keyed by its unit).

    A revision has no DatasetVersion: the same pin is assessed once however
    many versions pin it, and readiness is derived per revision. Unit ids
    without a pin and DatasetVersion fields are rejected loudly, so a stale
    caller cannot have the job pick a revision for it.
    """

    domain: ClassVar[CanonicalDomain]
    _unit_id_fields: ClassVar[tuple[str, ...]] = ()

    revisions: list[RevisionPin] = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def _reject_unpinned(cls, data: Any) -> Any:
        rejected = _dataset_version_fields(_REVISION_REASON) | {
            name: _REVISION_REASON for name in cls._unit_id_fields
        }
        return _rejecting(data, rejected)

    @model_validator(mode="after")
    def _one_domain_once(self) -> RevisionAssessmentJobParams:
        foreign = sorted({p.unit_id for p in self.revisions if p.domain != self.domain})
        if foreign:
            raise ValueError(f"revisions must be {self.domain} revisions: {foreign}")
        units = [p.unit_id for p in self.revisions]
        if len(set(units)) != len(units):
            raise ValueError("revisions must pin each unit at most once")
        return self
