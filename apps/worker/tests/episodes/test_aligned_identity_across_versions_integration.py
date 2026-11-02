"""One Episode revision aligned with one recipe from several DatasetVersions, on real
PostgreSQL and real MinIO, with synthetic LeRobot Episodes built, registered and
composed by the real path (ADR-022).

The result belongs to the revision and the recipe, so every DatasetVersion pinning
that revision gets one object and one record, and a DatasetVersion that does not is
refused before anything is written.

Requires the disposable database and bucket of `make test-integration`; skips (and so
fails there) otherwise.
"""

from __future__ import annotations

import asyncio
import hashlib
import sys
from pathlib import Path

import pytest
from sqlalchemy import text

from sceneops_analytics import SceneOpsDataset, check_learning_export_tables
from sceneops_core.artifacts.schemas import ArtifactKind, ArtifactOwnerType
from sceneops_core.episodes.alignment import (
    TemporalAlignmentConfig,
    TemporalSourceContext,
    align_episode,
    aligned_episode_alignment_key,
    load_aligned_episode_artifact,
)
from sceneops_core.episodes.learning import EpisodeRef
from sceneops_core.episodes.learning_export import LearningDataExportManifest
from sceneops_core.episodes.schemas import load_canonical_episode_manifest
from sceneops_core.jobs.schemas import (
    AlignEpisodeJobParams,
    BuildSnapshotEpisodesJobParams,
    ComposeDatasetVersionJobParams,
    CreateJobRequest,
    ExportLearningDataJobParams,
    JobType,
    ProfileAlignedEpisodeJobParams,
    ValidateAlignedEpisodeJobParams,
)
from sceneops_core.temporal import (
    ClockOrigin,
    IncomparableClocksError,
    require_comparable,
)
from sceneops_db.postgres.artifacts import PostgresArtifactRefRepository
from sceneops_db.postgres.episodes import PostgresEpisodeRepository
from sceneops_db.postgres.jobs import PostgresJobEventRepository, PostgresJobRepository
from sceneops_db.session import get_async_sessionmaker
from sceneops_episodes.artifacts import (
    EpisodeManifestWriteConflictError,
    canonical_document_bytes,
)
from sceneops_execution.jobs.service import JobService
from sceneops_sources.lerobot_synthetic import write_lerobot_tree
from sceneops_worker.core import dependencies as dependencies_module
from sceneops_worker.core.context import WorkerContext
from sceneops_worker.datasets.composition import compose_dataset_version
from sceneops_worker.episodes.registration import register_episodes
from sceneops_worker.jobs.base import JobHandlerRequest
from sceneops_worker.jobs.dataset._aligned_episode_resolution import (
    AlignedArtifactNotEligibleError,
)
from sceneops_worker.jobs.dataset.align_episode import (
    AlignEpisodeJobHandler,
    SourceRevisionMismatchError,
    SourceRevisionNotMemberError,
)
from sceneops_worker.jobs.dataset.build_snapshot_episodes import (
    BuildSnapshotEpisodesJobHandler,
)
from sceneops_worker.jobs.dataset.export_learning_data import (
    ExportLearningDataJobHandler,
)
from sceneops_worker.jobs.dataset.profile_aligned_episode import (
    ProfileAlignedEpisodeJobHandler,
)
from sceneops_worker.jobs.dataset.validate_aligned_episode import (
    ValidateAlignedEpisodeJobHandler,
)

sys.path.insert(0, str(Path(__file__).parent))
from test_aligned_schema_upgrade_integration import (  # noqa: E402
    ALIGNMENT,
    BUILD_CONFIG,
    VERSION as VERSION_A,
    _Env,
)

VERSION_B = "version-b"
OTHER_RECIPE = TemporalAlignmentConfig(
    target_frequency_hz=5.0, tolerance_us=50_000, max_gap_us=400_000
)


def _pin(revision) -> dict:
    return {
        "domain": "episode",
        "unit_id": revision.episode_id,
        "manifest_artifact_id": revision.manifest_artifact_id,
        "manifest_checksum": revision.manifest_checksum,
    }


