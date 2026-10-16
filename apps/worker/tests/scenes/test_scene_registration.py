"""REGISTER_SCENES registrar rules (ADR-015 §6.4, MS-7) against real
canonical manifest bytes in a local ArtifactStore and a transaction-staged
in-memory store of units, revisions and DatasetVersion pins. Registration
adds immutable revisions after the durability and integrity gate and
returns their complete pins; it never reads or writes a DatasetVersion.
Real-PostgreSQL / MinIO transaction, constraint and concurrency behavior is
covered by test_scene_registration_integration.py."""

from __future__ import annotations

import copy

import pytest

from sceneops_core.artifacts.schemas import ArtifactKind
from sceneops_core.provenance import RevisionDecision, RevisionPin
from sceneops_core.scenes.testing import recording_source
from sceneops_worker.scenes.registration import (
    SceneManifestRejectedError,
    SceneRegistrationConflictError,
    SceneRegistrationScopeError,
    register_scenes,
)

RECORDING_CHECKSUM = "sha256:" + "1" * 64


async def _register(world, artifacts):
    return await register_scenes(
        context=world.context,
        manifest_artifact_ids=[a.artifact_id for a in artifacts],
    )


def _decisions(registration) -> dict[str, RevisionDecision]:
    return {r.revision.scene_id: r.decision for r in registration.revisions}


# --- revisions -----------------------------------------------------------------


async def test_registers_revisions_and_returns_their_complete_pins(scene_world):
    a = await scene_world.publish(
        scene_world.manifest(source=recording_source(unit_key="a"))
    )
    b = await scene_world.publish(
        scene_world.manifest(
            source=recording_source(unit_key="b"), keyframe_timestamps_ns=(1_000,)
        )
    )

    result = await _register(scene_world, [b, a])

    committed = scene_world.scenes.revisions()
    assert len(committed) == 2
    # One pin per input, in unit id order, each naming a committed revision
    # with exactly its manifest artifact and checksum.
    assert [p.unit_id for p in result.pins] == sorted(s for s, _ in committed)
    for pin in result.pins:
        assert isinstance(pin, RevisionPin)
        assert pin.domain == "scene"
        record = committed[(pin.unit_id, pin.manifest_checksum)]
        assert pin.manifest_artifact_id == record.manifest_artifact_id
        assert (
            pin.manifest_checksum
            == scene_world.artifacts[pin.manifest_artifact_id].checksum
        )
    assert {p.manifest_artifact_id for p in result.pins} == {
        a.artifact_id,
        b.artifact_id,
    }
    assert set(_decisions(result).values()) == {RevisionDecision.NEW_UNIT}
    assert result.robot_run_id == "run-001"
    assert scene_world.commits == 1


async def test_registration_never_reads_or_writes_a_dataset_version(scene_world):
    artifact = await scene_world.publish(scene_world.manifest())
    scene_world.context.dataset_store.reset_mock()

    await _register(scene_world, [artifact])

    assert scene_world.context.dataset_store.mock_calls == []
    assert scene_world.scenes.committed[1] == {}


async def test_identical_retry_converges_on_the_registered_revision(scene_world):
    artifact = await scene_world.publish(scene_world.manifest())
    first = await _register(scene_world, [artifact])
    before = copy.deepcopy(scene_world.scenes.committed)

    again = await _register(scene_world, [artifact])

    assert again.pins == first.pins
    assert set(_decisions(again).values()) == {RevisionDecision.EXISTING_REVISION}
    assert scene_world.scenes.committed == before


async def test_a_new_fingerprint_adds_a_revision_and_keeps_the_first(scene_world):
    first = await scene_world.publish(scene_world.manifest())
    (created,) = (await _register(scene_world, [first])).pins

    rebuilt = await scene_world.publish(
        scene_world.manifest(build_config={"channels": ["CAM_FRONT"]})
    )
    result = await _register(scene_world, [rebuilt])

    (pin,) = result.pins
    assert pin.unit_id == created.unit_id
    assert pin.manifest_checksum != created.manifest_checksum
    assert _decisions(result) == {pin.unit_id: RevisionDecision.NEW_REVISION}
    assert sorted(
        r.manifest_artifact_id for r in scene_world.scenes.revisions().values()
    ) == sorted([first.artifact_id, rebuilt.artifact_id])


