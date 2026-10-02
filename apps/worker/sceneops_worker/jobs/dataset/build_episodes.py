from __future__ import annotations

from dataclasses import dataclass

from sceneops_core.artifacts.schemas.enums import ArtifactKind
from sceneops_core.artifacts.schemas.owner import ArtifactOwnerType
from sceneops_core.artifacts.schemas.refs import ArtifactRef
from sceneops_core.common.ids import generate_artifact_id
from sceneops_core.common.schemas import JsonDict
from sceneops_core.datasets.schemas.records import DatasetVersionRecord
from sceneops_core.episodes.schemas import EpisodeSegmentationStrategy, EpisodeSource
from sceneops_core.jobs.schemas import (
    BuildEpisodesJobParams,
    BuildEpisodesJobResult,
    JobManifest,
    JobType,
)
from sceneops_core.pipelines.schemas import PipelineTaskInputs
from sceneops_worker.core.context import WorkerContext
from sceneops_worker.datasets.ingestion.rosbag_raw_log import RosbagAdapter
from sceneops_worker.episodes.artifacts import (
    EpisodeArtifactStore,
    EpisodeArtifactWriteResult,
)
from sceneops_worker.episodes.building import (
    EpisodeBuildResult,
    EpisodeBuilder,
    EpisodeSegmenter,
)
from sceneops_worker.jobs.base import JobHandler, JobHandlerRequest
from sceneops_worker.robots.resolver import resolve_recording


class UnsupportedSegmentationError(ValueError):
    """``mission_boundary`` segmentation was requested against a source
    whose Mission window(s) never overlap any frame/robot-state
    timestamp -- see ``BuildEpisodesJobHandler.
    _reject_silent_zero_episode_mission_boundary``."""


