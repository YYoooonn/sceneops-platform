from app.modules.artifacts.storage import ArtifactStorage
from app.modules.datasets.repository import DatasetRepository

from sceneops_core.ids.artifacts import sample_sensor_artifact_id
from sceneops_core.schemas.artifacts import ArtifactType


class ArtifactService:
    def __init__(
        self,
        dataset_repository: DatasetRepository,
        artifact_storage: ArtifactStorage,
    ) -> None:
        self.dataset_repository = dataset_repository
        self.artifact_storage = artifact_storage

    def list_sample_artifacts(
        self,
        dataset_id: str,
        dataset_version: str,
        sample_id: str,
    ) -> list[dict]:
        sample = self.dataset_repository.get_sample(
            dataset_id,
            dataset_version,
            sample_id,
        )

        if sample is None:
            return []

        artifacts = []

        for channel, sensor in sample.get("sensors", {}).items():
            filename = sensor.get("filename")

            if filename is None:
                continue

            artifacts.append(
                {
                    "artifactId": sample_sensor_artifact_id(
                        sample_id=sample_id,
                        channel=channel,
                    ),
                    "datasetId": dataset_id,
                    "datasetVersion": dataset_version,
                    "sceneId": sample["sceneId"],
                    "sampleId": sample_id,
                    "type": self._infer_artifact_type(channel),
                    "channel": channel,
                    "uri": filename,
                    "downloadUrl": self.artifact_storage.get_download_url(filename),
                }
            )

        return artifacts

    def _infer_artifact_type(self, channel: str) -> str:
        if channel.startswith("CAM_"):
            return ArtifactType.CAMERA_IMAGE

        if channel.startswith("LIDAR_"):
            return ArtifactType.LIDAR_POINTCLOUD

        if channel.startswith("RADAR_"):
            return ArtifactType.RADAR_POINTCLOUD

        return ArtifactType.UNKNOWN
