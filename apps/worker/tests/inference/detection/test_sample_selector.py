"""DetectionSampleSelector over canonical Scenes read at pinned revisions."""

from __future__ import annotations

import pytest

from sceneops_core.datasets.schemas import DatasetManifest, DatasetSceneIndexEntry
from sceneops_core.scenes.schemas import scene_id_for
from sceneops_core.scenes.testing import payload_artifact_id, recording_source
from sceneops_worker.inference.detection.sample_selector import (
    DetectionSampleSelector,
    SampleSelectionConfig,
)
from sceneops_worker.scenes.artifacts import SceneManifestIntegrityError
from sceneops_worker.recordings.payload_refs import PayloadIntegrityError


async def _dataset(world, specs: dict[str, dict]) -> DatasetManifest:
    entries = []
    for key, kwargs in specs.items():
        source = recording_source(unit_key=key)
        artifact = await world.publish(world.manifest(source=source, **kwargs))
        entries.append(
            DatasetSceneIndexEntry(
                scene_id=scene_id_for(
                    dataset_id="ds", dataset_version="v1", source=source
                ),
                manifest_artifact_id=artifact.artifact_id,
                manifest_checksum=artifact.checksum,
                manifest_uri=artifact.uri,
            )
        )
    return DatasetManifest(dataset_id="ds", dataset_version="v1", scenes=entries)


def _config(**overrides) -> SampleSelectionConfig:
    defaults = dict(
        dataset_id="ds",
        dataset_version="v1",
        camera_channel="CAM_FRONT",
        enable_3d_lifting=False,
    )
    defaults.update(overrides)
    return SampleSelectionConfig(**defaults)


async def test_selects_one_sample_per_keyframe_with_the_camera_payload(scene_world):
    dataset = await _dataset(
        scene_world, {"a": {}, "b": {"keyframe_timestamps_ns": (1_000,)}}
    )
    samples = await DetectionSampleSelector().select(
        dataset,
        scene_world.scene_artifact_store,
        _config(),
        scene_world.payload_locator,
    )

    assert len(samples) == 3
    first = samples[0]
    assert first.scene_id == dataset.scenes[0].scene_id
    assert first.sample_id == f"{first.scene_id}-keyframe-0000"
    # The image location comes from the payload's ArtifactRecord.
    assert first.image_uri == "file://" + scene_world.payload_uri(
        payload_artifact_id("cam-1000000007")
    )
    # The camera observation keeps its own source time, not the keyframe's.
    assert first.timestamp_ns == 1_000_000_007
    assert first.lidar is None and first.lidar_uri is None


async def test_lidar_observation_attached_when_lifting_enabled(scene_world):
    dataset = await _dataset(scene_world, {"a": {}})
    samples = await DetectionSampleSelector().select(
        dataset,
        scene_world.scene_artifact_store,
        _config(enable_3d_lifting=True),
        scene_world.payload_locator,
    )
    lidar = samples[0].lidar
    assert lidar is not None
    assert lidar.observation.channel == "LIDAR_TOP"
    assert lidar.calibration is not None and lidar.ego_pose is not None
    assert samples[0].lidar_uri == scene_world.payload_uri(
        lidar.observation.payload.artifact_id
    )


async def test_unregistered_payload_artifact_is_refused(scene_world):
    source = recording_source(unit_key="a")
    artifact = await scene_world.publish(
        scene_world.manifest(source=source), with_payloads=False
    )
    dataset = DatasetManifest(
        dataset_id="ds",
        dataset_version="v1",
        scenes=[
            DatasetSceneIndexEntry(
                scene_id=scene_id_for(
                    dataset_id="ds", dataset_version="v1", source=source
                ),
                manifest_artifact_id=artifact.artifact_id,
                manifest_checksum=artifact.checksum,
                manifest_uri=artifact.uri,
            )
        ],
    )
    with pytest.raises(PayloadIntegrityError, match="not registered"):
        await DetectionSampleSelector().select(
            dataset,
            scene_world.scene_artifact_store,
            _config(),
            scene_world.payload_locator,
        )


async def test_skips_keyframes_without_the_camera_channel(scene_world):
    dataset = await _dataset(scene_world, {"a": {"camera_channel": "CAM_BACK"}})
    samples = await DetectionSampleSelector().select(
        dataset,
        scene_world.scene_artifact_store,
        _config(),
        scene_world.payload_locator,
    )
    assert samples == []


async def test_scene_filters_and_global_cap(scene_world):
    dataset = await _dataset(scene_world, {"a": {}, "b": {}, "c": {}})
    selector = DetectionSampleSelector()

    only_b = await selector.select(
        dataset,
        scene_world.scene_artifact_store,
        _config(scene_ids=[dataset.scenes[1].scene_id]),
        scene_world.payload_locator,
    )
    assert {s.scene_id for s in only_b} == {dataset.scenes[1].scene_id}

    first_scene = await selector.select(
        dataset,
        scene_world.scene_artifact_store,
        _config(max_scenes=1),
        scene_world.payload_locator,
    )
    assert {s.scene_id for s in first_scene} == {dataset.scenes[0].scene_id}

    capped = await selector.select(
        dataset,
        scene_world.scene_artifact_store,
        _config(max_samples=3),
        scene_world.payload_locator,
    )
    assert len(capped) == 3

    with pytest.raises(ValueError, match="not found"):
        await selector.select(
            dataset,
            scene_world.scene_artifact_store,
            _config(scene_ids=["scene-x"]),
            scene_world.payload_locator,
        )


async def test_manifest_whose_bytes_changed_is_refused(scene_world):
    dataset = await _dataset(scene_world, {"a": {}})
    entry = dataset.scenes[0]
    dataset.scenes[0] = entry.model_copy(
        update={"manifest_checksum": "sha256:" + "0" * 64}
    )
    with pytest.raises(SceneManifestIntegrityError):
        await DetectionSampleSelector().select(
            dataset,
            scene_world.scene_artifact_store,
            _config(),
            scene_world.payload_locator,
        )
