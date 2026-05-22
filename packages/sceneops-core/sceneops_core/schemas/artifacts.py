from enum import Enum

from pydantic import BaseModel


class ArtifactType(str, Enum):
    CAMERA_IMAGE = "CAMERA_IMAGE"
    LIDAR_POINTCLOUD = "LIDAR_POINTCLOUD"
    RADAR_POINTCLOUD = "RADAR_POINTCLOUD"
    PREDICTION_JSON = "PREDICTION_JSON"
    EVALUATION_JSON = "EVALUATION_JSON"
    UNKNOWN = "UNKNOWN"


class SampleArtifact(BaseModel):
    artifactId: str
    datasetId: str
    datasetVersion: str
    sceneId: str
    sampleId: str
    type: ArtifactType | str
    channel: str | None = None
    uri: str
    downloadUrl: str