def _service(session) -> JobService:
    return JobService(
        repository=PostgresJobRepository(session),
        event_repository=PostgresJobEventRepository(session),
        artifact_repository=PostgresArtifactRefRepository(session),
        episode_repository=PostgresEpisodeRepository(session),
    )


class _Versions(_Env):
    async def compose(self, version: str, revisions: list) -> None:
        async with get_async_sessionmaker()() as session:
            await compose_dataset_version(
                context=self.context(session),
                params=ComposeDatasetVersionJobParams.model_validate(
                    {
                        "dataset_id": self.dataset_id,
                        "dataset_version": version,
                        "pins": [_pin(r) for r in revisions],
                    }
                ),
            )

    async def another_revision(self, revision):
        """A second registered revision of the same Episode: the same content built
        with another channel selection."""
        (other,) = [
            r
            for r in await self._build(
                {
                    "features": [
                        f
                        for f in BUILD_CONFIG["features"]
                        if f["role"] != "observation"
                    ]
                }
            )
            if r.episode_id == revision.episode_id
        ]
        assert other.manifest_artifact_id != revision.manifest_artifact_id
        return other

    async def _build(self, build_config: dict) -> list:
        params = BuildSnapshotEpisodesJobParams.model_validate(
            {"snapshot_id": self.snapshot_id, "build_config": build_config}
        )
        async with get_async_sessionmaker()() as session:
            built = await BuildSnapshotEpisodesJobHandler().run(
                JobHandlerRequest(
                    job=self.next_job(JobType.BUILD_SNAPSHOT_EPISODES, params),
                    params=params,
                    context=self.context(session),
                )
            )
        async with get_async_sessionmaker()() as session:
            registered = await register_episodes(
                context=self.context(session),
                manifest_artifact_ids=built.manifest_artifact_ids,
            )
        return [r.revision for r in registered.revisions]

    async def run_handler(self, handler, job_type: JobType, params):
        async with get_async_sessionmaker()() as session:
            return await handler.run(
                JobHandlerRequest(
                    job=self.next_job(job_type, params),
                    params=params,
                    context=self.context(session),
                )
            )

    async def align_in(
        self, version: str, revisions: list, *, recipe=ALIGNMENT, pin=False
    ):
        episodes = [
            {
                "episode_id": r.episode_id,
                **(
                    {
                        "source_artifact_id": r.manifest_artifact_id,
                        "source_manifest_sha256": r.manifest_checksum.removeprefix(
                            "sha256:"
                        ),
                    }
                    if pin
                    else {}
                ),
            }
            for r in revisions
        ]
        params = AlignEpisodeJobParams(
            episodes=episodes,
            dataset_id=self.dataset_id,
            dataset_version=version,
            alignment_config=recipe,
        )
        return await self.run_handler(
            AlignEpisodeJobHandler(), JobType.ALIGN_EPISODE, params
        )

    async def analyse_in(self, version: str, ref, *, handler, model, job_type):
        params = model(
            episode_id=ref.episode_id,
            dataset_id=self.dataset_id,
            dataset_version=version,
            aligned_artifact_id=ref.aligned_artifact_id,
            aligned_artifact_checksum=ref.aligned_artifact_checksum,
        )
        return await self.run_handler(handler(), job_type, params)

    async def validate_in(self, version: str, ref):
        return await self.analyse_in(
            version,
            ref,
            handler=ValidateAlignedEpisodeJobHandler,
            model=ValidateAlignedEpisodeJobParams,
            job_type=JobType.VALIDATE_ALIGNED_EPISODE,
        )

    async def profile_in(self, version: str, ref):
        return await self.analyse_in(
            version,
            ref,
            handler=ProfileAlignedEpisodeJobHandler,
            model=ProfileAlignedEpisodeJobParams,
            job_type=JobType.PROFILE_ALIGNED_EPISODE,
        )

    async def export_in(self, version: str, refs: list):
        params = ExportLearningDataJobParams(
            dataset_id=self.dataset_id,
            dataset_version=version,
            inputs=[
                {
                    "episode_id": r.episode_id,
                    "aligned_artifact_id": r.aligned_artifact_id,
                    "aligned_artifact_checksum": r.aligned_artifact_checksum,
                }
                for r in refs
            ],
        )
        return await self.run_handler(
            ExportLearningDataJobHandler(), JobType.EXPORT_LEARNING_DATA, params
        )

    async def objects(self) -> dict[str, bytes]:
        return {
            item.uri: await self.store.read_bytes(item.uri)
            for item in await self.store.list_objects(self.settings.artifact_root_uri)
        }

    async def record(self, artifact_id: str):
        async with get_async_sessionmaker()() as session:
            return await self.context(session).artifact_record_store.get(artifact_id)

    async def records_of(self, kind: ArtifactKind, episode_id: str) -> list:
        async with get_async_sessionmaker()() as session:
            return await self.context(session).artifact_record_store.list(
                kind=kind, owner_id=episode_id
            )


