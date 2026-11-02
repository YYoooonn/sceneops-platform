"""An upgrade of the AlignedEpisode artifact schema on real PostgreSQL and real
MinIO, with synthetic LeRobot Episodes built, registered and composed by the real
path: ALIGN_EPISODE writes beside an object of the old schema without touching
it, keeps each Episode's own clock instance, and converges when repeated; a
reader refuses the old object; a Job that succeeded under the old schema is not
returned for a request for the new one.

Requires the disposable database and bucket of `make test-integration`; skips
(and so fails there) otherwise.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy import text

from sceneops_core.artifacts.schemas import ArtifactKind, ArtifactOwnerType, ArtifactRef
from sceneops_core.common.derived_ids import aligned_episode_artifact_id
from sceneops_core.common.time import utc_now
from sceneops_core.datasets.schemas import DatasetRecord
from sceneops_core.episodes.alignment import (
    AlignedEpisodeArtifact,
    EpisodeSourceRevision,
    TemporalAlignmentConfig,
    TemporalSourceContext,
    UnsupportedAlignedArtifactSchemaError,
    align_episode,
    alignment_config_hash,
    alignment_key,
    load_aligned_episode_artifact,
)
from sceneops_core.episodes.schemas import load_canonical_episode_manifest
from sceneops_core.executions import compute_execution_key, params_for_execution_key
from sceneops_core.jobs.schemas import (
    AlignEpisodeJobParams,
    BuildSnapshotEpisodesJobParams,
    ComposeDatasetVersionJobParams,
    CreateJobRequest,
    JobManifest,
    JobStatus,
    JobType,
    ProfileAlignedEpisodeJobParams,
    ValidateAlignedEpisodeJobParams,
)
from sceneops_core.pipelines.schemas import (
    CreatePipelineRunRequest,
    PipelineRunStatus,
    PipelineType,
)
from sceneops_core.sources import SnapshotDeclaration, SnapshotSelection
from sceneops_core.temporal import (
    ClockOrigin,
    IncomparableClocksError,
    require_comparable,
)
from sceneops_db.postgres.artifacts import PostgresArtifactRefRepository
from sceneops_db.postgres.episodes import PostgresEpisodeRepository
from sceneops_db.postgres.jobs import PostgresJobEventRepository, PostgresJobRepository
from sceneops_db.postgres.pipelines import (
    PostgresPipelineRunRepository,
    PostgresPipelineTaskRunRepository,
)
from sceneops_db.session import get_async_sessionmaker
from sceneops_episodes.artifacts import EpisodeManifestWriteConflictError
from sceneops_execution.jobs.service import JobService
from sceneops_execution.pipelines.service import PipelineService
from sceneops_sources import publish_snapshot
from sceneops_sources.lerobot_synthetic import INCLUDE, write_lerobot_tree
from sceneops_storage.backends.s3 import S3ArtifactStore
from sceneops_worker.core import dependencies as dependencies_module
from sceneops_worker.core.dependencies import create_worker_context
from sceneops_worker.datasets.composition import compose_dataset_version
from sceneops_worker.episodes.registration import register_episodes
from sceneops_worker.jobs.base import JobHandlerRequest
from sceneops_worker.jobs.dataset.align_episode import AlignEpisodeJobHandler
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
from sceneops_worker.sources.registration import register_source_snapshot

# No next.* feature: alignment refuses transition streams.
BUILD_CONFIG = {
    "features": [
        {"feature": "observation.image", "role": "observation"},
        {"feature": "observation.state", "role": "state"},
        {"feature": "action", "role": "action"},
    ]
}
ALIGNMENT = TemporalAlignmentConfig(
    target_frequency_hz=10.0, tolerance_us=50_000, max_gap_us=200_000
)
VERSION = "aligned-upgrade"


def _v1_alignment_key(semantics_version: str, clock: str) -> str:
    """The recipe key of a schema v1 artifact, spelled out: no schema input."""
    payload = json.dumps(
        {
            "alignment_config_hash": alignment_config_hash(ALIGNMENT),
            "alignment_semantics_version": semantics_version,
            "source_clock": clock,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class _Env:
    def __init__(self, settings, run_id: str, root: Path) -> None:
        self.settings = settings
        self.run_id = run_id
        self.root = root
        self.store = S3ArtifactStore(settings=settings.artifact)
        self.dataset_id = f"ds-{run_id}"
        self.jobs = 0

    def context(self, session):
        return create_worker_context(session, settings=self.settings, worker_id="test")

    def next_job(self, job_type: JobType, params) -> JobManifest:
        self.jobs += 1
        return JobManifest(
            job_id=f"job-{self.run_id}-{self.jobs}",
            type=job_type,
            status=JobStatus.RUNNING,
            params=params.model_dump(mode="json"),
        )

    async def compose_two_episodes(self) -> list:
        """Two registered native Episodes of one content, in one DatasetVersion;
        returns their member revisions."""
        published = await publish_snapshot(
            store=self.store,
            sources_root=self.settings.artifact.source_root_uri,
            source_dir=self.root,
            declaration=SnapshotDeclaration(
                format="lerobot",
                upstream=f"test/{self.run_id}",
                upstream_revision="a",
                selection=SnapshotSelection(include=INCLUDE),
            ),
            annotations={"license": "mit"},
        )
        async with get_async_sessionmaker()() as session:
            registration = await register_source_snapshot(
                context=self.context(session), descriptor_uri=published.descriptor_uri
            )
        self.snapshot_id = registration.snapshot.snapshot_id
        params = BuildSnapshotEpisodesJobParams.model_validate(
            {
                "snapshot_id": self.snapshot_id,
                "build_config": BUILD_CONFIG,
            }
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
            revisions = await register_episodes(
                context=self.context(session),
                manifest_artifact_ids=built.manifest_artifact_ids,
            )
        pins = [r.pin.model_dump(mode="json") for r in revisions.revisions]
        async with get_async_sessionmaker()() as session:
            context = self.context(session)
            await context.dataset_store.create_dataset(
                DatasetRecord(dataset_id=self.dataset_id, name=self.run_id)
            )
            await context.commit()
            await compose_dataset_version(
                context=context,
                params=ComposeDatasetVersionJobParams.model_validate(
                    {
                        "dataset_id": self.dataset_id,
                        "dataset_version": VERSION,
                        "pins": pins,
                    }
                ),
            )
        return [r.revision for r in revisions.revisions]

    async def manifest(self, revision):
        async with get_async_sessionmaker()() as session:
            record = await self.context(session).artifact_record_store.get(
                revision.manifest_artifact_id
            )
        return record, await self.store.read_bytes(record.uri)

    async def seed_v1_artifact(self, revision) -> tuple[str, str, bytes]:
        """The object and ArtifactRecord an earlier deployment left: schema v1
        bytes at the v1 key. Returns (artifact id, uri, bytes)."""
        record, data = await self.manifest(revision)
        aligned = align_episode(
            load_canonical_episode_manifest(data),
            ALIGNMENT,
            TemporalSourceContext(
                source_clock=load_canonical_episode_manifest(data)
                .declared_window()
                .source_clock
            ),
            episode_id=revision.episode_id,
        )
        sha = hashlib.sha256(data).hexdigest()
        document = AlignedEpisodeArtifact(
            source_revision=EpisodeSourceRevision(
                episode_id=revision.episode_id,
                episode_manifest_uri=record.uri,
                source_artifact_id=record.artifact_id,
                source_manifest_sha256=sha,
            ),
            aligned_episode=aligned,
        ).to_artifact_dict()
        document["schemaVersion"] = "v1"
        del document["alignedEpisode"]["clockOrigin"]
        del document["alignedEpisode"]["clockScope"]
        old = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
        checksum = "sha256:" + hashlib.sha256(old).hexdigest()
        async with get_async_sessionmaker()() as session:
            context = self.context(session)
            key = _v1_alignment_key(
                aligned.alignment_semantics_version, aligned.source_clock
            )
            # Where that deployment wrote it: under the DatasetVersion.
            uri = (
                f"{self.settings.dataset_root_uri}/{self.dataset_id}/versions/{VERSION}"
                f"/episodes/{revision.episode_id}/aligned/{sha[:16]}/{key[:16]}.json"
            )
            await self.store.write_bytes(uri, old)
            artifact_id = aligned_episode_artifact_id(
                episode_id=revision.episode_id, checksum=checksum
            )
            await context.artifact_record_store.register(
                artifact_id=artifact_id,
                ref=ArtifactRef(
                    kind=ArtifactKind.ALIGNED_EPISODE_MANIFEST,
                    uri=uri,
                    media_type="application/json",
                    checksum=checksum,
                    size_bytes=len(old),
                ),
                owner_type=ArtifactOwnerType.EPISODE,
                owner_id=revision.episode_id,
                dataset_id=self.dataset_id,
                dataset_version=VERSION,
                job_id=f"job-{self.run_id}-v1",
            )
            await context.commit()
        return artifact_id, uri, old

    async def align(self, revisions):
        params = AlignEpisodeJobParams(
            episodes=[{"episode_id": r.episode_id} for r in revisions],
            dataset_id=self.dataset_id,
            dataset_version=VERSION,
            alignment_config=ALIGNMENT,
        )
        async with get_async_sessionmaker()() as session:
            return await AlignEpisodeJobHandler().run(
                JobHandlerRequest(
                    job=self.next_job(JobType.ALIGN_EPISODE, params),
                    params=params,
                    context=self.context(session),
                )
            )

    def _aligned_params(self, model, episode_id, artifact_id, checksum):
        return model(
            episode_id=episode_id,
            dataset_id=self.dataset_id,
            dataset_version=VERSION,
            aligned_artifact_id=artifact_id,
            aligned_artifact_checksum=checksum,
        )

    async def validate(self, episode_id, artifact_id, checksum=None):
        params = self._aligned_params(
            ValidateAlignedEpisodeJobParams, episode_id, artifact_id, checksum
        )
        async with get_async_sessionmaker()() as session:
            return await ValidateAlignedEpisodeJobHandler().run(
                JobHandlerRequest(
                    job=self.next_job(JobType.VALIDATE_ALIGNED_EPISODE, params),
                    params=params,
                    context=self.context(session),
                )
            )

    async def profile(self, episode_id, artifact_id, checksum=None):
        params = self._aligned_params(
            ProfileAlignedEpisodeJobParams, episode_id, artifact_id, checksum
        )
        async with get_async_sessionmaker()() as session:
            return await ProfileAlignedEpisodeJobHandler().run(
                JobHandlerRequest(
                    job=self.next_job(JobType.PROFILE_ALIGNED_EPISODE, params),
                    params=params,
                    context=self.context(session),
                )
            )


@pytest.fixture()
async def env(
    _fresh_database_connection, _minio_reachable, worker_settings, unique_id, tmp_path
):
    dependencies_module._artifact_store = None
    run_id = unique_id("aligned-upgrade")
    root = write_lerobot_tree(tmp_path / "tree", lengths=(3, 3))
    (root / "README.md").write_text(f"# {run_id}\n")
    yield _Env(worker_settings, run_id, root)
    dependencies_module._artifact_store = None


async def test_v2_lands_beside_v1_keeps_each_instance_and_converges(env):
    revisions = await env.compose_two_episodes()
    assert len(revisions) == 2
    first = revisions[0]
    old_id, old_uri, old_bytes = await env.seed_v1_artifact(first)

    result = await env.align(revisions)

    refs = {ref.episode_id: ref for ref in result.aligned}
    assert refs[first.episode_id].aligned_artifact_uri != old_uri
    assert refs[first.episode_id].aligned_artifact_id != old_id
    # The old object and its record are exactly what they were.
    assert await env.store.read_bytes(old_uri) == old_bytes
    async with get_async_sessionmaker()() as session:
        context = env.context(session)
        old_record = await context.artifact_record_store.get(old_id)
        assert old_record.uri == old_uri
        assert old_record.size_bytes == len(old_bytes)

    # The bytes in the bucket are the checksum the record pins, and they hold
    # the instance the source manifest issues.
    aligned = {}
    for revision in revisions:
        ref = refs[revision.episode_id]
        data = await env.store.read_bytes(ref.aligned_artifact_uri)
        assert ref.aligned_artifact_checksum == (
            "sha256:" + hashlib.sha256(data).hexdigest()
        )
        artifact = load_aligned_episode_artifact(data)
        assert artifact.schema_version == "v2"
        _, manifest_bytes = await env.manifest(revision)
        manifest = load_canonical_episode_manifest(manifest_bytes)
        episode = artifact.aligned_episode
        assert episode.clock_origin == ClockOrigin.UNIT
        assert episode.clock_instance == manifest.clock_instance(episode.source_clock)
        assert artifact.source_revision.source_artifact_id == (
            revision.manifest_artifact_id
        )
        aligned[revision.episode_id] = episode

    # Identical frame times, two Episodes: not comparable.
    one, two = aligned.values()
    assert [s.timestamp_us for s in one.steps] == [s.timestamp_us for s in two.steps]
    with pytest.raises(IncomparableClocksError):
        require_comparable(one.clock_instance, two.clock_instance)

    # Repeating the alignment converges on the same objects and records.
    again = await env.align(revisions)
    assert [
        (r.aligned_artifact_id, r.aligned_artifact_uri, r.aligned_artifact_checksum)
        for r in again.aligned
    ] == [
        (r.aligned_artifact_id, r.aligned_artifact_uri, r.aligned_artifact_checksum)
        for r in result.aligned
    ]


async def test_control_new_bytes_at_the_unchanged_key_conflict_on_real_minio(env):
    """What the schema input of the key exists for."""
    (first, _) = await env.compose_two_episodes()
    _, old_uri, old_bytes = await env.seed_v1_artifact(first)
    record, data = await env.manifest(first)
    manifest = load_canonical_episode_manifest(data)
    clock = manifest.declared_window().source_clock
    sha = hashlib.sha256(data).hexdigest()
    aligned = align_episode(
        manifest,
        ALIGNMENT,
        TemporalSourceContext(source_clock=clock),
        episode_id=first.episode_id,
    )
    artifact = AlignedEpisodeArtifact(
        source_revision=EpisodeSourceRevision(
            episode_id=first.episode_id,
            episode_manifest_uri=record.uri,
            source_artifact_id=record.artifact_id,
            source_manifest_sha256=sha,
        ),
        aligned_episode=aligned,
    )
    location = dict(
        episode_id=first.episode_id,
        source_manifest_sha256=sha,
    )
    async with get_async_sessionmaker()() as session:
        store = env.context(session).episode_artifact_store
        v1_key = _v1_alignment_key(aligned.alignment_semantics_version, clock)
        control_uri = store.aligned_episode_uri(alignment_key=v1_key, **location)
        await env.store.write_bytes(control_uri, old_bytes)
        with pytest.raises(EpisodeManifestWriteConflictError):
            await store.write_aligned_episode(
                alignment_key=v1_key, artifact=artifact, **location
            )
        assert await env.store.read_bytes(control_uri) == old_bytes
        assert await env.store.read_bytes(old_uri) == old_bytes

        # The same write at the current key lands, and a retry is a no-op.
        key = alignment_key(ALIGNMENT, aligned.alignment_semantics_version, clock)
        one = await store.write_aligned_episode(
            alignment_key=key, artifact=artifact, **location
        )
        two = await store.write_aligned_episode(
            alignment_key=key, artifact=artifact, **location
        )
        assert (one.uri, one.checksum) == (two.uri, two.checksum)
        assert one.uri != old_uri


async def test_readers_refuse_the_old_object_and_accept_the_new_one(env):
    revisions = await env.compose_two_episodes()
    first = revisions[0]
    old_id, _, old_bytes = await env.seed_v1_artifact(first)
    objects_before = len(await env.store.list_objects(env.settings.artifact_root_uri))

    for read in (env.validate, env.profile):
        with pytest.raises(UnsupportedAlignedArtifactSchemaError, match="'v1'"):
            await read(first.episode_id, old_id)
    assert (
        len(await env.store.list_objects(env.settings.artifact_root_uri))
        == objects_before
    )

    result = await env.align(revisions)
    ref = next(r for r in result.aligned if r.episode_id == first.episode_id)

    report = await env.validate(
        first.episode_id, ref.aligned_artifact_id, ref.aligned_artifact_checksum
    )
    assert report.valid is True
    await env.profile(
        first.episode_id, ref.aligned_artifact_id, ref.aligned_artifact_checksum
    )

    # The learning export reads v2 and is otherwise what it was.
    params = ExportLearningDataJobHandler().params_model(
        dataset_id=env.dataset_id,
        dataset_version=VERSION,
        inputs=[
            {
                "episode_id": r.episode_id,
                "aligned_artifact_id": r.aligned_artifact_id,
                "aligned_artifact_checksum": r.aligned_artifact_checksum,
            }
            for r in result.aligned
        ],
    )
    async with get_async_sessionmaker()() as session:
        exported = await ExportLearningDataJobHandler().run(
            JobHandlerRequest(
                job=env.next_job(JobType.EXPORT_LEARNING_DATA, params),
                params=params,
                context=env.context(session),
            )
        )
    assert exported.episode_count == 2


async def test_a_succeeded_old_schema_job_is_not_reused_on_real_postgres(env):
    revisions = await env.compose_two_episodes()
    request = CreateJobRequest(
        type=JobType.ALIGN_EPISODE,
        dataset_id=env.dataset_id,
        dataset_version=VERSION,
        params={
            "episodes": [{"episode_id": r.episode_id} for r in revisions],
            "alignment_config": ALIGNMENT.model_dump(mode="json", exclude_none=True),
        },
    )

    def service(session) -> JobService:
        return JobService(
            repository=PostgresJobRepository(session),
            event_repository=PostgresJobEventRepository(session),
            artifact_repository=PostgresArtifactRefRepository(session),
            episode_repository=PostgresEpisodeRepository(session),
        )

    # What the request resolves to, without leaving anything behind.
    async with get_async_sessionmaker()() as session:
        probe = await service(session).create_job(request)
        await session.rollback()

    legacy_params = params_for_execution_key(JobType.ALIGN_EPISODE, probe.params)
    del legacy_params["aligned_artifact_schema_version"]
    legacy_key = compute_execution_key(
        kind="job",
        type=JobType.ALIGN_EPISODE.value,
        dataset_id=env.dataset_id,
        dataset_version=VERSION,
        params=legacy_params,
    )
    assert legacy_key != probe.execution_key

    now = utc_now()
    async with get_async_sessionmaker()() as session:
        old = await PostgresJobRepository(session).create(
            probe.model_copy(
                update={
                    "job_id": f"job-{env.run_id}-old-schema",
                    "status": JobStatus.SUCCEEDED,
                    "execution_key": legacy_key,
                    "created_at": now,
                    "updated_at": now,
                }
            )
        )
        await session.commit()

    async with get_async_sessionmaker()() as session:
        current = await service(session).create_job(request)
        await session.commit()
    assert current.job_id != old.job_id
    assert current.execution_key == probe.execution_key
    assert current.status == JobStatus.PENDING

    # Within one schema, the request stays idempotent: in flight and once it
    # has succeeded.
    async with get_async_sessionmaker()() as session:
        assert (await service(session).create_job(request)).job_id == current.job_id
        await session.execute(
            text("UPDATE jobs SET status = :status WHERE job_id = :job_id"),
            {"status": JobStatus.SUCCEEDED.value, "job_id": current.job_id},
        )
        await session.commit()
    async with get_async_sessionmaker()() as session:
        assert (await service(session).create_job(request)).job_id == current.job_id


async def test_a_forced_pipeline_run_is_what_later_unforced_requests_return(env):
    """The documented upgrade procedure, on the real repository's ordering: an
    unforced request keeps returning the old run until a forced one exists, and
    the newest matching run from then on."""
    await env.compose_two_episodes()
    request = dict(
        type=PipelineType.EPISODE_LEARNING_DATA_BUILDING,
        dataset_id=env.dataset_id,
        dataset_version=VERSION,
        params={"align_episode": {"run": env.run_id}},
    )

    async def create(**overrides) -> str:
        async with get_async_sessionmaker()() as session:
            run = await PipelineService(
                pipeline_repository=PostgresPipelineRunRepository(session),
                task_repository=PostgresPipelineTaskRunRepository(session),
            ).create_pipeline_run(CreatePipelineRunRequest(**request, **overrides))
            await session.commit()
            return run.pipeline_run.pipeline_run_id

    async def succeed(pipeline_run_id: str) -> None:
        async with get_async_sessionmaker()() as session:
            await session.execute(
                text(
                    "UPDATE pipeline_runs SET status = :status "
                    "WHERE pipeline_run_id = :id"
                ),
                {"status": PipelineRunStatus.SUCCEEDED.value, "id": pipeline_run_id},
            )
            await session.commit()

    old = await create()
    await succeed(old)
    assert await create() == old

    forced = await create(force=True)
    assert forced != old
    await succeed(forced)

    assert await create() == forced
