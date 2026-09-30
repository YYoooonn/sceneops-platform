from __future__ import annotations

import hashlib
from dataclasses import dataclass

from sceneops_storage import ArtifactStore


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class RobotRunArtifactWriteResult:
    uri: str
    checksum: str
    size_bytes: int


class RobotRunArtifactStore:
    """URI/write helpers for one RobotRun's raw recording artifact.

    One RobotRun has exactly one raw ROS2 rosbag2/MCAP recording (whether
    it reached local disk via a direct ``ros2 bag record`` or via
    Kafka-based durable capture, ros2/capture/ -- both produce identical
    MCAP content, so this store makes no distinction). Mirrors
    ``EpisodeArtifactStore``'s URI-helper-plus-write-result shape
    (``sceneops_worker/episodes/artifacts.py``).
    """

    def __init__(
        self,
        *,
        artifact_store: ArtifactStore,
        robot_run_root_uri: str,
    ) -> None:
        self.artifact_store = artifact_store
        self.robot_run_root_uri = robot_run_root_uri

    def recording_uri(self, robot_run_id: str) -> str:
        return self.artifact_store.join_uri(
            self.robot_run_root_uri, robot_run_id, f"{robot_run_id}.mcap"
        )

    async def exists(self, robot_run_id: str) -> bool:
        return await self.artifact_store.exists(self.recording_uri(robot_run_id))

    async def read_recording_bytes(self, robot_run_id: str) -> bytes:
        return await self.artifact_store.read_bytes(self.recording_uri(robot_run_id))

    async def write_recording(
        self, *, robot_run_id: str, data: bytes
    ) -> RobotRunArtifactWriteResult:
        uri = self.recording_uri(robot_run_id)
        await self.artifact_store.write_bytes(uri, data)
        return RobotRunArtifactWriteResult(
            uri=uri, checksum=f"sha256:{_sha256_hex(data)}", size_bytes=len(data)
        )