@pytest.fixture()
async def env(
    _fresh_database_connection, _minio_reachable, worker_settings, unique_id, tmp_path
):
    dependencies_module._artifact_store = None
    run_id = unique_id("aligned-identity")
    root = write_lerobot_tree(tmp_path / "tree", lengths=(3, 3))
    (root / "README.md").write_text(f"# {run_id}\n")
    yield _Versions(worker_settings, run_id, root)
    dependencies_module._artifact_store = None


# ── multi-version identity and storage ───────────────────────────────────────────


@pytest.mark.parametrize("composed_after_the_first_alignment", [False, True])
async def test_two_versions_share_one_object_and_one_record(
    env, composed_after_the_first_alignment
):
    """The defect: the second version's alignment failed with
    ArtifactRecordConflictError and left an object nothing references. Both orders:
    the second version existing before the artifact, and only after it."""
    (first, _) = await env.compose_two_episodes()
    if not composed_after_the_first_alignment:
        await env.compose(VERSION_B, [first])

    one = await env.align_in(VERSION_A, [first])
    objects = await env.objects()
    record = await env.record(one.aligned[0].aligned_artifact_id)

    if composed_after_the_first_alignment:
        await env.compose(VERSION_B, [first])
    two = await env.align_in(VERSION_B, [first])

    a, b = one.aligned[0], two.aligned[0]
    assert (
        a.aligned_artifact_id,
        a.aligned_artifact_uri,
        a.aligned_artifact_checksum,
    ) == (
        b.aligned_artifact_id,
        b.aligned_artifact_uri,
        b.aligned_artifact_checksum,
    )
    # No new object, and the first one's bytes and the record's first-write
    # provenance are untouched.
    assert await env.objects() == objects
    assert await env.record(a.aligned_artifact_id) == record

    assert (record.owner_type, record.owner_id) == (
        ArtifactOwnerType.EPISODE.value,
        first.episode_id,
    )
    assert record.dataset_id is None and record.dataset_version is None
    assert env.dataset_id not in a.aligned_artifact_uri
    assert "/aligned_episodes/" in a.aligned_artifact_uri
    stored = objects[a.aligned_artifact_uri]
    assert env.dataset_id.encode() not in stored
    assert "sha256:" + hashlib.sha256(stored).hexdigest() == a.aligned_artifact_checksum
    assert (
        len(
            await env.records_of(
                ArtifactKind.ALIGNED_EPISODE_MANIFEST, first.episode_id
            )
        )
        == 1
    )


async def test_repeats_in_either_version_are_deterministic(env):
    (first, _) = await env.compose_two_episodes()
    await env.compose(VERSION_B, [first])
    refs = [
        (await env.align_in(version, [first], pin=pin)).aligned[0]
        for version, pin in (
            (VERSION_A, False),
            (VERSION_A, True),
            (VERSION_B, False),
            (VERSION_B, True),
        )
    ]
    assert len({r.model_dump_json() for r in refs}) == 1


