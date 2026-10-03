"""REGISTER_SCENES registrar rules (ADR-007 §17-§18, I-1, I-10-I-12, I-24)
against real canonical manifest bytes in a local ArtifactStore and a
transaction-staged in-memory SceneStore. Real-PostgreSQL transaction and
locking behavior is covered by test_scene_registration_integration.py."""

from __future__ import annotations

import pytest

from sceneops_core.artifacts.schemas import ArtifactKind
from sceneops_core.scenes.schemas import scene_id_for
from sceneops_core.scenes.testing import external_source, recording_source
from sceneops_worker.scenes.registration import (
    SceneManifestRejectedError,
    SceneRegistrationConflictError,
    SceneRegistrationScopeError,
    register_scenes,
)

RECORDING_CHECKSUM = "sha256:" + "1" * 64


async def _register(world, artifacts, *, replace=False, dataset_id="ds", version="v1"):
    return await register_scenes(
        context=world.context,
        dataset_id=dataset_id,
        dataset_version=version,
        manifest_artifact_ids=[a.artifact_id for a in artifacts],
        replace=replace,
    )


def _summary(world, dataset_id="ds", version="v1"):
    return world.summaries[(dataset_id, version)]


# --- external units (§18.2) -----------------------------------------------------


async def test_registers_external_units_and_recomputes_summary(scene_world):
    scene_world.add_dataset_version()
    a = await scene_world.publish(
        scene_world.manifest(source=external_source(source_unit_key="a"))
    )
    b = await scene_world.publish(
        scene_world.manifest(
            source=external_source(source_unit_key="b"), keyframe_timestamps_ns=(1_000,)
        )
    )

    result = await _register(scene_world, [a, b])

    assert [s.manifest_artifact_id for s in result.scenes] == [
        a.artifact_id,
        b.artifact_id,
    ]
    assert result.created_scene_ids == [s.scene_id for s in result.scenes]
    committed = scene_world.scenes.committed
    assert set(committed) == set(result.created_scene_ids)
    for record in committed.values():
        assert (
            record.manifest_checksum
            == scene_world.artifacts[record.manifest_artifact_id].checksum
        )
    assert _summary(scene_world) == {
        "scene_count": 2,
        "keyframe_count": 3,
        "observation_count": 7,
        "observed_channels": ["CAM_FRONT", "LIDAR_TOP"],
    }
    assert scene_world.commits == 1


async def test_identical_retry_is_a_no_op(scene_world):
    scene_world.add_dataset_version()
    artifact = await scene_world.publish(scene_world.manifest())
    await _register(scene_world, [artifact])

    again = await _register(scene_world, [artifact])

    assert again.created_scene_ids == []
    assert again.unchanged_scene_ids == [again.scenes[0].scene_id]
    assert len(scene_world.scenes.committed) == 1


async def test_different_revision_conflicts_without_replace_and_changes_nothing(
    scene_world,
):
    scene_world.add_dataset_version()
    first = await scene_world.publish(scene_world.manifest())
    await _register(scene_world, [first])
    before = dict(scene_world.scenes.committed)

    rebuilt = await scene_world.publish(
        scene_world.manifest(build_config={"channels": ["CAM_FRONT"]})
    )
    with pytest.raises(SceneRegistrationConflictError):
        await _register(scene_world, [rebuilt])

    assert scene_world.scenes.committed == before
    assert scene_world.rollbacks == 1


async def test_replace_repoints_the_same_identity(scene_world):
    scene_world.add_dataset_version()
    first = await scene_world.publish(scene_world.manifest())
    created = (await _register(scene_world, [first])).scenes[0]

    rebuilt = await scene_world.publish(
        scene_world.manifest(build_config={"channels": ["CAM_FRONT"]})
    )
    result = await _register(scene_world, [rebuilt], replace=True)

    assert result.replaced_scene_ids == [created.scene_id]
    current = scene_world.scenes.committed[created.scene_id]
    assert current.manifest_artifact_id == rebuilt.artifact_id
    assert current.producer_fingerprint != created.producer_fingerprint
    # The superseded revision stays available for lineage.
    assert first.artifact_id in scene_world.artifacts


async def test_registration_is_all_or_nothing(scene_world):
    scene_world.add_dataset_version()
    existing = await scene_world.publish(
        scene_world.manifest(source=external_source(source_unit_key="b"))
    )
    await _register(scene_world, [existing])
    before = dict(scene_world.scenes.committed)

    new_unit = await scene_world.publish(
        scene_world.manifest(source=external_source(source_unit_key="a"))
    )
    conflicting = await scene_world.publish(
        scene_world.manifest(
            source=external_source(source_unit_key="b"), build_config={"channels": []}
        )
    )
    with pytest.raises(SceneRegistrationConflictError):
        await _register(scene_world, [new_unit, conflicting])

    # The new unit processed before the conflict is not committed.
    assert scene_world.scenes.committed == before


