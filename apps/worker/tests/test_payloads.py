"""Verified payload access (ADR-016, ADR-017): a canonical ``PayloadRef`` is
read as it is, the same way for every source, over a real local store where
the producers publish; corrupted bytes fail the read, and a consumer that
decodes whole objects refuses a selector instead of reading the whole object."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from sceneops_core.artifacts.schemas import PayloadRef, VideoFrameSelector
from sceneops_core.common.checksums import sha256_checksum
from sceneops_core.config import ArtifactSettings
from sceneops_core.sources import SnapshotDeclaration, SnapshotSelection
from sceneops_recording.observations.store import (
    ObservationPayloadStore,
    observation_payload_uri,
)
from sceneops_sources import (
    content_file_payload_ref,
    content_file_uri,
    publish_snapshot,
)
from sceneops_storage import LocalArtifactStore, PayloadVerificationError
from sceneops_worker.payloads import (
    UnsupportedPayloadSelectorError,
    payload_reader,
    require_whole_payloads,
)

RUN = "run-001"
PAYLOAD_ID = "payload-0123456789abcdef0123456789abcdef"
PAYLOAD = b"\x00\x01point cloud\xfe\xff"
LIDAR_PATH = "samples/LIDAR_TOP/a+b?c#d%e é.pcd.bin"
LIDAR = b"\x10\x20" * 40
CDR = "application/x.ros2-cdr.sensor_msgs.msg.pointcloud2"


class _World:
    def __init__(self, tmp_path) -> None:
        self.tmp_path = tmp_path
        self.root = str(tmp_path / "artifacts")
        self.settings = ArtifactSettings(root_uri=self.root)
        self.store = LocalArtifactStore(root_uri=self.root)
        self.context = SimpleNamespace(
            settings=SimpleNamespace(artifact=self.settings),
            artifact_store=self.store,
        )

    async def recording_payload(self) -> tuple[PayloadRef, str]:
        """A payload published as the recording builders publish it, and the
        reference they emit for it; returns (ref, stored uri)."""
        store = ObservationPayloadStore(
            artifact_store=self.store,
            payload_root_uri=self.settings.observation_payload_root_uri,
        )
        stored, _ = await store.publish(
            robot_run_id=RUN,
            artifact_id=PAYLOAD_ID,
            data=PAYLOAD,
            checksum=sha256_checksum(PAYLOAD),
        )
        ref = PayloadRef(
            uri=observation_payload_uri(robot_run_id=RUN, artifact_id=PAYLOAD_ID),
            checksum=sha256_checksum(PAYLOAD),
            size_bytes=len(PAYLOAD),
            media_type=CDR,
        )
        return ref, stored

    async def content_file(self) -> tuple[PayloadRef, str]:
        """A preserved file published by publish-snapshot, and the reference
        the native builders emit for it; returns (ref, stored uri)."""
        tree = self.tmp_path / "tree"
        (tree / "samples/LIDAR_TOP").mkdir(parents=True)
        (tree / LIDAR_PATH).write_bytes(LIDAR)
        published = await publish_snapshot(
            store=self.store,
            sources_root=self.settings.source_root_uri,
            source_dir=tree,
            declaration=SnapshotDeclaration(
                format="nuscenes",
                upstream="test/payloads",
                upstream_revision="a",
                selection=SnapshotSelection(include=("**",)),
            ),
        )
        inventory = published.inventory
        (entry,) = [f for f in inventory.files if f.path == LIDAR_PATH]
        ref = content_file_payload_ref(
            inventory.content_id,
            entry,
            media_type="application/x.nuscenes.lidar-pcd-bin",
        )
        return ref, content_file_uri(published.inventory_uri, LIDAR_PATH)


@pytest.fixture()
def world(tmp_path) -> _World:
    return _World(tmp_path)


async def test_both_sources_read_through_the_same_verified_path(world):
    recording, _ = await world.recording_payload()
    content, _ = await world.content_file()
    reader = payload_reader(world.context)
    assert [await reader.read(r) for r in (recording, content)] == [PAYLOAD, LIDAR]


async def test_the_uri_resolves_to_where_the_producer_stored_the_bytes(world):
    """No location is re-derived from an artifact or content id: the Logical
    URI the builder emitted resolves to the object the producer wrote, reserved
    characters in a preserved path included."""
    recording, recording_stored = await world.recording_payload()
    content, content_stored = await world.content_file()
    resolver = payload_reader(world.context).resolver
    assert resolver.resolve(recording.uri) == recording_stored
    assert resolver.resolve(content.uri) == content_stored


@pytest.mark.parametrize("source", ["recording", "content"])
async def test_bytes_corrupted_after_registration_fail_the_read(world, source):
    if source == "recording":
        ref, stored = await world.recording_payload()
        data = PAYLOAD
    else:
        ref, stored = await world.content_file()
        data = LIDAR
    await world.store.write_bytes(stored, data[::-1])
    with pytest.raises(PayloadVerificationError):
        await payload_reader(world.context).read(ref)


async def test_a_selector_is_refused_by_a_whole_object_consumer(world):
    content, _ = await world.content_file()
    framed = content.model_copy(
        update={
            "media_type": "video/mp4",
            "selector": VideoFrameSelector(timestamp_ns=33_000_000),
        }
    )
    recording, _ = await world.recording_payload()
    require_whole_payloads([recording, content], consumer="detection")
    with pytest.raises(UnsupportedPayloadSelectorError, match="select part"):
        require_whole_payloads([recording, framed], consumer="detection")