async def test_recipes_and_revisions_are_distinct_artifacts(env):
    (first, _) = await env.compose_two_episodes()
    other_revision = await env.another_revision(first)
    await env.compose(VERSION_B, [other_revision])

    base = (await env.align_in(VERSION_A, [first])).aligned[0]
    recipe = (await env.align_in(VERSION_A, [first], recipe=OTHER_RECIPE)).aligned[0]
    revision = (await env.align_in(VERSION_B, [other_revision])).aligned[0]

    for other in (recipe, revision):
        assert other.aligned_artifact_id != base.aligned_artifact_id
        assert other.aligned_artifact_uri != base.aligned_artifact_uri
        assert other.aligned_artifact_checksum != base.aligned_artifact_checksum
    assert revision.source_artifact_id == other_revision.manifest_artifact_id


async def test_analyses_of_a_shared_artifact_are_shared_too(env):
    (first, _) = await env.compose_two_episodes()
    await env.compose(VERSION_B, [first])
    ref = (await env.align_in(VERSION_A, [first])).aligned[0]

    validations = [await env.validate_in(v, ref) for v in (VERSION_A, VERSION_B)]
    objects = await env.objects()
    profiles = [await env.profile_in(v, ref) for v in (VERSION_A, VERSION_B)]

    assert validations[0].valid is True
    for one, two in (validations, profiles):
        assert one.report_artifact_id == two.report_artifact_id
        assert one.report_uri == two.report_uri
    new = (await env.objects()).keys() - objects.keys()
    assert new == {profiles[0].report_uri}
    record = await env.record(validations[0].report_artifact_id)
    assert record.dataset_id is None and record.dataset_version is None


# ── membership ───────────────────────────────────────────────────────────────────


async def test_a_version_not_pinning_the_revision_is_refused_and_writes_nothing(env):
    (first, second) = await env.compose_two_episodes()
    other_revision = await env.another_revision(first)
    await env.compose("only-second", [second])
    await env.compose("other-revision", [other_revision])
    ref = (await env.align_in(VERSION_A, [first])).aligned[0]
    objects = await env.objects()
    aligned_records = await env.records_of(
        ArtifactKind.ALIGNED_EPISODE_MANIFEST, first.episode_id
    )

    # The Episode is not a member, by the unpinned and the pinned route.
    for pin in (False, True):
        with pytest.raises(ValueError, match="is not a member of"):
            await env.align_in("only-second", [first], pin=pin)
    # It is a member under another revision: the artifact and its pin both name the
    # one this version does not pin.
    with pytest.raises(SourceRevisionNotMemberError):
        await env.align_in("other-revision", [first], pin=True)
    for use in (env.validate_in, env.profile_in):
        with pytest.raises(AlignedArtifactNotEligibleError, match="not a member"):
            await use("only-second", ref)
        with pytest.raises(AlignedArtifactNotEligibleError, match="computed from"):
            await use("other-revision", ref)
    with pytest.raises(AlignedArtifactNotEligibleError, match="computed from"):
        await env.export_in("other-revision", [ref])
    with pytest.raises(AlignedArtifactNotEligibleError, match="not a member"):
        await env.export_in("only-second", [ref])

    assert await env.objects() == objects
    assert (
        await env.records_of(ArtifactKind.ALIGNED_EPISODE_MANIFEST, first.episode_id)
        == aligned_records
    )


async def test_a_forged_pin_is_refused_and_writes_nothing(env):
    (first, second) = await env.compose_two_episodes()
    objects = await env.objects()
    forged_checksum = first.model_copy(
        update={"manifest_checksum": "sha256:" + "f" * 64}
    )
    with pytest.raises(SourceRevisionMismatchError):
        await env.align_in(VERSION_A, [forged_checksum], pin=True)
    # Another Episode's revision under this Episode's name.
    forged_artifact = first.model_copy(
        update={"manifest_artifact_id": second.manifest_artifact_id}
    )
    with pytest.raises(SourceRevisionNotMemberError):
        await env.align_in(VERSION_A, [forged_artifact], pin=True)
    assert await env.objects() == objects