async def test_same_source_in_two_dataset_versions_is_two_scenes(scene_world):
    scene_world.add_dataset_version("ds", "v1")
    scene_world.add_dataset_version("ds", "v2")
    manifest = scene_world.manifest()
    in_v1 = await scene_world.publish(manifest, version="v1")
    in_v2 = await scene_world.publish(manifest, version="v2")

    a = (await _register(scene_world, [in_v1], version="v1")).scenes[0]
    b = (await _register(scene_world, [in_v2], version="v2")).scenes[0]

    assert a.scene_id != b.scene_id
    assert a.manifest_checksum == b.manifest_checksum


# --- recording scope (§18.3) ----------------------------------------------------


def _segment(unit_key, start, end, run_id="run-1"):
    return recording_source(
        robot_run_id=run_id,
        unit_key=unit_key,
        start_timestamp_ns=start,
        end_timestamp_ns=end,
        recording_checksum=RECORDING_CHECKSUM,
    )


async def _recording_build(world, segments, *, build_config=None):
    return [
        await world.publish(
            world.manifest(
                source=source,
                build_config=build_config,
                keyframe_timestamps_ns=(source.start_timestamp_ns + 100,),
            )
        )
        for source in segments
    ]


async def test_recording_scope_lifecycle(scene_world):
    scene_world.add_dataset_version()
    scene_world.add_robot_run("run-1", recording_checksum=RECORDING_CHECKSUM)

    first = await _recording_build(
        scene_world, [_segment("seg-0", 0, 1_000), _segment("seg-1", 1_000, 2_000)]
    )
    created = await _register(scene_world, first)
    assert len(created.created_scene_ids) == 2

    # Same fingerprint: the current complete set is reused.
    again = await _register(scene_world, first)
    assert sorted(again.unchanged_scene_ids) == sorted(created.created_scene_ids)

    # A different build of the same recording conflicts without replace.
    rebuilt = await _recording_build(
        scene_world,
        [_segment("seg-0", 0, 1_500), _segment("seg-x", 1_500, 2_000)],
        build_config={"window_ms": 1500},
    )
    with pytest.raises(SceneRegistrationConflictError):
        await _register(scene_world, rebuilt)

    # With replace the scope is swapped atomically: reused key repointed,
    # absent key removed, new key inserted, one fingerprint remains.
    replaced = await _register(scene_world, rebuilt, replace=True)
    seg0 = scene_id_for(
        dataset_id="ds", dataset_version="v1", source=_segment("seg-0", 0, 1)
    )
    seg1 = scene_id_for(
        dataset_id="ds", dataset_version="v1", source=_segment("seg-1", 0, 1)
    )
    assert replaced.replaced_scene_ids == [seg0]
    assert replaced.removed_scene_ids == [seg1]
    assert len(replaced.created_scene_ids) == 1
    scope = scene_world.scenes.committed.values()
    assert seg1 not in scene_world.scenes.committed
    assert len({s.producer_fingerprint for s in scope}) == 1
    assert _summary(scene_world)["scene_count"] == 2


async def test_recording_scope_rejects_mixed_runs_and_fingerprints(scene_world):
    scene_world.add_dataset_version()
    scene_world.add_robot_run("run-1", recording_checksum=RECORDING_CHECKSUM)
    scene_world.add_robot_run("run-2", recording_checksum=RECORDING_CHECKSUM)

    one = await _recording_build(scene_world, [_segment("seg-0", 0, 1_000)])
    other_run = await _recording_build(
        scene_world, [_segment("seg-0", 0, 1_000, run_id="run-2")]
    )
    with pytest.raises(SceneRegistrationScopeError, match="exactly one RobotRun"):
        await _register(scene_world, one + other_run)

    other_config = await _recording_build(
        scene_world, [_segment("seg-1", 1_000, 2_000)], build_config={"x": 1}
    )
    with pytest.raises(SceneRegistrationScopeError, match="one producer fingerprint"):
        await _register(scene_world, one + other_config)

    external = await scene_world.publish(scene_world.manifest())
    with pytest.raises(SceneRegistrationScopeError, match="single source kind"):
        await _register(scene_world, one + [external])
    assert scene_world.scenes.committed == {}


async def test_recording_scene_requires_the_registered_recording_bytes(scene_world):
    scene_world.add_dataset_version()
    artifacts = await _recording_build(scene_world, [_segment("seg-0", 0, 1_000)])
    with pytest.raises(SceneRegistrationScopeError, match="not registered"):
        await _register(scene_world, artifacts)

    scene_world.add_robot_run("run-1", recording_checksum="sha256:" + "9" * 64)
    with pytest.raises(SceneRegistrationScopeError, match="recording bytes"):
        await _register(scene_world, artifacts)


