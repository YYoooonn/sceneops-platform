"""An upgrade of the learning data schema on real PostgreSQL and real MinIO, with
synthetic LeRobot Episodes built, registered, composed and aligned by the real
path, and the objects and Job rows an earlier deployment left behind:

* a Job that succeeded before its contract entered the key is not the answer to a
  request now, and running the request for real refuses what today's readers refuse;
* a repeated current request converges on one Job, and its bytes on one object;
* a v2 export lands beside the v1 one, which stays exactly as it was;
* a v1 export is never an incremental base, and a refused request writes nothing.

The objects of the old export are the exact bytes the old writer produced (see
``sceneops_analytics.testing.learning_migration``). Handlers run in process here:
this proves storage, database and reuse semantics, not a deployed worker.

Requires the disposable database and bucket of `make test-integration`; skips (and
so fails there) otherwise.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest
from sqlalchemy import text

from sceneops_analytics import (
    AnalyticsTableWriter,
    SceneOpsDataset,
    check_learning_export_tables,
)
from sceneops_analytics.testing.learning_migration import write_v1_export
from sceneops_core.artifacts.schemas.enums import ArtifactKind
from sceneops_core.artifacts.schemas.owner import ArtifactOwnerType
from sceneops_core.artifacts.schemas.refs import ArtifactRef
from sceneops_core.common.derived_ids import derived_artifact_id
from sceneops_core.common.time import utc_now
from sceneops_core.episodes.alignment import (
    AlignedEpisodeArtifact,
    UnsupportedAlignedArtifactSchemaError,
    load_aligned_episode_artifact,
)
from sceneops_core.episodes.learning import EpisodeRef
from sceneops_core.episodes.learning_export import (
    IncrementalExportUnsupportedBaseError,
    LearningDataExportManifest,
    UnsupportedLearningDataSchemaError,
)
from sceneops_core.executions import compute_execution_key, params_for_execution_key
from sceneops_core.jobs.schemas import (
    CreateJobRequest,
    JobManifest,
    JobStatus,
    JobType,
)
from sceneops_core.pipelines.schemas import (
    CreatePipelineRunRequest,
    PipelineRunStatus,
    PipelineType,
)
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
from sceneops_execution.jobs.service import JobService
from sceneops_execution.pipelines.service import PipelineService
from sceneops_sources.lerobot_synthetic import write_lerobot_tree
from sceneops_worker.core import dependencies as dependencies_module
from sceneops_worker.jobs.base import JobHandlerRequest
from sceneops_worker.jobs.dataset.curate_episodes import CurateEpisodesJobHandler
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
from test_aligned_schema_upgrade_integration import VERSION, _Env  # noqa: E402

ANALYSES = {
    JobType.VALIDATE_ALIGNED_EPISODE: ValidateAlignedEpisodeJobHandler,
    JobType.PROFILE_ALIGNED_EPISODE: ProfileAlignedEpisodeJobHandler,
}


def _service(session) -> JobService:
    return JobService(
        repository=PostgresJobRepository(session),
        event_repository=PostgresJobEventRepository(session),
        artifact_repository=PostgresArtifactRefRepository(session),
        episode_repository=PostgresEpisodeRepository(session),
    )


class _LearningEnv(_Env):
    @property
    def writer(self) -> AnalyticsTableWriter:
        return AnalyticsTableWriter(
            artifact_store=self.store, root_uri=self.settings.analytics_root_uri
        )

    async def objects(self) -> dict[str, bytes]:
        return {
            item.uri: await self.store.read_bytes(item.uri)
            for item in await self.store.list_objects(self.settings.artifact_root_uri)
        }

    async def artifact_rows(self) -> int:
        async with get_async_sessionmaker()() as session:
            result = await session.execute(
                text("SELECT count(*) FROM artifacts WHERE dataset_id = :id"),
                {"id": self.dataset_id},
            )
            return result.scalar_one()

    async def create(self, request: CreateJobRequest) -> JobManifest:
        async with get_async_sessionmaker()() as session:
            job = await _service(session).create_job(request)
            await session.commit()
            return job

    async def seed_succeeded_job(
        self, request: CreateJobRequest, *, legacy_key: str
    ) -> JobManifest:
        """The Job row an earlier deployment left: this request, succeeded, under
        the key the request had then."""
        async with get_async_sessionmaker()() as session:
            probe = await _service(session).create_job(request)
            await session.rollback()
        assert probe.execution_key != legacy_key
        now = utc_now()
        self.jobs += 1
        async with get_async_sessionmaker()() as session:
            old = await PostgresJobRepository(session).create(
                probe.model_copy(
                    update={
                        "job_id": f"job-{self.run_id}-old-{self.jobs}",
                        "status": JobStatus.SUCCEEDED,
                        "execution_key": legacy_key,
                        "created_at": now,
                        "updated_at": now,
                    }
                )
            )
            await session.commit()
        return old

    def legacy_key(self, job_type: JobType, params: dict) -> str:
        return compute_execution_key(
            kind="job",
            type=job_type.value,
            dataset_id=self.dataset_id,
            dataset_version=VERSION,
            params=params,
        )

    async def succeed(self, job: JobManifest) -> None:
        async with get_async_sessionmaker()() as session:
            await session.execute(
                text("UPDATE jobs SET status = :status WHERE job_id = :job_id"),
                {"status": JobStatus.SUCCEEDED.value, "job_id": job.job_id},
            )
            await session.commit()

    async def run_job(self, handler_type, job: JobManifest):
        handler = handler_type()
        params = handler.params_model.model_validate(job.params)
        async with get_async_sessionmaker()() as session:
            return await handler.run(
                JobHandlerRequest(job=job, params=params, context=self.context(session))
            )

    def analysis_request(self, job_type, episode_id, artifact_id) -> CreateJobRequest:
        return CreateJobRequest(
            type=job_type,
            dataset_id=self.dataset_id,
            dataset_version=VERSION,
            params={"episode_id": episode_id, "aligned_artifact_id": artifact_id},
        )

    def curate_request(self, manifest_artifact_id: str) -> CreateJobRequest:
        return CreateJobRequest(
            type=JobType.CURATE_EPISODES,
            dataset_id=self.dataset_id,
            dataset_version=VERSION,
            params={
                "learning_data_export_manifest_artifact_id": manifest_artifact_id,
                "policy": {},
            },
        )

    async def register_export_manifest(self, uri: str, data: bytes) -> str:
        """The ArtifactRecord an earlier deployment left beside its manifest."""
        checksum = "sha256:" + hashlib.sha256(data).hexdigest()
        artifact_id = derived_artifact_id(
            prefix="lexport", logical_id=json.loads(data)["exportId"], checksum=checksum
        )
        async with get_async_sessionmaker()() as session:
            context = self.context(session)
            await context.artifact_record_store.register(
                artifact_id=artifact_id,
                ref=ArtifactRef(
                    kind=ArtifactKind.LEARNING_DATA_EXPORT_MANIFEST,
                    uri=uri,
                    media_type="application/json",
                    checksum=checksum,
                    size_bytes=len(data),
                ),
                owner_type=ArtifactOwnerType.DATASET_VERSION,
                owner_id=f"{self.dataset_id}:{VERSION}",
                dataset_id=self.dataset_id,
                dataset_version=VERSION,
            )
            await session.commit()
        return artifact_id

    def export_request(self, inputs, **params) -> CreateJobRequest:
        return CreateJobRequest(
            type=JobType.EXPORT_LEARNING_DATA,
            dataset_id=self.dataset_id,
            dataset_version=VERSION,
            params={"inputs": inputs, **params},
        )

    async def aligned_entries(self, result) -> list[tuple[str, AlignedEpisodeArtifact]]:
        return [
            (
                ref.aligned_artifact_checksum,
                load_aligned_episode_artifact(
                    await self.store.read_bytes(ref.aligned_artifact_uri)
                ),
            )
            for ref in result.aligned
        ]


def _inputs(result) -> list[dict]:
    return [
        {
            "episode_id": ref.episode_id,
            "aligned_artifact_id": ref.aligned_artifact_id,
            "aligned_artifact_checksum": ref.aligned_artifact_checksum,
        }
        for ref in result.aligned
    ]


@pytest.fixture()
async def env(
    _fresh_database_connection, _minio_reachable, worker_settings, unique_id, tmp_path
):
    dependencies_module._artifact_store = None
    run_id = unique_id("learning-upgrade")
    root = write_lerobot_tree(tmp_path / "tree", lengths=(3, 3))
    (root / "README.md").write_text(f"# {run_id}\n")
    yield _LearningEnv(worker_settings, run_id, root)
    dependencies_module._artifact_store = None


@pytest.mark.parametrize("job_type", list(ANALYSES))
async def test_a_succeeded_v1_era_analysis_job_is_not_the_current_result(env, job_type):
    """What the deployed acceptance of the aligned schema measured: the old Job
    answered with ``valid: true`` for an object today's reader refuses."""
    (first, _) = await env.compose_two_episodes()
    old_id, old_uri, old_bytes = await env.seed_v1_artifact(first)
    request = env.analysis_request(job_type, first.episode_id, old_id)
    objects = await env.objects()

    # The transform of that deployment: lineage stripped, nothing injected.
    async with get_async_sessionmaker()() as session:
        probe = await _service(session).create_job(request)
        await session.rollback()
    legacy_params = {
        k: v for k, v in probe.params.items() if k != "aligned_artifact_id"
    }
    old = await env.seed_succeeded_job(
        request, legacy_key=env.legacy_key(job_type, legacy_params)
    )

    current = await env.create(request)
    assert current.job_id != old.job_id
    assert current.status == JobStatus.PENDING
    assert current.execution_key != old.execution_key

    # Running it for real refuses the object, and leaves it and everything else.
    with pytest.raises(UnsupportedAlignedArtifactSchemaError, match="'v1'"):
        await env.run_job(ANALYSES[job_type], current)
    assert await env.objects() == objects
    assert await env.store.read_bytes(old_uri) == old_bytes


