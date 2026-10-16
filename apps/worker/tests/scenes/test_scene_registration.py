"""REGISTER_SCENES registrar rules (ADR-015 §6.4; ADR-007 §17-§18, I-10,
I-11, I-24) against real canonical manifest bytes in a local ArtifactStore
and a transaction-staged in-memory store of units, revisions and
DatasetVersion pins. Units and revisions are only added; the recording-scope
rule applies to the DatasetVersion's pins. Real-PostgreSQL transaction,
constraint and locking behavior is covered by
test_scene_registration_integration.py."""

from __future__ import annotations

import copy

import pytest

from sceneops_core.artifacts.schemas import ArtifactKind
from sceneops_core.scenes.schemas import scene_id_for
from sceneops_core.scenes.testing import recording_source
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


# --- one recording scope (§18.3) -------------------------------------------------


async def test_registers_the_complete_scope_and_recomputes_summary(scene_world):
    scene_world.add_dataset_version()
    a = await scene_world.publish(
        scene_world.manifest(source=recording_source(unit_key="a"))
    )
    b = await scene_world.publish(
        scene_world.manifest(
            source=recording_source(unit_key="b"), keyframe_timestamps_ns=(1_000,)
        )
    )

    result = await _register(scene_world, [a, b])

    assert [s.manifest_artifact_id for s in result.scenes] == [
        a.artifact_id,
        b.artifact_id,
    ]
    assert result.created_scene_ids == sorted(s.scene_id for s in result.scenes)
    committed = scene_world.scenes.members()
    assert set(committed) == set(result.created_scene_ids)
    assert len(scene_world.scenes.revisions()) == 2
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
    assert len(scene_world.scenes.members()) == 1
    assert len(scene_world.scenes.revisions()) == 1


async def test_different_revision_conflicts_without_replace_and_changes_nothing(
    scene_world,
):
    scene_world.add_dataset_version()
    first = await scene_world.publish(scene_world.manifest())
    await _register(scene_world, [first])
    before = copy.deepcopy(scene_world.scenes.committed)

    rebuilt = await scene_world.publish(
        scene_world.manifest(build_config={"channels": ["CAM_FRONT"]})
    )
    with pytest.raises(SceneRegistrationConflictError):
        await _register(scene_world, [rebuilt])

    # The new revision was registered inside the rolled-back transaction.
    assert scene_world.scenes.committed == before
    assert scene_world.rollbacks == 1


async def test_replace_repins_the_same_unit_and_keeps_both_revisions(scene_world):
    scene_world.add_dataset_version()
    first = await scene_world.publish(scene_world.manifest())
    created = (await _register(scene_world, [first])).scenes[0]

    rebuilt = await scene_world.publish(
        scene_world.manifest(build_config={"channels": ["CAM_FRONT"]})
    )
    result = await _register(scene_world, [rebuilt], replace=True)

    assert result.replaced_scene_ids == [created.scene_id]
    pinned = scene_world.scenes.members()[created.scene_id]
    assert pinned.manifest_artifact_id == rebuilt.artifact_id
    assert pinned.producer_fingerprint != created.producer_fingerprint
    # The unit keeps both revisions: registration never removes one.
    assert sorted(
        r.manifest_artifact_id for r in scene_world.scenes.revisions().values()
    ) == sorted([first.artifact_id, rebuilt.artifact_id])
    assert first.artifact_id in scene_world.artifacts


async def test_one_revision_is_pinned_by_two_dataset_versions(scene_world):
    """A unit is independent of the DatasetVersion: registering one build
    into a second version adds a pin, not a unit, revision or artifact."""
    scene_world.add_dataset_version(version="v1")
    scene_world.add_dataset_version(version="v2")
    artifact = await scene_world.publish(scene_world.manifest())
    artifacts_before = dict(scene_world.artifacts)

    one = await _register(scene_world, [artifact], version="v1")
    two = await _register(scene_world, [artifact], version="v2")

    assert one.scenes == two.scenes
    assert two.created_scene_ids == one.created_scene_ids
    assert scene_world.scenes.members(version="v1") == scene_world.scenes.members(
        version="v2"
    )
    assert len(scene_world.scenes.revisions()) == 1
    assert scene_world.artifacts == artifacts_before


