from __future__ import annotations

from dataclasses import dataclass

from sceneops_core.artifacts.schemas.enums import ArtifactKind
from sceneops_core.artifacts.schemas.owner import ArtifactOwnerType
from sceneops_core.artifacts.schemas.refs import ArtifactRef
from sceneops_core.common.ids import generate_artifact_id
from sceneops_core.common.schemas import JsonDict
from sceneops_core.datasets.schemas.records import DatasetVersionRecord
from sceneops_core.jobs.schemas import (
    BuildEpisodesJobParams,
    BuildEpisodesJobResult,
    JobManifest,
    JobType,
)
from sceneops_core.pipelines.schemas import PipelineTaskInputs
from sceneops_core.robots.schemas import RobotRunRecord, RobotRunStatus
from sceneops_worker.core.context import WorkerContext
from sceneops_worker.datasets.ingestion.rosbag_raw_log import RosbagAdapter
from sceneops_worker.episodes.artifacts import EpisodeArtifactStore
from sceneops_worker.episodes.building import EpisodeBuildResult, EpisodeBuilder
from sceneops_worker.jobs.base import JobHandler, JobHandlerRequest
from sceneops_worker.observations.artifacts import ObservationArtifactStore


@dataclass(frozen=True)
class BuildEpisodesExecution:
    """Resolved inputs and collaborators for one build_episodes handler invocation."""

    job: JobManifest
    params: BuildEpisodesJobParams
    context: WorkerContext
    raw_log_id: str
    mcap_uri: str
    robot_run: RobotRunRecord | None
    obs_store: ObservationArtifactStore
    episode_artifact_store: EpisodeArtifactStore
    dataset_version_record: DatasetVersionRecord
    version_root_uri: str