@pytest.mark.parametrize("job_type", list(ANALYSES))
async def test_a_current_analysis_job_is_idempotent_and_v2_still_passes(env, job_type):
    revisions = await env.compose_two_episodes()
    ref = next(iter((await env.align(revisions)).aligned))
    request = env.analysis_request(job_type, ref.episode_id, ref.aligned_artifact_id)

    first = await env.create(request)
    result = await env.run_job(ANALYSES[job_type], first)
    if job_type == JobType.VALIDATE_ALIGNED_EPISODE:
        assert result.valid is True
    await env.succeed(first)
    objects = await env.objects()

    again = await env.create(request)
    assert again.job_id == first.job_id
    assert again.status == JobStatus.SUCCEEDED

    # A forced repeat writes nothing new: its report is the same bytes.
    forced = await env.create(request.model_copy(update={"force": True}))
    assert forced.job_id != first.job_id
    await env.run_job(ANALYSES[job_type], forced)
    assert await env.objects() == objects


async def test_a_succeeded_v1_export_job_is_not_returned_and_v2_lands_beside_it(env):
    revisions = await env.compose_two_episodes()
    aligned = await env.align(revisions)
    entries = await env.aligned_entries(aligned)
    v1_uri, v1_bytes = await write_v1_export(
        env.writer, dataset_id=env.dataset_id, dataset_version=VERSION, entries=entries
    )
    v1_export_id = json.loads(v1_bytes)["exportId"]
    v1_objects = await env.objects()

    request = env.export_request(_inputs(aligned))
    async with get_async_sessionmaker()() as session:
        probe = await _service(session).create_job(request)
        await session.rollback()
    legacy_inputs = sorted(
        (
            {k: v for k, v in item.items() if k != "aligned_artifact_id"}
            for item in probe.params["inputs"]
        ),
        key=lambda i: (i["episode_id"], i["aligned_artifact_checksum"]),
    )
    old = await env.seed_succeeded_job(
        request,
        legacy_key=env.legacy_key(
            JobType.EXPORT_LEARNING_DATA, {**probe.params, "inputs": legacy_inputs}
        ),
    )

    current = await env.create(request)
    assert current.job_id != old.job_id
    assert current.status == JobStatus.PENDING

    rows_before = await env.artifact_rows()
    result = await env.run_job(ExportLearningDataJobHandler, current)

    # New identity, new objects, and the old export is exactly what it was.
    assert result.export_id != v1_export_id
    assert result.manifest_uri != v1_uri
    objects = await env.objects()
    for uri, data in v1_objects.items():
        assert objects[uri] == data
    new_uris = objects.keys() - v1_objects.keys()
    assert all(result.export_id[:16] in uri for uri in new_uris)
    assert len(new_uris) == 4  # learning_episodes, a shard per table, the manifest

    # Read back from MinIO: footers through ranged reads, then the whole contract.
    data = objects[result.manifest_uri]
    manifest = LearningDataExportManifest.model_validate_json(data)
    assert manifest.schema_version == "v2"
    await check_learning_export_tables(env.store, manifest)
    dataset = await SceneOpsDataset.open(
        learning_manifest=manifest,
        learning_manifest_checksum=result.manifest_artifact_id,
        artifact_store=env.store,
    )
    assert len(dataset) == 2

    # Lineage: the aligned revisions pinned, the source revisions they name.
    assert {
        (i.episode_id, i.aligned_artifact_id, i.aligned_artifact_checksum)
        for i in manifest.inputs
    } == {
        (r.episode_id, r.aligned_artifact_id, r.aligned_artifact_checksum)
        for r in aligned.aligned
    }
    by_id = {r.episode_id: r for r in revisions}
    instances = []
    for checksum, artifact in entries:
        episode = artifact.aligned_episode
        assert artifact.source_revision.source_artifact_id == (
            by_id[episode.episode_id].manifest_artifact_id
        )
        metadata = dataset.get_episode(
            EpisodeRef(
                episode_id=episode.episode_id, aligned_artifact_checksum=checksum
            )
        )
        assert metadata.clock_instance == episode.clock_instance
        assert metadata.clock_origin == ClockOrigin.UNIT
        instances.append(metadata.clock_instance)
        for index, step in enumerate(episode.steps):
            restored = await dataset.get_step(metadata.episode_ref, index)
            assert restored.timestamp_us == step.timestamp_us
    with pytest.raises(IncomparableClocksError):
        require_comparable(*instances)

    # Repeats converge: one Job, the same objects and the same records.
    await env.succeed(current)
    rows_after = await env.artifact_rows()
    assert rows_after == rows_before + 4
    assert (await env.create(request)).job_id == current.job_id
    forced = await env.create(request.model_copy(update={"force": True}))
    assert forced.job_id != current.job_id
    repeat = await env.run_job(ExportLearningDataJobHandler, forced)
    assert repeat.export_id == result.export_id
    assert repeat.manifest_artifact_id == result.manifest_artifact_id
    assert repeat.manifest_uri == result.manifest_uri
    assert await env.objects() == objects
    assert await env.artifact_rows() == rows_after