async def test_an_artifact_named_as_another_episode_is_refused(env):
    (first, second) = await env.compose_two_episodes()
    ref = (await env.align_in(VERSION_A, [first])).aligned[0]
    with pytest.raises(Exception, match="does not belong to"):
        await env.validate_in(
            VERSION_A, ref.model_copy(update={"episode_id": second.episode_id})
        )


# ── immutability and partial failure ─────────────────────────────────────────────


async def test_a_genuine_content_conflict_fails_and_keeps_both_stores_as_they_were(env):
    """Other bytes at the key of this revision and recipe: nothing can have written
    them legitimately, so they are reported, never replaced and never registered."""
    (first, _) = await env.compose_two_episodes()
    _, manifest_bytes = await env.manifest(first)
    manifest = load_canonical_episode_manifest(manifest_bytes)
    aligned = align_episode(
        manifest,
        ALIGNMENT,
        TemporalSourceContext(source_clock=manifest.declared_window().source_clock),
        episode_id=first.episode_id,
    )
    async with get_async_sessionmaker()() as session:
        uri = env.context(session).episode_artifact_store.aligned_episode_uri(
            episode_id=first.episode_id,
            source_manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
            alignment_key=aligned_episode_alignment_key(aligned),
        )
    await env.store.write_bytes(uri, b"not the artifact of this recipe")

    with pytest.raises(EpisodeManifestWriteConflictError, match="write-once"):
        await env.align_in(VERSION_A, [first])

    assert await env.store.read_bytes(uri) == b"not the artifact of this recipe"
    assert not await env.records_of(
        ArtifactKind.ALIGNED_EPISODE_MANIFEST, first.episode_id
    )


async def test_an_object_left_by_a_failed_registration_is_converged_on(
    env, monkeypatch
):
    """Write, then crash before the commit: the object exists and no record does. The
    next attempt, from any version, adopts that object: no second one, no error."""
    (first, _) = await env.compose_two_episodes()
    await env.compose(VERSION_B, [first])

    async def crash(self):
        raise ConnectionError("database went away before the commit")

    with monkeypatch.context() as patch:
        patch.setattr(WorkerContext, "commit", crash)
        with pytest.raises(ConnectionError):
            await env.align_in(VERSION_A, [first])

    objects = await env.objects()
    assert not await env.records_of(
        ArtifactKind.ALIGNED_EPISODE_MANIFEST, first.episode_id
    )
    assert sum("/aligned_episodes/" in uri for uri in objects) == 1

    ref = (await env.align_in(VERSION_B, [first])).aligned[0]
    assert await env.objects() == objects
    assert ref.aligned_artifact_uri in objects
    assert await env.record(ref.aligned_artifact_id) is not None


async def test_a_record_at_an_earlier_location_is_reused_and_stays_readable(env):
    """A v2 record an earlier deployment wrote under a DatasetVersion prefix has this
    id and another URI. It is valid: its bytes are read, used and left in place, with
    no second object and no conflict."""
    (first, _) = await env.compose_two_episodes()
    await env.compose(VERSION_B, [first])
    _, manifest_bytes = await env.manifest(first)
    sha = hashlib.sha256(manifest_bytes).hexdigest()

    # What this revision's artifact is, computed once and then discarded.
    probe = (await env.align_in(VERSION_A, [first])).aligned[0]
    data = (await env.objects())[probe.aligned_artifact_uri]
    old_uri = (
        f"{env.settings.dataset_root_uri}/{env.dataset_id}/versions/{VERSION_A}"
        f"/episodes/{first.episode_id}/aligned/{sha[:16]}/old.json"
    )
    await env.store.write_bytes(old_uri, data)
    async with get_async_sessionmaker()() as session:
        await session.execute(
            text("UPDATE artifacts SET uri = :uri WHERE artifact_id = :id"),
            {"uri": old_uri, "id": probe.aligned_artifact_id},
        )
        await session.commit()
    await env.store.delete_prefix(probe.aligned_artifact_uri)
    objects = await env.objects()

    again = (await env.align_in(VERSION_B, [first])).aligned[0]

    assert again.aligned_artifact_id == probe.aligned_artifact_id
    assert again.aligned_artifact_uri == old_uri
    assert await env.objects() == objects
    assert (await env.validate_in(VERSION_B, again)).valid is True


