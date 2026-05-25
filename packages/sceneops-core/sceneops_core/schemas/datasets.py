from __future__ import annotations

from typing import Any
from enum import Enum

from pydantic import BaseModel


class DatasetType(str, Enum):
    NUSCENES = "nuscenes"
    WAYMO = "waymo"
    KITTI = "kitti"
    CUSTOM = "custom"

class DatasetIndexItem(BaseModel):
    datasetId: str
    versions: list[str]


class DatasetVersionManifest(BaseModel):
    datasetId: str
    datasetVersion: str
    source: str | None = None
    status: str
    sceneCount: int
    sampleCount: int
    annotationCount: int
    targetChannels: list[str]


class SceneIndexItem(BaseModel):
    datasetId: str
    datasetVersion: str
    sceneId: str
    sceneToken: str
    source: str | None = None
    description: str
    sampleCount: int
    status: str


class SceneManifest(SceneIndexItem):
    firstSampleToken: str
    lastSampleToken: str
    sampleIds: list[str]


class SensorFrame(BaseModel):
    channel: str
    sampleDataToken: str
    filename: str
    fileformat: str
    isKeyFrame: bool
    width: int | None = None
    height: int | None = None
    calibratedSensor: dict[str, Any]
    egoPose: dict[str, Any]


class Annotation(BaseModel):
    annotationToken: str
    instanceToken: str
    categoryName: str
    translation: list[float]
    size: list[float]
    rotation: list[float]
    numLidarPts: int
    numRadarPts: int
    visibilityToken: str
    attributeTokens: list[str]


class SampleManifest(BaseModel):
    datasetId: str
    datasetVersion: str
    sceneId: str
    sampleId: str
    sampleToken: str
    index: int
    timestamp: int
    prev: str
    next: str
    sensors: dict[str, SensorFrame]
    annotations: list[Annotation]
