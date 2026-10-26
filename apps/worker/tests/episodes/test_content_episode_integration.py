"""Content-sourced Episodes on real PostgreSQL and real MinIO (ADR-015 §4,
§6.4–§6.7, §7; M3.0 D2, D3, D5): BUILD_SNAPSHOT_EPISODES from a registered
SourceSnapshot, REGISTER_EPISODES through the SourceFileRef gate, and
COMPOSE_DATASET_VERSION of the revisions without any RobotRun.

Each test preserves its own synthetic LeRobot v3.0 tree (its README carries
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
from sceneops_core.episodes.schemas import episode_id_for
from sceneops_core.jobs.schemas import (
    BuildSnapshotEpisodesJobParams,
    ComposeDatasetVersionJobParams,
    JobManifest,
    JobStatus,
    JobType,
)
from sceneops_core.provenance import RevisionDecision, manifest_artifact_id_for
from sceneops_core.sources import SnapshotDeclaration, SnapshotSelection
from sceneops_db.session import get_async_sessionmaker
from sceneops_episodes.lerobot_builder import LeRobotEpisodeBuildError
from sceneops_sources import content_file_uri, publish_snapshot
from sceneops_sources.lerobot_synthetic import INCLUDE, VIDEO_PATH, write_lerobot_tree
from sceneops_storage.backends.s3 import S3ArtifactStore
from sceneops_worker.core import dependencies as dependencies_module
from sceneops_worker.core.dependencies import create_worker_context
from sceneops_worker.datasets.composition import compose_dataset_version
from sceneops_worker.episodes.registration import (
    EpisodeManifestRejectedError,
    EpisodeRegistrationScopeError,
    register_episodes,
)
from sceneops_worker.jobs.base import JobHandlerRequest
from sceneops_worker.jobs.dataset.build_snapshot_episodes import (
    BuildSnapshotEpisodesJobHandler,
)
from sceneops_worker.sources.registration import register_source_snapshot

CONFIG = {
    "features": [
        {"feature": "observation.image", "role": "observation"},
        {"feature": "observation.state", "role": "state"},
        {"feature": "action", "role": "action"},
        {"feature": "next.reward", "role": "transition"},
        {"feature": "next.done", "role": "transition"},
    ]
}


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
                format="lerobot",
                upstream=f"test/{self.run_id}",
                upstream_revision=revision,
                selection=SnapshotSelection(include=INCLUDE),
            ),
            annotations={"license": "mit"},
        )
        async with get_async_sessionmaker()() as session:
            registration = await register_source_snapshot(
                context=self.context(session), descriptor_uri=published.descriptor_uri
            )
        return registration.snapshot.snapshot_id, registration.content.content_id

    async def build(self, snapshot_id: str, unit_keys: Sequence[str] | None = None):
        self.jobs += 1
        job_id = f"job-{self.run_id}-{self.jobs}"
        params = BuildSnapshotEpisodesJobParams.model_validate(
            {"snapshot_id": snapshot_id, "build_config": CONFIG, "unit_keys": unit_keys}
        )
        async with get_async_sessionmaker()() as session:
            context = self.context(session)
            return await BuildSnapshotEpisodesJobHandler().run(
                JobHandlerRequest(
                    job=JobManifest(
                        job_id=job_id,
                        type=JobType.BUILD_SNAPSHOT_EPISODES,
                        status=JobStatus.RUNNING,
                        params=params.model_dump(mode="json"),
                    ),
                    params=params,
                    context=context,
                )
            )

    async def register(self, artifact_ids):
        async with get_async_sessionmaker()() as session:
            return await register_episodes(
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
            "episodes": "SELECT count(*) FROM episodes WHERE content_id = :c",
            "revisions": (
                "SELECT count(*) FROM episode_revisions r JOIN episodes e "
                "USING (episode_id) WHERE e.content_id = :c"
            ),
            "manifest_artifacts": (
                "SELECT count(*) FROM artifacts a JOIN episodes e "
                "ON a.owner_id = e.episode_id WHERE e.content_id = :c"
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

    async def manifest_job(self, artifact_id: str) -> str | None:
        async with get_async_sessionmaker()() as session:
            return (
                await self.context(session).artifact_record_store.get(artifact_id)
            ).job_id


@pytest.fixture()
async def env(
    _fresh_database_connection, _minio_reachable, worker_settings, unique_id, tmp_path
):
    dependencies_module._artifact_store = None
    run_id = unique_id("lerobot")
    root = write_lerobot_tree(tmp_path / "tree", lengths=(3, 2, 4))
    (root / "README.md").write_text(f"# {run_id}\n")
    yield _Env(worker_settings, run_id, root)
    dependencies_module._artifact_store = None


async def test_snapshot_build_registers_and_composes_without_a_robot_run(env):
    snapshot_a, content_id = await env.snapshot("a")

    # Episode 0 alone first.
    first = await env.build(snapshot_a, unit_keys=["0"])
    assert (first.snapshot_id, first.content_id, first.unit_keys) == (
        snapshot_a, content_id, ["0"],
    )  # fmt: skip
    (zero,) = (await env.register(first.manifest_artifact_ids)).revisions
    assert zero.decision == RevisionDecision.NEW_UNIT
    assert (zero.revision.source_kind, zero.revision.content_id) == (
        "content",
        content_id,
    )
    assert zero.revision.robot_run_id is None
    assert (zero.revision.transition_count, zero.revision.transition_topics) == (
        6, ["next.done", "next.reward"],
    )  # fmt: skip

    # The whole content: episode 0 is the same revision, the others are new.
    full = await env.build(snapshot_a)
    assert full.manifest_artifact_ids[0] == first.manifest_artifact_ids[0]
    assert full.created_manifest_count == 2
    registration = await env.register(full.manifest_artifact_ids)
    assert (registration.source_kind, registration.source_id) == ("content", content_id)
    assert sorted(r.decision for r in registration.revisions) == sorted(
        [
            RevisionDecision.EXISTING_REVISION,
            RevisionDecision.NEW_UNIT,
            RevisionDecision.NEW_UNIT,
        ]
    )
    settled = await env.counts(content_id)
    assert (
        settled["episodes"]
        == settled["revisions"]
        == settled["manifest_artifacts"]
        == 3
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
    declared_twice = await env.counts(content_id)  # + snapshot B's descriptor
    through_b = await env.build(snapshot_b)
    assert through_b.snapshot_id == snapshot_b
    assert through_b.manifest_artifact_ids == full.manifest_artifact_ids
    assert (
        await env.manifest_job(full.manifest_artifact_ids[0]) == f"job-{env.run_id}-1"
    )
    assert await env.counts(content_id) == declared_twice

    # A DatasetVersion of content-sourced revisions, by pins and by selector.
    pins = [r.pin.model_dump(mode="json") for r in registration.revisions]
    by_pins = await env.compose(dataset_version="pins", pins=pins)
    assert (by_pins.member_count, by_pins.version.episode.episode_count) == (3, 3)
    assert by_pins.version.scene is None
    by_selector = await env.compose(
        dataset_version="selector",
        selector={
            "sources": [
                {
                    "domain": "episode",
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


async def test_build_refusals_write_nothing(env, tmp_path):
    snapshot_id, content_id = await env.snapshot()
    with pytest.raises(LeRobotEpisodeBuildError, match="name no episode"):
        await env.build(snapshot_id, unit_keys=["0", "9"])
    assert (await env.counts(content_id))["manifest_artifacts"] == 0

    # Another content whose recorded timestamps leave the frame grid, and one of
    # a LeRobot version the builder does not support: both register as
    # preserved sources, neither builds.
    for name, faults in (
        ("off-grid", {"timestamp": lambda e, k: 0.1 * k + (0.01 if k == 1 else 0.0)}),
        ("v2", {"codebase_version": "v2.1"}),
    ):
        tree = write_lerobot_tree(tmp_path / name, **faults)
        (tree / "README.md").write_text(f"# {env.run_id} {name}\n")
        faulty_snapshot, faulty_content = await env.snapshot(name, source=tree)
        with pytest.raises(LeRobotEpisodeBuildError, match="float32|not supported"):
            await env.build(faulty_snapshot)
        assert (await env.counts(faulty_content))["manifest_artifacts"] == 0


async def _republish(env, manifest) -> str:
    """Publish ``manifest`` as a build would, so only the gate stands between
    it and a revision row."""
    async with get_async_sessionmaker()() as session:
        context = env.context(session)
        episode_id = episode_id_for(manifest.lineage.source)
        published = await context.episode_artifact_store.publish_canonical_manifest(
            episode_id=episode_id, manifest=manifest
        )
        artifact_id = manifest_artifact_id_for(
            unit_id=episode_id, manifest_checksum=published.checksum
        )
        await context.artifact_record_store.create_if_absent(
            artifact_id=artifact_id,
            ref=ArtifactRef(
                kind=ArtifactKind.EPISODE_MANIFEST,
                uri=published.uri,
                media_type="application/json",
                checksum=published.checksum,
                size_bytes=published.size_bytes,
            ),
            owner_type=ArtifactOwnerType.EPISODE,
            owner_id=episode_id,
        )
        await context.commit()
        return artifact_id


async def _built_manifest(env, snapshot_id: str):
    built = await env.build(snapshot_id, unit_keys=["1"])
    async with get_async_sessionmaker()() as session:
        context = env.context(session)
        record = await context.artifact_record_store.get(built.manifest_artifact_ids[0])
        return await context.episode_artifact_store.read_pinned_manifest(
            uri=record.uri, checksum=record.checksum, size_bytes=record.size_bytes
        )


async def test_a_source_file_ref_that_differs_from_the_inventory_is_rejected(env):
    snapshot_id, content_id = await env.snapshot()
    manifest = await _built_manifest(env, snapshot_id)
    document = json.loads(manifest.to_canonical_bytes())
    for occurrence in document["observations"]:
        occurrence["payload"]["sha256"] = "sha256:" + "0" * 64
    forged = type(manifest).model_validate(document)
    with pytest.raises(EpisodeManifestRejectedError, match="inventory pins"):
        await env.register([await _republish(env, forged)])
    assert (await env.counts(content_id))["revisions"] == 0


async def test_a_referenced_content_object_missing_from_minio_is_rejected(env):
    snapshot_id, content_id = await env.snapshot()
    built = await env.build(snapshot_id, unit_keys=["1"])
    async with get_async_sessionmaker()() as session:
        content = await env.context(session).source_store.get_content(content_id)
        inventory = await env.context(session).artifact_record_store.get(
            content.inventory_artifact_id
        )
    await env.store.delete_prefix(content_file_uri(inventory.uri, VIDEO_PATH))
    with pytest.raises(EpisodeManifestRejectedError, match="not stored"):
        await env.register(built.manifest_artifact_ids)
    assert (await env.counts(content_id))["revisions"] == 0


async def test_a_binding_to_unregistered_content_is_out_of_scope(env, tmp_path):
    snapshot_id, content_id = await env.snapshot()
    manifest = await _built_manifest(env, snapshot_id)
    # The same manifest re-bound to content that was never registered, with
    # its fingerprint and file references recomputed consistently.
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
    for occurrence in document["observations"]:
        occurrence["payload"]["content_id"] = other_id
    rebound = type(manifest).model_validate(document)
    with pytest.raises(EpisodeRegistrationScopeError, match="not registered"):
        await env.register([await _republish(env, rebound)])