# ── concurrency ──────────────────────────────────────────────────────────────────


async def test_concurrent_versions_converge_on_one_artifact(env):
    (first, _) = await env.compose_two_episodes()
    await env.compose(VERSION_B, [first])
    results = await asyncio.gather(
        *(env.align_in(v, [first]) for v in [VERSION_A, VERSION_B] * 3)
    )
    assert len({r.aligned[0].model_dump_json() for r in results}) == 1
    assert (
        len(
            await env.records_of(
                ArtifactKind.ALIGNED_EPISODE_MANIFEST, first.episode_id
            )
        )
        == 1
    )
    objects = await env.objects()
    assert sum("/aligned_episodes/" in uri for uri in objects) == 1


async def test_versions_sharing_two_episodes_in_opposite_orders_do_not_deadlock(env):
    """A Job holds the record it registered until its commit. Held across the next
    registration, requests in opposite orders crossed and PostgreSQL killed one."""
    (first, second) = await env.compose_two_episodes()
    await env.compose(VERSION_B, [first, second])

    async def align(version: str, revisions: list):
        params = AlignEpisodeJobParams(
            episodes=[{"episode_id": r.episode_id} for r in revisions],
            dataset_id=env.dataset_id,
            dataset_version=version,
            alignment_config=ALIGNMENT,
        )
        async with get_async_sessionmaker()() as session:
            context = env.context(session)
            register = context.artifact_record_store.register

            async def held_open(**kwargs):
                result = await register(**kwargs)
                await asyncio.sleep(0.5)
                return result

            context.artifact_record_store.register = held_open
            return await AlignEpisodeJobHandler().run(
                JobHandlerRequest(
                    job=env.next_job(JobType.ALIGN_EPISODE, params),
                    params=params,
                    context=context,
                )
            )

    one, two = await asyncio.gather(
        align(VERSION_A, [first, second]), align(VERSION_B, [second, first])
    )
    # Results keep the order each request stated.
    assert [r.episode_id for r in one.aligned] == [first.episode_id, second.episode_id]
    assert [r.episode_id for r in two.aligned] == [second.episode_id, first.episode_id]
    by_episode = {r.episode_id: r.aligned_artifact_id for r in one.aligned}
    assert {r.episode_id: r.aligned_artifact_id for r in two.aligned} == by_episode


# ── temporal integrity and consumers ─────────────────────────────────────────────


async def test_the_reused_artifact_keeps_the_instance_its_manifest_issued(env):
    (first, second) = await env.compose_two_episodes()
    await env.compose(VERSION_B, [first, second])
    await env.align_in(VERSION_A, [first, second])
    refs = (await env.align_in(VERSION_B, [first, second])).aligned

    instances = []
    for ref in refs:
        revision = first if ref.episode_id == first.episode_id else second
        record, manifest_bytes = await env.manifest(revision)
        manifest = load_canonical_episode_manifest(manifest_bytes)
        artifact = load_aligned_episode_artifact(
            await env.store.read_bytes(ref.aligned_artifact_uri)
        )
        aligned = artifact.aligned_episode
        assert aligned.clock_instance == manifest.clock_instance(aligned.source_clock)
        assert aligned.clock_origin == ClockOrigin.UNIT
        assert artifact.source_revision.source_artifact_id == record.artifact_id
        assert artifact.source_revision.source_manifest_sha256 == (
            hashlib.sha256(manifest_bytes).hexdigest()
        )
        instances.append(aligned.clock_instance)
    # Unit-origin clocks of two LeRobot Episodes stay isolated through reuse.
    with pytest.raises(IncomparableClocksError):
        require_comparable(*instances)


