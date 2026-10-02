from __future__ import annotations

from typing import Any

from pydantic import Field, model_validator

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel


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