async def test_a_second_checksum_for_one_fingerprint_is_refused(scene_world):
    """Same unit, same producer fingerprint, other bytes: a non-deterministic
    producer. It is refused even into a DatasetVersion that pins nothing."""
    scene_world.add_dataset_version(version="v1")
    scene_world.add_dataset_version(version="v2")
    first = await scene_world.publish(scene_world.manifest())
    await _register(scene_world, [first], version="v1")
    drifted = await scene_world.publish(
        scene_world.manifest(keyframe_timestamps_ns=(1_000,))
    )
    assert drifted.checksum != first.checksum
    before = copy.deepcopy(scene_world.scenes.committed)

    with pytest.raises(SceneRegistrationConflictError, match="not deterministic"):
        await _register(scene_world, [drifted], version="v2")
    assert scene_world.scenes.committed == before


async def test_a_manifest_artifact_id_must_be_the_revisions_own(scene_world):
    scene_world.add_dataset_version()
    artifact = await scene_world.publish(scene_world.manifest())
    renamed = artifact.model_copy(update={"artifact_id": "art-scene-renamed"})
    scene_world.artifacts[renamed.artifact_id] = renamed
    with pytest.raises(SceneManifestRejectedError, match="is not the revision"):
        await _register(scene_world, [renamed])
    assert scene_world.scenes.revisions(committed=False) == {}


async def test_registration_is_all_or_nothing(scene_world):
    scene_world.add_dataset_version()
    current = [
        await scene_world.publish(
            scene_world.manifest(source=recording_source(unit_key=key))
        )
        for key in ("a", "b")
    ]
    await _register(scene_world, current)
    before = copy.deepcopy(scene_world.scenes.committed)

    rebuilt = [
        await scene_world.publish(
            scene_world.manifest(
                source=recording_source(unit_key=key), build_config={"channels": []}
            )
        )
        for key in ("a", "c")
    ]
    with pytest.raises(SceneRegistrationConflictError):
        await _register(scene_world, rebuilt)

    # Nothing of the rejected set is committed and nothing current is removed.
    assert scene_world.scenes.committed == before


async def test_a_partial_input_never_extends_a_registered_scope(scene_world):
    """The registrar treats its input as the complete set of the scope: a
    second registration with the same fingerprint but fewer or other units
    does not add or remove members (§17.5 rule 4)."""
    scene_world.add_dataset_version()
    a = await scene_world.publish(
        scene_world.manifest(source=recording_source(unit_key="a"))
    )
    b = await scene_world.publish(
        scene_world.manifest(source=recording_source(unit_key="b"))
    )
    await _register(scene_world, [a])
    result = await _register(scene_world, [b])
    assert result.created_scene_ids == []
    assert [s.unit_key for s in scene_world.scenes.members().values()] == ["a"]


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
    seg0 = scene_id_for(_segment("seg-0", 0, 1))
    seg1 = scene_id_for(_segment("seg-1", 0, 1))
    assert replaced.replaced_scene_ids == [seg0]
    assert replaced.removed_scene_ids == [seg1]
    assert len(replaced.created_scene_ids) == 1
    scope = scene_world.scenes.members()
    assert seg1 not in scope
    assert len({s.producer_fingerprint for s in scope.values()}) == 1
    assert _summary(scene_world)["scene_count"] == 2
    # Unpinned, not deleted: seg-1's revision is still registered.
    assert seg1 in {s for s, _ in scene_world.scenes.revisions()}


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

    assert scene_world.scenes.committed == ({}, {})


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

    not_a_scene = await scene_world.publish(
        scene_world.manifest(), kind=ArtifactKind.EPISODE_MANIFEST
    )
    with pytest.raises(SceneManifestRejectedError, match="not a canonical"):
        await _register(scene_world, [not_a_scene])

    tampered = await scene_world.publish(
        scene_world.manifest(source=recording_source(unit_key="t"))
    )
    data = await scene_world.artifact_store.read_bytes(tampered.uri)
    await scene_world.artifact_store.write_bytes(
        tampered.uri, data.replace(b"CAM_FRONT", b"CAM_FRONX")
    )
    with pytest.raises(SceneManifestRejectedError, match="checksum"):
        await _register(scene_world, [tampered])
    assert scene_world.scenes.committed == ({}, {})


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
    assert scene_world.scenes.working == ({}, {})


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
    assert scene_world.scenes.working == ({}, {})


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
    assert scene_world.scenes.committed == ({}, {})
