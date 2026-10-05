from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from sceneops_core.robots.schemas import (
    MissionRecord,
    MissionStatus,
    RobotRecord,
    RobotRunRecord,
    RobotStateRecord,
    RobotStatus,
)


@runtime_checkable
class RobotRepository(Protocol):
    async def create(self, robot: RobotRecord) -> RobotRecord: ...

    async def upsert(self, robot: RobotRecord) -> RobotRecord: ...

    async def get(self, robot_id: str) -> RobotRecord | None: ...

    async def create_if_absent(self, robot: RobotRecord) -> None: ...

    async def get_for_update(self, robot_id: str) -> RobotRecord | None: ...

    async def update(self, robot: RobotRecord) -> RobotRecord: ...

    async def list(
        self,
        *,
        status: RobotStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[RobotRecord]: ...


@runtime_checkable
class RobotRunRepository(Protocol):
    async def create(self, run: RobotRunRecord) -> RobotRunRecord: ...

    async def get(self, run_id: str) -> RobotRunRecord | None: ...

    async def get_many(self, run_ids: Sequence[str]) -> dict[str, RobotRunRecord]: ...

    async def list(
        self,
        *,
        robot_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[RobotRunRecord]: ...


@runtime_checkable
class MissionRepository(Protocol):
    async def create(self, mission: MissionRecord) -> MissionRecord: ...

    async def upsert(self, mission: MissionRecord) -> MissionRecord: ...

    async def get(self, mission_id: str) -> MissionRecord | None: ...

    async def update(self, mission: MissionRecord) -> MissionRecord: ...

    async def list(
        self,
        *,
        robot_id: str | None = None,
        robot_run_id: str | None = None,
        status: MissionStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[MissionRecord]: ...


@runtime_checkable
class RobotStateRepository(Protocol):
    async def create(self, state: RobotStateRecord) -> RobotStateRecord: ...

    async def create_many(
        self, states: list[RobotStateRecord]
    ) -> list[RobotStateRecord]: ...

    async def get(self, state_id: str) -> RobotStateRecord | None: ...

    async def list(
        self,
        *,
        robot_id: str | None = None,
        robot_run_id: str | None = None,
        mission_id: str | None = None,
        scene_id: str | None = None,
        start_timestamp_us: int | None = None,
        end_timestamp_us: int | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[RobotStateRecord]: ...
