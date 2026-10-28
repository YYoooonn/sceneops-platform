"""Content-sourced Scenes on real PostgreSQL and real MinIO (ADR-015 §4,
§6.4–§6.7, §7; M3.0 D2, D3, D5): BUILD_SNAPSHOT_SCENES from a registered
SourceSnapshot, REGISTER_SCENES through the SourceFileRef gate,
COMPOSE_DATASET_VERSION of the revisions without any RobotRun, and the payload
locator resolving preserved files.

Each test preserves its own synthetic nuScenes v1.0 tree (its LICENSE carries
the test's id, so its content is its own) under its own declaration.
Requires the disposable database and bucket of `make test-integration`;
skips (and so fails there) otherwise.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pytest
from sqlalchemy import text

from sceneops_core.artifacts.schemas import ArtifactKind, ArtifactOwnerType, ArtifactRef
from sceneops_core.datasets.schemas import DatasetRecord
from sceneops_core.jobs.schemas import (
    BuildSnapshotScenesJobParams,
    ComposeDatasetVersionJobParams,
    JobManifest,
    JobStatus,
    JobType,
)
from sceneops_core.provenance import RevisionDecision, manifest_artifact_id_for
from sceneops_core.scenes.schemas import scene_id_for
from sceneops_core.sources import SnapshotDeclaration, SnapshotSelection
from sceneops_db.session import get_async_sessionmaker
from sceneops_scenes.nuscenes_builder import NuScenesSceneBuildError
from sceneops_sources import content_file_uri, publish_snapshot
from sceneops_sources.nuscenes_synthetic import (
    CHANNELS,
    INCLUDE,
    token,
    write_nuscenes_tree,
)
from sceneops_storage.backends.s3 import S3ArtifactStore
from sceneops_worker.core import dependencies as dependencies_module
from sceneops_worker.core.dependencies import create_worker_context
from sceneops_worker.datasets.composition import compose_dataset_version
from sceneops_worker.jobs.base import JobHandlerRequest
from sceneops_worker.jobs.dataset.build_snapshot_scenes import (
    BuildSnapshotScenesJobHandler,
)
from sceneops_worker.payloads import PayloadLocator
from sceneops_worker.recordings.payload_refs import PayloadIntegrityError
from sceneops_worker.scenes.registration import (
    SceneManifestRejectedError,
    SceneRegistrationScopeError,
    register_scenes,
)
from sceneops_worker.sources.registration import register_source_snapshot

CONFIG = {"channels": sorted(CHANNELS)}


class _Env:
    def __init__(self, settings, run_id: str, root: Path) -> None:
        self.settings = settings
        self.run_id = run_id
        self.root = root
        self.store = S3ArtifactStore(settings=settings.artifact)
        self.jobs = 0

    def context(self, session):
        return create_worker_context(session, settings=self.settings, worker_id="test")

    async def snapshot(self, revision: str = "a", *, source: Path | None = None):
        """Publish and register one declaration of ``source`` (default: the
        test's tree); returns (snapshot_id, content_id)."""
        published = await publish_snapshot(
            store=self.store,
            sources_root=self.settings.artifact.source_root_uri,
            source_dir=source or self.root,
            declaration=SnapshotDeclaration(
                format="nuscenes",
                upstream=f"test/{self.run_id}",
                upstream_revision=revision,
                selection=SnapshotSelection(include=INCLUDE),
            ),
            annotations={"license": "nuscenes-terms-of-use"},
        )
        async with get_async_sessionmaker()() as session:
            registration = await register_source_snapshot(
                context=self.context(session), descriptor_uri=published.descriptor_uri
            )
        return registration.snapshot.snapshot_id, registration.content.content_id

    async def build(self, snapshot_id: str, unit_keys: Sequence[str] | None = None):
        self.jobs += 1
        job_id = f"job-{self.run_id}-{self.jobs}"
        params = BuildSnapshotScenesJobParams.model_validate(
            {"snapshot_id": snapshot_id, "build_config": CONFIG, "unit_keys": unit_keys}
        )
        async with get_async_sessionmaker()() as session:
            context = self.context(session)
            return await BuildSnapshotScenesJobHandler().run(
                JobHandlerRequest(
                    job=JobManifest(
                        job_id=job_id,
                        type=JobType.BUILD_SNAPSHOT_SCENES,
                        status=JobStatus.RUNNING,
                        params=params.model_dump(mode="json"),
                    ),
                    params=params,
                    context=context,
                )
            )

    async def register(self, artifact_ids):
        async with get_async_sessionmaker()() as session:
            return await register_scenes(
                context=self.context(session), manifest_artifact_ids=artifact_ids
            )

    async def compose(self, **params):
        async with get_async_sessionmaker()() as session:
            context = self.context(session)
            if await context.dataset_store.get_dataset(f"ds-{self.run_id}") is None:
                await context.dataset_store.create_dataset(
                    DatasetRecord(dataset_id=f"ds-{self.run_id}", name=self.run_id)
                )
                await context.commit()
            return await compose_dataset_version(
                context=context,
                params=ComposeDatasetVersionJobParams.model_validate(
                    {"dataset_id": f"ds-{self.run_id}", **params}
                ),
            )

    async def counts(self, content_id: str) -> dict[str, int]:
        queries = {
            "scenes": "SELECT count(*) FROM scenes WHERE content_id = :c",
            "revisions": (
                "SELECT count(*) FROM scene_revisions r JOIN scenes s "
                "USING (scene_id) WHERE s.content_id = :c"
            ),
            "manifest_artifacts": (
                "SELECT count(*) FROM artifacts a JOIN scenes s "
                "ON a.owner_id = s.scene_id WHERE s.content_id = :c"
            ),
        }
        async with get_async_sessionmaker()() as session:
            counts = {
                name: (await session.execute(text(sql), {"c": content_id})).scalar_one()
                for name, sql in queries.items()
            }
        counts["objects"] = len(
            await self.store.list_objects(self.settings.artifact_root_uri)
        )
        return counts

    async def manifest(self, artifact_id: str):
        async with get_async_sessionmaker()() as session:
            context = self.context(session)
            record = await context.artifact_record_store.get(artifact_id)
            return record, await context.scene_artifact_store.read_pinned_manifest(
                uri=record.uri, checksum=record.checksum, size_bytes=record.size_bytes
            )


@pytest.fixture()
async def env(
    _fresh_database_connection, _minio_reachable, worker_settings, unique_id, tmp_path
):
    dependencies_module._artifact_store = None
    run_id = unique_id("nuscenes")
    root = write_nuscenes_tree(tmp_path / "tree", scenes=2, samples=3, sweeps=1)
    (root / "LICENSE").write_text(f"{run_id}\n")
    yield _Env(worker_settings, run_id, root)
    dependencies_module._artifact_store = None


async def test_snapshot_build_registers_and_composes_without_a_robot_run(env):
    snapshot_a, content_id = await env.snapshot("a")
    first_scene, second_scene = sorted([token("scene", 0), token("scene", 1)])

    first = await env.build(snapshot_a, unit_keys=[first_scene])
    assert (first.snapshot_id, first.content_id, first.unit_keys) == (
        snapshot_a, content_id, [first_scene],
    )  # fmt: skip
    assert (first.observation_count, first.keyframe_count, first.pose_count) == (
        18, 3, 18,
    )  # fmt: skip
    (one,) = (await env.register(first.manifest_artifact_ids)).revisions
    assert one.decision == RevisionDecision.NEW_UNIT
    assert (one.revision.source_kind, one.revision.content_id) == (
        "content",
        content_id,
    )
    assert one.revision.robot_run_id is None
    assert (one.revision.observation_count, one.revision.keyframe_count) == (18, 3)

    full = await env.build(snapshot_a)
    assert full.unit_keys == [first_scene, second_scene]
    assert full.manifest_artifact_ids[0] == first.manifest_artifact_ids[0]
    assert full.created_manifest_count == 1
    registration = await env.register(full.manifest_artifact_ids)
    assert (registration.source_kind, registration.source_id) == ("content", content_id)
    assert sorted(r.decision for r in registration.revisions) == sorted(
        [RevisionDecision.EXISTING_REVISION, RevisionDecision.NEW_UNIT]
    )
    settled = await env.counts(content_id)
    assert (
        settled["scenes"] == settled["revisions"] == settled["manifest_artifacts"] == 2
    )

    # Rebuilding and re-registering writes no row, artifact or object.
    again = await env.build(snapshot_a)
    assert (again.manifest_artifact_ids, again.created_manifest_count) == (
        full.manifest_artifact_ids, 0,
    )  # fmt: skip
    assert {
        r.decision for r in (await env.register(again.manifest_artifact_ids)).revisions
    } == {RevisionDecision.EXISTING_REVISION}
    assert await env.counts(content_id) == settled

    # A second declaration of the same bytes reuses every revision; its Job
    # result names it, while the manifest records keep the first build's Job.
    snapshot_b, same_content = await env.snapshot("b")
    assert (snapshot_b != snapshot_a, same_content) == (True, content_id)
    declared_twice = await env.counts(content_id)
    through_b = await env.build(snapshot_b)
    assert through_b.snapshot_id == snapshot_b
    assert through_b.manifest_artifact_ids == full.manifest_artifact_ids
    record, _ = await env.manifest(full.manifest_artifact_ids[0])
    assert record.job_id == f"job-{env.run_id}-1"
    assert await env.counts(content_id) == declared_twice

    # A DatasetVersion of content-sourced Scene revisions, by pins and by selector.
    pins = [r.pin.model_dump(mode="json") for r in registration.revisions]
    by_pins = await env.compose(dataset_version="pins", pins=pins)
    assert (by_pins.member_count, by_pins.version.scene.scene_count) == (2, 2)
    assert by_pins.version.episode is None
    by_selector = await env.compose(
        dataset_version="selector",
        selector={
            "sources": [
                {
                    "domain": "scene",
                    "source_kind": "content",
                    "source_id": content_id,
                    "producer_fingerprint": full.producer_fingerprint,
                }
            ]
        },
    )
    assert (
        by_selector.version.composition_checksum == by_pins.version.composition_checksum
    )


async def test_the_payload_locator_resolves_preserved_files(env):
    snapshot_id, content_id = await env.snapshot()
    built = await env.build(snapshot_id, unit_keys=[token("scene", 0)])
    await env.register(built.manifest_artifact_ids)
    _, manifest = await env.manifest(built.manifest_artifact_ids[0])
    refs = [o.payload for o in manifest.observations]
    async with get_async_sessionmaker()() as session:
        context = env.context(session)
        uris = await PayloadLocator(context).uris(refs)
        content = await context.source_store.get_content(content_id)
        inventory = await context.artifact_record_store.get(
            content.inventory_artifact_id
        )
        assert uris == {r: content_file_uri(inventory.uri, r.path) for r in refs}
        camera = next(r for r in refs if r.media_type == "image/jpeg")
        assert (
            await env.store.read_bytes(uris[camera])
            == (env.root / camera.path).read_bytes()
        )
        forged = camera.model_copy(update={"size_bytes": camera.size_bytes + 1})
        with pytest.raises(PayloadIntegrityError, match="inventory pins"):
            await PayloadLocator(context).uris([forged])


async def test_build_refusals_write_nothing(env, tmp_path):
    snapshot_id, content_id = await env.snapshot()
    with pytest.raises(NuScenesSceneBuildError, match="name no scene"):
        await env.build(snapshot_id, unit_keys=[token("scene", 9)])
    assert (await env.counts(content_id))["manifest_artifacts"] == 0

    # A slice that preserves one scene's files only registers as a preserved
    # source; building it fails, naming the unpreserved scene, until unit_keys
    # names the preserved one.
    sliced = write_nuscenes_tree(tmp_path / "slice", omit_scene_files=(0,))
    (sliced / "LICENSE").write_text(f"{env.run_id} slice\n")
    slice_snapshot, slice_content = await env.snapshot("slice", source=sliced)
    with pytest.raises(NuScenesSceneBuildError, match=token("scene", 0)):
        await env.build(slice_snapshot)
    assert (await env.counts(slice_content))["manifest_artifacts"] == 0
    assert (
        await env.build(slice_snapshot, unit_keys=[token("scene", 1)])
    ).scene_count == 1


async def _republish(env, manifest) -> str:
    """Publish ``manifest`` as a build would, so only the gate stands between
    it and a revision row."""
    async with get_async_sessionmaker()() as session:
        context = env.context(session)
        scene_id = scene_id_for(manifest.lineage.source)
        published = await context.scene_artifact_store.publish_canonical_manifest(
            scene_id=scene_id, manifest=manifest
        )
        artifact_id = manifest_artifact_id_for(
            unit_id=scene_id, manifest_checksum=published.checksum
        )
        await context.artifact_record_store.create_if_absent(
            artifact_id=artifact_id,
            ref=ArtifactRef(
                kind=ArtifactKind.SCENE_MANIFEST,
                uri=published.uri,
                media_type="application/json",
                checksum=published.checksum,
                size_bytes=published.size_bytes,
            ),
            owner_type=ArtifactOwnerType.SCENE,
            owner_id=scene_id,
            scene_id=scene_id,
        )
        await context.commit()
        return artifact_id


async def test_a_source_file_ref_that_differs_from_the_inventory_is_rejected(env):
    snapshot_id, content_id = await env.snapshot()
    built = await env.build(snapshot_id, unit_keys=[token("scene", 1)])
    _, manifest = await env.manifest(built.manifest_artifact_ids[0])
    document = json.loads(manifest.to_canonical_bytes())
    document["observations"][0]["payload"]["sha256"] = "sha256:" + "0" * 64
    forged = type(manifest).model_validate(document)
    with pytest.raises(SceneManifestRejectedError, match="inventory pins"):
        await env.register([await _republish(env, forged)])
    assert (await env.counts(content_id))["revisions"] == 0


async def test_a_referenced_content_object_missing_from_minio_is_rejected(env):
    snapshot_id, content_id = await env.snapshot()
    built = await env.build(snapshot_id, unit_keys=[token("scene", 1)])
    _, manifest = await env.manifest(built.manifest_artifact_ids[0])
    async with get_async_sessionmaker()() as session:
        content = await env.context(session).source_store.get_content(content_id)
        inventory = await env.context(session).artifact_record_store.get(
            content.inventory_artifact_id
        )
    lidar = next(o for o in manifest.observations if o.channel == "LIDAR_TOP")
    await env.store.delete_prefix(content_file_uri(inventory.uri, lidar.payload.path))
    with pytest.raises(SceneManifestRejectedError, match="not stored"):
        await env.register(built.manifest_artifact_ids)
    assert (await env.counts(content_id))["revisions"] == 0


async def test_a_binding_to_unregistered_content_is_out_of_scope(env):
    snapshot_id, _ = await env.snapshot()
    built = await env.build(snapshot_id, unit_keys=[token("scene", 1)])
    _, manifest = await env.manifest(built.manifest_artifact_ids[0])
    from sceneops_core.provenance import ContentUnitSource, ProducerInfo
    from sceneops_core.sources import content_id_for

    other_checksum = "sha256:" + "9" * 64
    other_id = content_id_for(other_checksum)
    source = ContentUnitSource(
        **{
            **manifest.lineage.source.model_dump(),
            "content_id": other_id,
            "content_checksum": other_checksum,
        }
    )
    producer = ProducerInfo.create(
        producer_id=manifest.lineage.producer.producer_id,
        semantics_version=manifest.lineage.producer.semantics_version,
        build_config=manifest.lineage.producer.build_config,
        source=source.source_revision(),
    )
    document = json.loads(manifest.to_canonical_bytes())
    document["lineage"] = {
        "source": source.model_dump(mode="json"),
        "producer": producer.model_dump(mode="json"),
    }
    for observation in document["observations"]:
        observation["payload"]["content_id"] = other_id
    rebound = type(manifest).model_validate(document)
    with pytest.raises(SceneRegistrationScopeError, match="not registered"):
        await env.register([await _republish(env, rebound)])