@dataclass(frozen=True)
class BuildEpisodesExecution:
    """Resolved inputs and collaborators for one build_episodes handler invocation."""

    job: JobManifest
    params: BuildEpisodesJobParams
    context: WorkerContext
    raw_log_id: str
    episode_artifact_store: EpisodeArtifactStore
    dataset_version_record: DatasetVersionRecord


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

    Uses ``RosbagAdapter.extract_episode_source()`` (one bag read, in-memory
    only) rather than ``build_raw_log()`` — this handler never needed the
    persisted Scene-owned ``RawLogManifest``/``RawLogFrameIndex`` artifacts
    that ``build_raw_log()`` produces, only the sensor frame list inside them
    (see SceneOps V2 Request 12).

    Segmentation strategy is decided by ``EpisodeSegmenter`` from
    ``params.segmentation``, not by this handler or by ``EpisodeBuilder``
    (see SceneOps V2 Request 13).
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

        execution = self._prepare_execution(request, version_record=version_record)

        robot_id, source = await self._extract_episode_source(execution)

        windows = EpisodeSegmenter().segment(source=source, config=params.segmentation)

        build_result = EpisodeBuilder().build(
            dataset_id=version_record.dataset_id,
            dataset_version=version_record.version,
            raw_log_id=execution.raw_log_id,
            robot_id=robot_id,
            robot_run_id=params.robot_run_id,
            source=source,
            windows=windows,
            strategy=params.segmentation.strategy,
        )
        self._reject_silent_zero_episode_mission_boundary(
            params=params, source=source, build_result=build_result
        )

        write_results = await self._write_episode_manifests(execution, build_result)
        episode_manifest_uris = [r.uri for r in write_results]

        await self._register_episode_artifacts(execution, build_result, write_results)

        # DatasetVersion.episode.episode_count is written by
        # RegisterEpisodeJobHandler, not here -- this handler only produces
        # manifests/artifacts, never an EpisodeRecord (see SceneOps V2
        # Request 15's own note above and register_episode.py's docstring).
        # Writing it here from build_result.episode_count would be an
        # operation-local count stamped before the row this DatasetVersion
        # is meant to summarize even exists.

        await context.commit()

        return BuildEpisodesJobResult(
            raw_log_id=execution.raw_log_id,
            episode_ids=[e.episode_id for e in build_result.episodes],
            episode_manifest_uris=episode_manifest_uris,
            episode_count=build_result.episode_count,
            observation_frame_count=build_result.observation_frame_count,
            action_frame_count=build_result.action_frame_count,
            segmentation_strategy=params.segmentation.strategy.value,
            channels=sorted({frame.channel for frame in source.frames}),
        )

    # ── recording read ─────────────────────────────────────────────────────────

    @staticmethod
    async def _extract_episode_source(
        execution: BuildEpisodesExecution,
    ) -> tuple[str, EpisodeSource]:
        """Read the RobotRun's registered recording through the verified
        recording resolver (ADR-007 §12.4) -- never from a caller-supplied
        URI. ``RosbagAdapter`` only ever sees the resolver's verified local
        copy, whatever ArtifactStore backend holds the recording. Returns
        the RobotRun's robot_id with the source: the recording's robot is
        never taken from the caller."""
        params = execution.params
        context = execution.context
        async with resolve_recording(
            robot_run_id=params.robot_run_id,
            robot_store=context.robot_store,
            artifact_record_store=context.artifact_record_store,
            artifact_store=context.artifact_store,
        ) as recording:
            adapter = RosbagAdapter(
                source_store=context.raw_source_store,
                source_root_uri=str(recording.local_path),
            )
            return recording.robot_id, adapter.extract_episode_source(
                robot_id=recording.robot_id, robot_run_id=params.robot_run_id
            )

    @staticmethod
    def _reject_silent_zero_episode_mission_boundary(
        *,
        params: BuildEpisodesJobParams,
        source: EpisodeSource,
        build_result: EpisodeBuildResult,
    ) -> None:
        """``mission_boundary`` segmentation (EpisodeSegmenter) already
        falls back to ``whole_run`` when a source has NO dated Missions at
        all -- that path is fine and unchanged. This guards the OTHER,
        previously-silent failure mode: Mission(s) exist (so segmentation
        produced real Mission-bounded windows), but not one single frame
        or robot-state timestamp fell inside ANY of them, so
        ``EpisodeBuilder`` produced zero Episodes -- a result that used to
        look identical to "this run legitimately has no data", when it
        actually means the Mission window(s) and the source data's
        timestamps live on two incompatible clocks.

        The most common real cause (Phase 6.6 finding) is a Kafka-captured
        MCAP: ``/mission/status`` carries synthetic replay-event time while
        CAN-derived channels carry real historical observation time under
        the frozen MCAP ``log_time`` contract -- the two never overlap.
        ``whole_run`` segmentation needs no such alignment and is the
        supported choice for a streaming-captured RobotRun (see
        docs/workflows/robot-run-and-mcap.md §6). This function does not
        change segmentation behavior at all -- only fails loudly instead
        of returning an empty, misleading success.
        """
        if params.segmentation.strategy != EpisodeSegmentationStrategy.MISSION_BOUNDARY:
            return
        if build_result.episode_count > 0:
            return
        if not source.missions:
            return  # the real "no Missions at all" case -- already whole_run
        raise UnsupportedSegmentationError(
            f"mission_boundary segmentation produced zero Episodes even "
            f"though {len(source.missions)} Mission(s) were present in the "
            f"source -- no frame/robot-state timestamp fell inside any "
            f"Mission's [started_at, ended_at) window. The most common "
            f"cause is a Kafka-captured MCAP, where /mission/status carries "
            f"synthetic replay-event time while CAN-derived channels carry "
            f"real historical observation time -- the two never overlap "
            f"under the current MCAP log_time contract (see "
            f"docs/workflows/robot-run-and-mcap.md §6). Use "
            f"segmentation.strategy=whole_run for a streaming-captured "
            f"RobotRun instead; mission_boundary is only supported where "
            f"every channel shares a compatible recording timeline."
        )

    # ── version resolution ────────────────────────────────────────────

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

    # ── setup ──────────────────────────────────────────────────────────────────

    def _prepare_execution(
        self,
        request: JobHandlerRequest[BuildEpisodesJobParams],
        *,
        version_record: DatasetVersionRecord,
    ) -> BuildEpisodesExecution:
        context = request.context
        params = request.params

        raw_log_id = (
            params.raw_log_id
            or f"{version_record.dataset_id}-{version_record.version}-episodes"
        )

        return BuildEpisodesExecution(
            job=request.job,
            params=params,
            context=context,
            raw_log_id=raw_log_id,
            episode_artifact_store=context.episode_artifact_store,
            dataset_version_record=version_record,
        )

    # ── episode manifest persistence ────────────────────────────────────────────

    @staticmethod
    async def _write_episode_manifests(
        execution: BuildEpisodesExecution,
        build_result: EpisodeBuildResult,
    ) -> list[EpisodeArtifactWriteResult]:
        results: list[EpisodeArtifactWriteResult] = []
        for manifest in build_result.episodes:
            result = await execution.episode_artifact_store.write_episode_manifest(
                dataset_id=execution.dataset_version_record.dataset_id,
                dataset_version=execution.dataset_version_record.version,
                episode_id=manifest.episode_id,
                manifest=manifest,
            )
            results.append(result)
        return results

    async def _register_episode_artifacts(
        self,
        execution: BuildEpisodesExecution,
        build_result: EpisodeBuildResult,
        write_results: list[EpisodeArtifactWriteResult],
    ) -> None:
        # SceneOps V2 Request 2.3 §3: checksum/size_bytes now populated for
        # every EPISODE_MANIFEST ArtifactRecord, computed over the exact
        # bytes physically written (EpisodeArtifactStore._canonical_bytes),
        # not an independently re-serialized object.
        context = execution.context
        version_record = execution.dataset_version_record
        for manifest, write_result in zip(build_result.episodes, write_results):
            await context.artifact_record_store.create(
                artifact_id=generate_artifact_id(),
                ref=ArtifactRef(
                    kind=ArtifactKind.EPISODE_MANIFEST,
                    uri=write_result.uri,
                    media_type="application/json",
                    checksum=write_result.checksum,
                    size_bytes=write_result.size_bytes,
                ),
                owner_type=ArtifactOwnerType.EPISODE,
                owner_id=manifest.episode_id,
                dataset_id=version_record.dataset_id,
                dataset_version=version_record.version,
                job_id=execution.job.job_id,
                pipeline_run_id=execution.job.pipeline_run_id,
            )
