from __future__ import annotations

from enum import StrEnum


class RobotStatus(StrEnum):
    REGISTERED = "registered"
    ACTIVE = "active"
    INACTIVE = "inactive"
    DECOMMISSIONED = "decommissioned"


class RobotRunStatus(StrEnum):
    RECORDING = "recording"
    COMPLETED = "completed"
    INGESTED = "ingested"
    FAILED = "failed"


class MissionStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"


class RobotOperationState(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    ERROR = "error"
    EMERGENCY_STOP = "emergency_stop"