async def test_a_v1_aligned_pin_gets_a_new_export_job_and_is_refused(env):
    revisions = await env.compose_two_episodes()
    old_id, _, _ = await env.seed_v1_artifact(revisions[0])
    request = env.export_request(
        [{"episode_id": revisions[0].episode_id, "aligned_artifact_id": old_id}]
    )
    objects = await env.objects()
    job = await env.create(request)
    with pytest.raises(UnsupportedAlignedArtifactSchemaError, match="'v1'"):
        await env.run_job(ExportLearningDataJobHandler, job)
    assert await env.objects() == objects


async def test_a_succeeded_curation_of_a_v1_export_is_not_the_current_result(env):
    """CURATE's handler reads the export under the schema of its day, and its key said
    nothing about that schema: an unforced request returned the old Job, ``selected``
    decisions included, for an export today's reader refuses."""
    revisions = await env.compose_two_episodes()
    entries = await env.aligned_entries(await env.align(revisions))
    uri, data = await write_v1_export(
        env.writer, dataset_id=env.dataset_id, dataset_version=VERSION, entries=entries
    )
    manifest_id = await env.register_export_manifest(uri, data)
    request = env.curate_request(manifest_id)
    objects = await env.objects()

    # The transform of that deployment: nothing of what this change injects.
    async with get_async_sessionmaker()() as session:
        probe = await _service(session).create_job(request)
        await session.rollback()
    injected = ("learning_data_schema_version", "curation_semantics_version")
    legacy_params = {
        k: v
        for k, v in params_for_execution_key(
            JobType.CURATE_EPISODES, probe.params
        ).items()
        if k not in injected
    }
    old = await env.seed_succeeded_job(
        request, legacy_key=env.legacy_key(JobType.CURATE_EPISODES, legacy_params)
    )

    current = await env.create(request)
    assert current.job_id != old.job_id
    assert current.status == JobStatus.PENDING

    with pytest.raises(UnsupportedLearningDataSchemaError, match="'v1'"):
        await env.run_job(CurateEpisodesJobHandler, current)
    assert await env.objects() == objects