async def test_learning_data_of_either_version_consumes_the_reused_artifact(env):
    (first, second) = await env.compose_two_episodes()
    await env.compose(VERSION_B, [first, second])
    await env.align_in(VERSION_A, [first, second])
    refs = (await env.align_in(VERSION_B, [first, second])).aligned

    exports = [await env.export_in(v, refs) for v in (VERSION_A, VERSION_B)]

    assert exports[0].export_id == exports[1].export_id
    assert exports[0].manifest_uri != exports[1].manifest_uri
    assert exports[0].manifest_artifact_id != exports[1].manifest_artifact_id
    objects = await env.objects()
    for export, version in zip(exports, (VERSION_A, VERSION_B)):
        manifest = LearningDataExportManifest.model_validate_json(
            objects[export.manifest_uri]
        )
        assert manifest.dataset_version == version
        assert {i.aligned_artifact_id for i in manifest.inputs} == {
            r.aligned_artifact_id for r in refs
        }
        await check_learning_export_tables(env.store, manifest)
        dataset = await SceneOpsDataset.open(
            learning_manifest=manifest,
            learning_manifest_checksum=export.manifest_artifact_id,
            artifact_store=env.store,
        )
        assert len(dataset) == 2
        for ref in refs:
            aligned = load_aligned_episode_artifact(
                objects[ref.aligned_artifact_uri]
            ).aligned_episode
            metadata = dataset.get_episode(
                EpisodeRef(
                    episode_id=ref.episode_id,
                    aligned_artifact_checksum=ref.aligned_artifact_checksum,
                )
            )
            assert metadata.clock_instance == aligned.clock_instance


# ── execution identity ───────────────────────────────────────────────────────────


async def test_execution_identity_follows_the_request_and_not_the_shared_output(env):
    """Each DatasetVersion's request is its own Job, because the membership check is
    part of what it asserts: a Job of A must not answer B. Identical requests dedup,
    and a request after a succeeded alignment of another recipe does not reuse it."""
    (first, _) = await env.compose_two_episodes()
    await env.compose(VERSION_B, [first])

    def request(version: str, recipe=ALIGNMENT) -> CreateJobRequest:
        return CreateJobRequest(
            type=JobType.ALIGN_EPISODE,
            dataset_id=env.dataset_id,
            dataset_version=version,
            params={
                "episodes": [{"episode_id": first.episode_id}],
                "alignment_config": recipe.model_dump(mode="json"),
            },
        )

    async def create(req):
        async with get_async_sessionmaker()() as session:
            job = await _service(session).create_job(req)
            await session.commit()
            return job

    a, a_again = await create(request(VERSION_A)), await create(request(VERSION_A))
    b = await create(request(VERSION_B))
    other = await create(request(VERSION_A, OTHER_RECIPE))

    assert a_again.job_id == a.job_id
    assert b.job_id != a.job_id and b.execution_key != a.execution_key
    assert other.job_id != a.job_id and other.execution_key != a.execution_key
    # Every one pinned to the same source content.
    assert {
        j.params["episodes"][0]["source_manifest_sha256"] for j in (a, b, other)
    } == {first.manifest_checksum.removeprefix("sha256:")}


async def test_the_stored_bytes_are_what_the_store_serialises(env):
    """The id computed before the write is the id of what the write stores."""
    (first, _) = await env.compose_two_episodes()
    ref = (await env.align_in(VERSION_A, [first])).aligned[0]
    stored = await env.store.read_bytes(ref.aligned_artifact_uri)
    assert canonical_document_bytes(load_aligned_episode_artifact(stored)) == stored
    registered = await env.record(ref.aligned_artifact_id)
    assert registered.size_bytes == len(stored)
    assert registered.checksum == ref.aligned_artifact_checksum