class BuildEpisodesJobHandler(
    JobHandler[BuildEpisodesJobParams, BuildEpisodesJobResult]
):
    """Robot rosbag/MCAP -> episodes.

    Separate from BuildScenesJobHandler (which may also consume the same
    rosbag via RosbagAdapter to build spatial SceneRecords) — this handler
    reads the same file's robot-state and mission topics instead, and
    segments them into task-oriented EpisodeRecords by Mission boundaries
    (see EpisodeBuilder). Both handlers construct their own independent
    RosbagAdapter instance; reading the same MCAP file twice for two
    different purposes is intentional, not a bug.
    """

    @property
    def job_type(self) -> JobType:
        return JobType.BUILD_EPISODES

    @property
    def params_model(self) -> type[BuildEpisodesJobParams]:
        return BuildEpisodesJobParams

    def build_job_params(self, inputs: PipelineTaskInputs) -> JsonDict:
        return {
            "dataset_id": inputs.dataset.dataset_id if inputs.dataset else None,
            "dataset_version": inputs.dataset.dataset_version
            if inputs.dataset
            else None,
            **inputs.params,
        }

    # ── orchestration ──────────────────────────────────────────────────────────

    async def run(
        self,
        request: JobHandlerRequest[BuildEpisodesJobParams],
    ) -> BuildEpisodesJobResult:
        params = request.params
        context = request.context

        dataset_id = params.dataset_id or context.default_dataset_id
        dataset_version = params.dataset_version or context.default_dataset_version
        version_record = await self._require_version(
            context, dataset_id, dataset_version
        )

        robot_run, mcap_uri = await self._resolve_source(context, params)

        execution = self._prepare_execution(
            request,
            version_record=version_record,
            robot_run=robot_run,
            mcap_uri=mcap_uri,
        )

        adapter = RosbagAdapter(
            source_store=context.raw_source_store,
            source_root_uri=execution.mcap_uri,
            observation_store=execution.obs_store,
        )

        (
            _,
            frame_index,
            raw_manifest_uri,
            raw_frame_index_uri,
        ) = await adapter.build_raw_log(
            dataset_id=version_record.dataset_id,
            dataset_version=version_record.version,
            raw_log_id=execution.raw_log_id,
            version_root_uri=execution.version_root_uri,
            params={},
        )
        robot_states = adapter.extract_robot_states(
            robot_id=params.robot_id, robot_run_id=params.robot_run_id
        )
        missions = adapter.extract_missions(
            robot_id=params.robot_id, robot_run_id=params.robot_run_id
        )

        build_result = EpisodeBuilder().build(
            dataset_id=version_record.dataset_id,
            dataset_version=version_record.version,
            raw_log_id=execution.raw_log_id,
            robot_id=params.robot_id,
            robot_run_id=params.robot_run_id,
            frame_index=frame_index,
            robot_states=robot_states,
            missions=missions,
        )

        episode_manifest_uris = await self._write_episode_manifests(
            execution, build_result
        )

        await self._register_episode_artifacts(
            execution, build_result, episode_manifest_uris
        )

        await self._update_dataset_version_episode_count(execution, build_result)

        if execution.robot_run is not None:
            await context.robot_store.save_run(
                execution.robot_run.model_copy(
                    update={"status": RobotRunStatus.INGESTED}
                )
            )

        await context.commit()

        return BuildEpisodesJobResult(
            raw_log_id=execution.raw_log_id,
            episode_ids=[e.episode_id for e in build_result.episodes],
            episode_manifest_uris=episode_manifest_uris,
            episode_count=build_result.episode_count,
            observation_frame_count=build_result.observation_frame_count,
            action_frame_count=build_result.action_frame_count,
            raw_log_manifest_uri=raw_manifest_uri,
            raw_log_frame_index_uri=raw_frame_index_uri,
            channels=sorted({frame.channel for frame in frame_index.frames}),
        )

    # ── version / source resolution ────────────────────────────────────────────

    @staticmethod
    async def _require_version(
        context: WorkerContext,
        dataset_id: str,
        dataset_version: str,
    ) -> DatasetVersionRecord:
        version = await context.dataset_store.get_version(
            dataset_id=dataset_id, version=dataset_version
        )
        if version is None:
            raise ValueError(
                f"Dataset version not registered: {dataset_id}/{dataset_version}"
            )
        return version

    @staticmethod
    async def _resolve_source(
        context: WorkerContext,
        params: BuildEpisodesJobParams,
    ) -> tuple[RobotRunRecord | None, str]:
        robot_run: RobotRunRecord | None = None
        mcap_uri = params.mcap_uri
        if params.robot_run_id is not None:
            robot_run = await context.robot_store.get_run(params.robot_run_id)
            if robot_run is None:
                raise ValueError(f"RobotRun not found: {params.robot_run_id}")
            mcap_uri = mcap_uri or robot_run.mcap_uri or robot_run.rosbag_uri
        if not mcap_uri:
            raise ValueError(
                "build_episodes requires mcap_uri, or a robot_run_id whose "
                "RobotRun has mcap_uri/rosbag_uri set."
            )
        return robot_run, mcap_uri

    # ── setup ──────────────────────────────────────────────────────────────────

    def _prepare_execution(
        self,
        request: JobHandlerRequest[BuildEpisodesJobParams],
        *,
        version_record: DatasetVersionRecord,
        robot_run: RobotRunRecord | None,
        mcap_uri: str,
    ) -> BuildEpisodesExecution:
        context = request.context
        params = request.params

        obs_store = ObservationArtifactStore(
            artifact_store=context.artifact_store,
            dataset_root_uri=context.settings.dataset_root_uri,
        )
        raw_log_id = (
            params.raw_log_id
            or f"{version_record.dataset_id}-{version_record.version}-episodes"
        )
        version_root_uri = context.dataset_artifact_store.dataset_version_root_uri(
            dataset_id=version_record.dataset_id,
            dataset_version=version_record.version,
        )

        return BuildEpisodesExecution(
            job=request.job,
            params=params,
            context=context,
            raw_log_id=raw_log_id,
            mcap_uri=mcap_uri,
            robot_run=robot_run,
            obs_store=obs_store,
            episode_artifact_store=context.episode_artifact_store,
            dataset_version_record=version_record,
            version_root_uri=version_root_uri,
        )

    # ── episode manifest persistence ────────────────────────────────────────────

    @staticmethod
    async def _write_episode_manifests(
        execution: BuildEpisodesExecution,
        build_result: EpisodeBuildResult,
    ) -> list[str]:
        uris: list[str] = []
        for manifest in build_result.episodes:
            uri = await execution.episode_artifact_store.write_episode_manifest(
                dataset_id=execution.dataset_version_record.dataset_id,
                dataset_version=execution.dataset_version_record.version,
                episode_id=manifest.episode_id,
                manifest=manifest,
            )
            uris.append(uri)
        return uris

    async def _register_episode_artifacts(
        self,
        execution: BuildEpisodesExecution,
        build_result: EpisodeBuildResult,
        episode_manifest_uris: list[str],
    ) -> None:
        context = execution.context
        version_record = execution.dataset_version_record
        for manifest, uri in zip(build_result.episodes, episode_manifest_uris):
            await context.artifact_record_store.create(
                artifact_id=generate_artifact_id(),
                ref=ArtifactRef(
                    kind=ArtifactKind.EPISODE_MANIFEST,
                    uri=uri,
                    media_type="application/json",
                ),
                owner_type=ArtifactOwnerType.EPISODE,
                owner_id=manifest.episode_id,
                dataset_id=version_record.dataset_id,
                dataset_version=version_record.version,
                job_id=execution.job.job_id,
                pipeline_run_id=execution.job.pipeline_run_id,
            )

    @staticmethod
    async def _update_dataset_version_episode_count(
        execution: BuildEpisodesExecution,
        build_result: EpisodeBuildResult,
    ) -> None:
        # Snapshot at build time, same convention/limitation as
        # BuildScenesJobHandler._mark_dataset_version_ingested's scene_count.
        version = execution.dataset_version_record
        await execution.context.dataset_store.update_episode_summary(
            dataset_id=version.dataset_id,
            version=version.version,
            episode_count=build_result.episode_count,
        )