async def test_a_curation_of_a_current_export_converges_across_the_key_change(env):
    """A Job that succeeded under the earlier key is run once more, and because the
    export is current it reaches the same curation, object and record."""
    revisions = await env.compose_two_episodes()
    export = await env.run_job(
        ExportLearningDataJobHandler,
        await env.create(env.export_request(_inputs(await env.align(revisions)))),
    )
    request = env.curate_request(export.manifest_artifact_id)

    first = await env.create(request)
    result = await env.run_job(CurateEpisodesJobHandler, first)
    await env.succeed(first)
    objects = await env.objects()
    rows = await env.artifact_rows()

    again = await env.create(request)
    assert (again.job_id, again.status) == (first.job_id, JobStatus.SUCCEEDED)

    forced = await env.create(request.model_copy(update={"force": True}))
    assert forced.job_id != first.job_id
    repeat = await env.run_job(CurateEpisodesJobHandler, forced)
    assert repeat.curation_id == result.curation_id
    assert repeat.manifest_artifact_id == result.manifest_artifact_id
    assert await env.objects() == objects
    assert await env.artifact_rows() == rows


async def test_a_v1_base_is_refused_before_reuse_and_writes_nothing(env):
    revisions = await env.compose_two_episodes()
    aligned = await env.align(revisions)
    entries = await env.aligned_entries(aligned)
    base = entries[:1]
    _, v1_bytes = await write_v1_export(
        env.writer, dataset_id=env.dataset_id, dataset_version=VERSION, entries=base
    )
    objects = await env.objects()
    rows = await env.artifact_rows()

    job = await env.create(
        env.export_request(
            _inputs(aligned)[1:], base_export_id=json.loads(v1_bytes)["exportId"]
        )
    )
    with pytest.raises(IncrementalExportUnsupportedBaseError, match="'v1'"):
        await env.run_job(ExportLearningDataJobHandler, job)

    assert await env.objects() == objects
    assert await env.artifact_rows() == rows


