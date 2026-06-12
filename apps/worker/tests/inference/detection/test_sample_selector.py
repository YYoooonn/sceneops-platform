"""Tests for DetectionSampleSelector."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from sceneops_core.datasets.schemas import DatasetManifest, DatasetSceneIndexEntry
from sceneops_worker.inference.detection.sample_selector import (
    DetectionSampleSelector,
    SampleSelectionConfig,
)


# ── helpers ───────────────────────────────────────────────────────────────────

RAW_ROOT = "/data/raw/nuscenes"
DATASET_ID = "nuscenes"
DATASET_VERSION = "v1.0-mini"


def _scene_entry(scene_id: str) -> DatasetSceneIndexEntry:
    return DatasetSceneIndexEntry(
        scene_id=scene_id,
        scene_manifest_uri=f"file:///data/scenes/{scene_id}.json",
    )


def _sensor_frame(channel: str, uri: str) -> MagicMock:
    sf = MagicMock()
    sf.channel = channel
    sf.uri = uri
    return sf


def _sample(sample_id: str, scene_id: str, channels: dict[str, str]) -> MagicMock:
    s = MagicMock()
    s.sample_id = sample_id
    s.scene_id = scene_id
    s.timestamp_us = 1000
    s.sensor_frames = [_sensor_frame(ch, uri) for ch, uri in channels.items()]
    return s


def _scene_manifest(scene_id: str, samples: list) -> MagicMock:
    m = MagicMock()
    m.scene_id = scene_id
    m.samples = samples
    return m


def _dataset_manifest(scene_ids: list[str]) -> DatasetManifest:
    return DatasetManifest(
        dataset_id=DATASET_ID,
        dataset_version=DATASET_VERSION,
        scenes=[_scene_entry(sid) for sid in scene_ids],
    )


def _config(**overrides) -> SampleSelectionConfig:
    defaults = dict(
        dataset_id=DATASET_ID,
        dataset_version=DATASET_VERSION,
        camera_channel="CAM_FRONT",
        raw_source_root_uri=RAW_ROOT,
        enable_3d_lifting=False,
    )
    defaults.update(overrides)
    return SampleSelectionConfig(**defaults)


def _store_with_scenes(scenes: dict[str, list]) -> MagicMock:
    """Return a mock SceneArtifactStore that serves the given scene_id→samples map."""

    async def load_scene_manifest(uri: str):
        for scene_id, samples in scenes.items():
            if scene_id in uri:
                return _scene_manifest(scene_id, samples)
        return None

    store = MagicMock()
    store.load_scene_manifest = AsyncMock(side_effect=load_scene_manifest)
    return store


# ── basic selection ───────────────────────────────────────────────────────────


async def test_selects_samples_with_camera_channel():
    store = _store_with_scenes(
        {
            "scene-1": [
                _sample(
                    "s1",
                    "scene-1",
                    {
                        "CAM_FRONT": "samples/CAM_FRONT/a.jpg",
                        "LIDAR_TOP": "lidar/a.bin",
                    },
                ),
            ],
        }
    )
    manifest = _dataset_manifest(["scene-1"])
    selector = DetectionSampleSelector()
    result = await selector.select(manifest, store, _config())

    assert len(result) == 1
    assert result[0].sample_id == "s1"
    assert result[0].image_uri == f"file://{RAW_ROOT}/samples/CAM_FRONT/a.jpg"
    assert result[0].camera_channel == "CAM_FRONT"


async def test_skips_sample_with_missing_camera_channel():
    store = _store_with_scenes(
        {
            "scene-1": [
                _sample("s1", "scene-1", {"LIDAR_TOP": "lidar/a.bin"}),  # no CAM_FRONT
                _sample("s2", "scene-1", {"CAM_FRONT": "samples/CAM_FRONT/b.jpg"}),
            ],
        }
    )
    manifest = _dataset_manifest(["scene-1"])
    selector = DetectionSampleSelector()
    result = await selector.select(manifest, store, _config())

    assert len(result) == 1
    assert result[0].sample_id == "s2"


# ── scene_ids filtering ───────────────────────────────────────────────────────


async def test_filters_by_scene_ids():
    store = _store_with_scenes(
        {
            "scene-1": [_sample("s1", "scene-1", {"CAM_FRONT": "a.jpg"})],
            "scene-2": [_sample("s2", "scene-2", {"CAM_FRONT": "b.jpg"})],
        }
    )
    manifest = _dataset_manifest(["scene-1", "scene-2"])
    selector = DetectionSampleSelector()
    result = await selector.select(manifest, store, _config(scene_ids=["scene-1"]))

    assert len(result) == 1
    assert result[0].scene_id == "scene-1"


async def test_fails_fast_on_unknown_scene_id():
    manifest = _dataset_manifest(["scene-1"])
    store = _store_with_scenes({})
    selector = DetectionSampleSelector()

    with pytest.raises(ValueError, match="not found in dataset manifest"):
        await selector.select(manifest, store, _config(scene_ids=["scene-99"]))


# ── max_scenes ────────────────────────────────────────────────────────────────


async def test_applies_max_scenes():
    store = _store_with_scenes(
        {
            "scene-1": [_sample("s1", "scene-1", {"CAM_FRONT": "a.jpg"})],
            "scene-2": [_sample("s2", "scene-2", {"CAM_FRONT": "b.jpg"})],
            "scene-3": [_sample("s3", "scene-3", {"CAM_FRONT": "c.jpg"})],
        }
    )
    manifest = _dataset_manifest(["scene-1", "scene-2", "scene-3"])
    selector = DetectionSampleSelector()
    result = await selector.select(manifest, store, _config(max_scenes=2))

    assert len(result) == 2
    assert {r.scene_id for r in result} == {"scene-1", "scene-2"}


# ── max_samples ───────────────────────────────────────────────────────────────


async def test_applies_max_samples_globally():
    store = _store_with_scenes(
        {
            "scene-1": [
                _sample("s1", "scene-1", {"CAM_FRONT": "a.jpg"}),
                _sample("s2", "scene-1", {"CAM_FRONT": "b.jpg"}),
            ],
            "scene-2": [
                _sample("s3", "scene-2", {"CAM_FRONT": "c.jpg"}),
            ],
        }
    )
    manifest = _dataset_manifest(["scene-1", "scene-2"])
    selector = DetectionSampleSelector()
    result = await selector.select(manifest, store, _config(max_samples=2))

    assert len(result) == 2


# ── lidar_uri resolution ──────────────────────────────────────────────────────


async def test_lidar_uri_resolved_when_enable_3d_lifting():
    store = _store_with_scenes(
        {
            "scene-1": [
                _sample(
                    "s1",
                    "scene-1",
                    {
                        "CAM_FRONT": "samples/CAM_FRONT/a.jpg",
                        "LIDAR_TOP": "samples/LIDAR_TOP/a.bin",
                    },
                ),
            ],
        }
    )
    manifest = _dataset_manifest(["scene-1"])
    selector = DetectionSampleSelector()
    result = await selector.select(manifest, store, _config(enable_3d_lifting=True))

    assert result[0].lidar_uri == f"file://{RAW_ROOT}/samples/LIDAR_TOP/a.bin"
    assert result[0].lidar_sensor_frame is not None


async def test_lidar_uri_omitted_when_3d_lifting_disabled():
    store = _store_with_scenes(
        {
            "scene-1": [
                _sample(
                    "s1",
                    "scene-1",
                    {
                        "CAM_FRONT": "samples/CAM_FRONT/a.jpg",
                        "LIDAR_TOP": "samples/LIDAR_TOP/a.bin",
                    },
                ),
            ],
        }
    )
    manifest = _dataset_manifest(["scene-1"])
    selector = DetectionSampleSelector()
    result = await selector.select(manifest, store, _config(enable_3d_lifting=False))

    assert result[0].lidar_uri is None
    assert result[0].lidar_sensor_frame is None