async def test_a_new_revision_never_repoints_an_existing_pin(scene_world):
    first = await scene_world.publish(scene_world.manifest())
    (registered,) = (await _register(scene_world, [first])).revisions
    scene_world.scenes.pin([registered.pin])

    rebuilt = await scene_world.publish(
        scene_world.manifest(build_config={"channels": ["CAM_FRONT"]})
    )
    await _register(scene_world, [rebuilt])

    pinned = scene_world.scenes.members()[registered.revision.scene_id]
    assert pinned.manifest_artifact_id == first.artifact_id


async def test_a_second_checksum_for_one_fingerprint_is_refused(scene_world):
    """Same unit, same producer fingerprint, other bytes: a non-deterministic
    producer."""
    first = await scene_world.publish(scene_world.manifest())
    await _register(scene_world, [first])
    drifted = await scene_world.publish(
        scene_world.manifest(keyframe_timestamps_ns=(1_000,))
    )
    assert drifted.checksum != first.checksum
    before = copy.deepcopy(scene_world.scenes.committed)

    with pytest.raises(SceneRegistrationConflictError, match="not deterministic"):
        await _register(scene_world, [drifted])
    assert scene_world.scenes.committed == before
    assert scene_world.rollbacks == 1


async def test_registration_is_all_or_nothing(scene_world):
    a = await scene_world.publish(
        scene_world.manifest(source=recording_source(unit_key="a"))
    )
    await _register(scene_world, [a])
    before = copy.deepcopy(scene_world.scenes.committed)

    # "c" is new and valid, "a" drifted: neither is committed.
    batch = [
        await scene_world.publish(
            scene_world.manifest(
                source=recording_source(unit_key=key), keyframe_timestamps_ns=(7,)
            )
        )
        for key in ("a", "c")
    ]
    with pytest.raises(SceneRegistrationConflictError):
        await _register(scene_world, batch)

    assert scene_world.scenes.committed == before


async def test_separate_builds_register_independently(scene_world):
    a = await scene_world.publish(
        scene_world.manifest(source=recording_source(unit_key="a"))
    )
    b = await scene_world.publish(
        scene_world.manifest(source=recording_source(unit_key="b"))
    )
    await _register(scene_world, [a])

    result = await _register(scene_world, [b])

    assert set(_decisions(result).values()) == {RevisionDecision.NEW_UNIT}
    assert len(scene_world.scenes.revisions()) == 2


async def test_a_manifest_artifact_id_must_be_the_revisions_own(scene_world):
    artifact = await scene_world.publish(scene_world.manifest())
    renamed = artifact.model_copy(update={"artifact_id": "art-scene-renamed"})
    scene_world.artifacts[renamed.artifact_id] = renamed
    with pytest.raises(SceneManifestRejectedError, match="is not the revision"):
        await _register(scene_world, [renamed])
    assert scene_world.scenes.revisions(committed=False) == {}


# --- one build of a registered RobotRun (S2) ---------------------------------------


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


async def test_input_must_be_one_build_of_one_robot_run(scene_world):
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
    artifacts = await _recording_build(scene_world, [_segment("seg-0", 0, 1_000)])
    with pytest.raises(SceneRegistrationScopeError, match="not registered"):
        await _register(scene_world, artifacts)

    scene_world.add_robot_run("run-1", recording_checksum="sha256:" + "9" * 64)
    with pytest.raises(SceneRegistrationScopeError, match="recording bytes"):
        await _register(scene_world, artifacts)


async def test_rejects_duplicate_inputs_and_units(scene_world):
    artifact = await scene_world.publish(scene_world.manifest())
    with pytest.raises(SceneRegistrationScopeError, match="duplicates"):
        await _register(scene_world, [artifact, artifact])

    twin = await scene_world.publish(
        scene_world.manifest(build_config={"channels": ["CAM_FRONT"]})
    )
    with pytest.raises(SceneRegistrationScopeError, match="more than one manifest"):
        await _register(scene_world, [artifact, twin])
    assert scene_world.scenes.committed == ({}, {})