async def test_a_v2_base_reuses_its_shards_on_real_minio(env):
    revisions = await env.compose_two_episodes()
    aligned = await env.align(revisions)
    inputs = _inputs(aligned)

    base_job = await env.create(env.export_request(inputs[:1]))
    base = await env.run_job(ExportLearningDataJobHandler, base_job)
    base_objects = await env.objects()

    job = await env.create(
        env.export_request(inputs[1:], base_export_id=base.export_id)
    )
    result = await env.run_job(ExportLearningDataJobHandler, job)
    assert result.reused_shard_counts == {"learning_steps": 1, "learning_signals": 1}
    assert result.new_shard_counts == {"learning_steps": 1, "learning_signals": 1}

    objects = await env.objects()
    for uri, data in base_objects.items():
        assert objects[uri] == data
    manifest = LearningDataExportManifest.model_validate_json(
        objects[result.manifest_uri]
    )
    await check_learning_export_tables(env.store, manifest)
    dataset = await SceneOpsDataset.open(
        learning_manifest=manifest,
        learning_manifest_checksum=result.manifest_artifact_id,
        artifact_store=env.store,
    )
    for checksum, artifact in await env.aligned_entries(aligned):
        metadata = dataset.get_episode(
            EpisodeRef(
                episode_id=artifact.aligned_episode.episode_id,
                aligned_artifact_checksum=checksum,
            )
        )
        assert metadata.clock_instance == artifact.aligned_episode.clock_instance
        await dataset.get_step(metadata.episode_ref, 0)


async def test_pipeline_runs_still_reuse_by_request_and_readers_refuse_what_they_name(
    env,
):
    """The limitation, as documented: version-aware Job keys do not reach a
    PipelineRun, whose key hashes its request, and its task Jobs carry none. An
    identical unforced request gets the old run; a forced one is what later ones
    get; and whatever the old run named is refused at its next read."""
    revisions = await env.compose_two_episodes()
    aligned = await env.align(revisions)
    _, v1_bytes = await write_v1_export(
        env.writer,
        dataset_id=env.dataset_id,
        dataset_version=VERSION,
        entries=await env.aligned_entries(aligned),
    )
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

    with pytest.raises(UnsupportedLearningDataSchemaError, match="'v1'"):
        LearningDataExportManifest.model_validate_json(v1_bytes)

    forced = await create(force=True)
    assert forced != old
    await succeed(forced)
    assert await create() == forced