# --- manifest verification (S1) ------------------------------------------------


async def test_rejects_unverifiable_inputs(scene_world):
    scene_world.add_dataset_version()
    with pytest.raises(SceneManifestRejectedError, match="not found"):
        await _register(scene_world, [type("A", (), {"artifact_id": "art-missing"})()])

    legacy = await scene_world.publish(
        scene_world.manifest(), kind=ArtifactKind.LEGACY_SCENE_MANIFEST
    )
    with pytest.raises(SceneManifestRejectedError, match="not a canonical"):
        await _register(scene_world, [legacy])

    tampered = await scene_world.publish(
        scene_world.manifest(source=external_source(source_unit_key="t"))
    )
    data = await scene_world.artifact_store.read_bytes(tampered.uri)
    await scene_world.artifact_store.write_bytes(
        tampered.uri, data.replace(b"CAM_FRONT", b"CAM_FRONX")
    )
    with pytest.raises(SceneManifestRejectedError, match="checksum"):
        await _register(scene_world, [tampered])
    assert scene_world.scenes.committed == {}


async def test_rejects_non_canonical_manifest_bytes(scene_world):
    import json

    from sceneops_core.artifacts.schemas import ArtifactRecord
    from sceneops_core.common.checksums import sha256_checksum

    scene_world.add_dataset_version()
    manifest = scene_world.manifest()
    pretty = json.dumps(json.loads(manifest.to_canonical_bytes()), indent=2).encode()
    uri = f"{scene_world.root}/pretty.json"
    await scene_world.artifact_store.write_bytes(uri, pretty)
    scene_world.artifacts["art-pretty"] = ArtifactRecord(
        artifact_id="art-pretty",
        kind=ArtifactKind.SCENE_MANIFEST,
        uri=uri,
        size_bytes=len(pretty),
        checksum=sha256_checksum(pretty),
    )
    with pytest.raises(SceneManifestRejectedError, match="canonical"):
        await register_scenes(
            context=scene_world.context,
            dataset_id="ds",
            dataset_version="v1",
            manifest_artifact_ids=["art-pretty"],
        )


async def test_rejects_unregistered_payload_artifacts(scene_world):
    scene_world.add_dataset_version()
    artifact = await scene_world.publish(scene_world.manifest(), with_payloads=False)
    with pytest.raises(SceneManifestRejectedError, match="not registered"):
        await _register(scene_world, [artifact])
    assert scene_world.scenes.working == {}


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("kind", ArtifactKind.SCENE_MANIFEST, "is a"),
        ("checksum", "sha256:" + "0" * 64, "checksum"),
        ("size_bytes", 1, "size_bytes"),
        ("media_type", "image/png", "media_type"),
    ],
)
async def test_payload_ref_must_match_its_artifact_record_exactly(
    scene_world, field, value, message
):
    """The registrar checks every payload reference against the
    ArtifactRecord's kind and exact integrity metadata; a location is never
    consulted."""
    scene_world.add_dataset_version()
    manifest = scene_world.manifest()
    artifact = await scene_world.publish(manifest)
    payload_id = manifest.observations[0].payload.artifact_id
    scene_world.artifacts[payload_id] = scene_world.artifacts[payload_id].model_copy(
        update={field: value}
    )
    with pytest.raises(SceneManifestRejectedError, match=message):
        await _register(scene_world, [artifact])
    assert scene_world.scenes.working == {}


async def test_payload_location_does_not_affect_registration(scene_world):
    """Moving payload bytes (a different ArtifactRecord uri) changes neither
    the manifest nor its registration."""
    scene_world.add_dataset_version()
    manifest = scene_world.manifest()
    artifact = await scene_world.publish(manifest)
    for observation in manifest.observations:
        payload_id = observation.payload.artifact_id
        scene_world.artifacts[payload_id] = scene_world.artifacts[
            payload_id
        ].model_copy(update={"uri": f"s3://another-bucket/moved/{payload_id}"})
    result = await _register(scene_world, [artifact])
    assert len(result.created_scene_ids) == 1


async def test_rejects_duplicates_and_missing_dataset_version(scene_world):
    artifact = await scene_world.publish(scene_world.manifest())
    with pytest.raises(SceneRegistrationScopeError, match="duplicates"):
        await _register(scene_world, [artifact, artifact])

    twin = await scene_world.publish(
        scene_world.manifest(build_config={"channels": ["CAM_FRONT"]})
    )
    with pytest.raises(SceneRegistrationScopeError, match="more than one manifest"):
        await _register(scene_world, [artifact, twin])

    with pytest.raises(SceneRegistrationScopeError, match="DatasetVersion not found"):
        await _register(scene_world, [artifact])
    assert scene_world.scenes.committed == {}