# --- durability and integrity gate (S1): manifest ----------------------------------


async def test_rejects_unverifiable_manifests(scene_world):
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


async def test_rejects_a_manifest_missing_from_the_store(scene_world):
    artifact = await scene_world.publish(scene_world.manifest())
    await scene_world.artifact_store.delete_prefix(artifact.uri)
    with pytest.raises(SceneManifestRejectedError, match="not found"):
        await _register(scene_world, [artifact])
    assert scene_world.scenes.committed == ({}, {})


async def test_rejects_non_canonical_manifest_bytes(scene_world):
    import json

    from sceneops_core.artifacts.schemas import ArtifactRecord
    from sceneops_core.common.checksums import sha256_checksum

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
            context=scene_world.context, manifest_artifact_ids=["art-pretty"]
        )


# --- durability and integrity gate (S1): payloads ----------------------------------


async def test_rejects_unregistered_payload_artifacts(scene_world):
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
    manifest = scene_world.manifest()
    artifact = await scene_world.publish(manifest)
    payload_id = manifest.observations[0].payload.artifact_id
    scene_world.artifacts[payload_id] = scene_world.artifacts[payload_id].model_copy(
        update={field: value}
    )
    with pytest.raises(SceneManifestRejectedError, match=message):
        await _register(scene_world, [artifact])
    assert scene_world.scenes.working == ({}, {})


async def test_a_payload_record_without_its_object_is_rejected(scene_world):
    """The gate, not the ArtifactRecord, decides: a registered payload whose
    object is missing from the store fails registration."""
    manifest = scene_world.manifest()
    artifact = await scene_world.publish(manifest)
    payload = scene_world.artifacts[manifest.observations[0].payload.artifact_id]
    await scene_world.artifact_store.delete_prefix(payload.uri)

    with pytest.raises(SceneManifestRejectedError, match="has no object"):
        await _register(scene_world, [artifact])
    assert scene_world.scenes.working == ({}, {})


async def test_a_payload_object_of_another_size_is_rejected(scene_world):
    manifest = scene_world.manifest()
    artifact = await scene_world.publish(manifest)
    payload = scene_world.artifacts[manifest.observations[0].payload.artifact_id]
    await scene_world.artifact_store.write_bytes(payload.uri, b"x")

    with pytest.raises(SceneManifestRejectedError, match="1 bytes, recorded"):
        await _register(scene_world, [artifact])
    assert scene_world.scenes.working == ({}, {})


async def test_payload_objects_are_checked_with_one_listing_per_prefix(scene_world):
    from unittest.mock import MagicMock

    store = scene_world.artifact_store
    scene_world.context.artifact_store = MagicMock(spec=store, wraps=store)
    manifest = scene_world.manifest()
    assert len(manifest.observations) > 1
    artifact = await scene_world.publish(manifest)

    await _register(scene_world, [artifact])

    listings = scene_world.context.artifact_store.list_objects.call_args_list
    assert [c.args for c in listings] == [(f"{scene_world.root}/payloads",)]


async def test_payload_location_does_not_affect_the_revision(scene_world):
    """Bytes stored under another key (and a record that says so) register
    the same revision: the manifest names payloads by id, not location."""
    manifest = scene_world.manifest()
    artifact = await scene_world.publish(manifest)
    for observation in manifest.observations:
        payload_id = observation.payload.artifact_id
        record = scene_world.artifacts[payload_id]
        moved = f"{scene_world.root}/moved/{payload_id}"
        await scene_world.artifact_store.write_bytes(
            moved, await scene_world.artifact_store.read_bytes(record.uri)
        )
        scene_world.artifacts[payload_id] = record.model_copy(update={"uri": moved})

    result = await _register(scene_world, [artifact])

    assert [p.manifest_artifact_id for p in result.pins] == [artifact.artifact_id]
