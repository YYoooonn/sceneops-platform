from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict

from .base import BaseJobResult


class IngestRobotStatesJobResult(BaseJobResult):
    robot_id: str
    robot_run_id: str | None = None

    state_count: int = 0

    start_timestamp_us: int | None = None
    end_timestamp_us: int | None = None

    metadata: JsonDict = Field(default_factory=dict)
