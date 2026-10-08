"""BUILD_RECORDING_EPISODES: the producer task of RECORDING_EPISODE_BUILDING
(ADR-007 §17.4, §29.10, §31).

    robot_run_id
      -> resolve_recording()                  verified local copy (never a caller URI)
      -> check_l1_recording()                 the step-6 L1 conformance suite
      -> plan_recording_episodes()            complete EpisodeManifest set + payload plan
      -> payload bytes, write-once            {payload_root}/{robot_run_id}/{artifact_id}
      -> OBSERVATION_PAYLOAD ArtifactRecords  deterministic ids, shared with Scene
                                              builds of the same RobotRun; reuse or
                                              conflict
      -> manifests, write-once                checksum-qualified keys
      -> EPISODE_MANIFEST ArtifactRecords     deterministic ids; reuse or conflict
      -> commit; result.manifest_artifact_ids -> REGISTER_EPISODES

The builder owns its output bytes and ArtifactRecords; it never writes an
EpisodeRecord or DatasetVersion state (§17.5 rule 2), and it never reads a
Scene.

Failure and retry: every key and artifact id is deterministic, so a retry
after a crash between writing bytes and committing ArtifactRecords
converges: identical bytes are reused, an existing ArtifactRecord with the
same location and integrity is reused, and anything that differs fails the
job. Bytes written by a failed build stay as unreferenced objects (§19).
"""

from __future__ import annotations

import asyncio
import hashlib

from sceneops_core.artifacts.schemas import ArtifactKind
from sceneops_core.artifacts.schemas.owner import ArtifactOwnerType
from sceneops_core.artifacts.schemas.refs import ArtifactRef
from sceneops_core.common.canonical_json import canonical_json_bytes
from sceneops_core.common.schemas import JsonDict
from sceneops_core.episodes.recording_build import EpisodeStreamRole
from sceneops_core.episodes.schemas import episode_id_for
from sceneops_core.jobs.schemas import (
    BuildRecordingEpisodesJobParams,
    BuildRecordingEpisodesJobResult,
    JobType,
)
from sceneops_core.pipelines.schemas import PipelineTaskInputs
from sceneops_recording import check_l1_recording

from sceneops_episodes.recording_builder import (
    EpisodeBuildPlan,
    RecordingEpisodeBuildError,
    iter_planned_payloads,
    plan_recording_episodes,
)
from sceneops_worker.jobs.base import JobHandler, JobHandlerRequest
from sceneops_worker.recordings.artifact_records import (
    ArtifactRecordConflictError,
    require_same_artifact as _require_same,
)
from sceneops_recording.observations.payloads import PlannedPayload, RecordingRevision
from sceneops_worker.robots.resolver import resolve_recording

EPISODE_MANIFEST_ARTIFACT_ID_SCHEMA_V1 = "sceneops.episode_manifest_artifact_id/v1"


def episode_manifest_artifact_id(*, episode_id: str, checksum: str) -> str:
    document = {
        "artifact_id_schema": EPISODE_MANIFEST_ARTIFACT_ID_SCHEMA_V1,
        "episode_id": episode_id,
        "checksum": checksum,
    }
    digest = hashlib.sha256(canonical_json_bytes(document)).hexdigest()
    return f"episode-manifest-{digest[:32]}"


class BuildRecordingEpisodesJobHandler(
    JobHandler[BuildRecordingEpisodesJobParams, BuildRecordingEpisodesJobResult]
):
    @property
    def job_type(self) -> JobType:
        return JobType.BUILD_RECORDING_EPISODES

    @property
    def params_model(self) -> type[BuildRecordingEpisodesJobParams]:
        return BuildRecordingEpisodesJobParams

    def build_job_params(self, inputs: PipelineTaskInputs) -> JsonDict:
        return {
            "dataset_id": inputs.dataset.dataset_id if inputs.dataset else None,
            "dataset_version": inputs.dataset.dataset_version
            if inputs.dataset
            else None,
            **inputs.params,
        }

    async def run(
        self, request: JobHandlerRequest[BuildRecordingEpisodesJobParams]
    ) -> BuildRecordingEpisodesJobResult:
        params = request.params
        context = request.context
        version = await context.dataset_store.get_version(
            dataset_id=params.dataset_id, version=params.dataset_version
        )
        if version is None:
            raise ValueError(
                "DatasetVersion is not registered: "
                f"{params.dataset_id}/{params.dataset_version}"
            )

        async with resolve_recording(
            robot_run_id=params.robot_run_id,
            robot_store=context.robot_store,
            artifact_record_store=context.artifact_record_store,
            artifact_store=context.artifact_store,
        ) as recording:
            report = await asyncio.to_thread(check_l1_recording, recording.local_path)
            if not report.conforms:
                details = [
                    f"{v.requirement}/{v.check}: {v.detail}" for v in report.violations
                ]
                raise RecordingEpisodeBuildError(
                    f"recording of RobotRun {params.robot_run_id} is not L1-conformant: "
                    f"{details[:5]}"
                )
            revision = RecordingRevision(
                robot_run_id=recording.robot_run_id,
                recording_checksum=recording.checksum,
                recording_clock=recording.source_clock,
            )
            plan = await asyncio.to_thread(
                plan_recording_episodes,
                recording.local_path,
                revision=revision,
                config=params.build_config,
            )
            created_payloads = await self._publish_payloads(request, plan, recording)

        manifest_artifact_ids = await self._publish_manifests(request, plan)
        await context.commit()

        return BuildRecordingEpisodesJobResult(
            dataset_id=params.dataset_id,
            dataset_version=params.dataset_version,
            robot_run_id=params.robot_run_id,
            recording_checksum=revision.recording_checksum,
            producer_fingerprint=plan.producer.producer_fingerprint,
            unit_keys=[e.unit_key for e in plan.episodes],
            manifest_artifact_ids=manifest_artifact_ids,
            episode_count=len(plan.episodes),
            observation_count=plan.count(EpisodeStreamRole.OBSERVATION),
            state_count=plan.count(EpisodeStreamRole.STATE),
            action_count=plan.count(EpisodeStreamRole.ACTION),
            event_count=plan.count(EpisodeStreamRole.EVENT),
            payload_artifact_count=len(plan.payloads),
            created_payload_count=created_payloads,
            topics=sorted(
                [s.topic for s in params.build_config.streams]
                + [e.topic for e in params.build_config.events]
            ),
        )

    async def _publish_payloads(
        self,
        request: JobHandlerRequest[BuildRecordingEpisodesJobParams],
        plan: EpisodeBuildPlan,
        recording,
    ) -> int:
        context = request.context
        store = context.episode_artifact_store.payload_store
        existing = await context.artifact_record_store.get_many(
            sorted(p.artifact_id for p in plan.payloads.values())
        )
        created = 0
        for planned, data in iter_planned_payloads(
            recording.local_path, plan, request.params.build_config
        ):
            uri, _ = await store.publish(
                robot_run_id=request.params.robot_run_id,
                artifact_id=planned.artifact_id,
                data=data,
                checksum=planned.checksum,
            )
            ref = _payload_ref(planned, uri)
            record = existing.get(planned.artifact_id)
            if record is None:
                # Builds of one RobotRun run concurrently (its Scene and Episode
                # builds, two runs of one scope): a payload another build registered
                # first is reused, and compared, below.
                (
                    record,
                    registered,
                ) = await context.artifact_record_store.create_if_absent(
                    artifact_id=planned.artifact_id,
                    ref=ref,
                    owner_type=ArtifactOwnerType.ROBOT_RUN,
                    owner_id=request.params.robot_run_id,
                    job_id=request.job.job_id,
                    pipeline_run_id=request.job.pipeline_run_id,
                )
                if registered:
                    created += 1
                    continue
            _require_same(record, ref)
        return created

    async def _publish_manifests(
        self,
        request: JobHandlerRequest[BuildRecordingEpisodesJobParams],
        plan: EpisodeBuildPlan,
    ) -> list[str]:
        params = request.params
        context = request.context
        artifact_ids: list[str] = []
        for episode in plan.episodes:
            episode_id = episode_id_for(
                dataset_id=params.dataset_id,
                dataset_version=params.dataset_version,
                source=episode.manifest.lineage.source,
            )
            published = await context.episode_artifact_store.publish_canonical_manifest(
                dataset_id=params.dataset_id,
                dataset_version=params.dataset_version,
                episode_id=episode_id,
                manifest=episode.manifest,
            )
            artifact_id = episode_manifest_artifact_id(
                episode_id=episode_id, checksum=published.checksum
            )
            ref = ArtifactRef(
                kind=ArtifactKind.EPISODE_MANIFEST,
                uri=published.uri,
                media_type="application/json",
                checksum=published.checksum,
                size_bytes=published.size_bytes,
            )
            record, registered = await context.artifact_record_store.create_if_absent(
                artifact_id=artifact_id,
                ref=ref,
                owner_type=ArtifactOwnerType.EPISODE,
                owner_id=episode_id,
                dataset_id=params.dataset_id,
                dataset_version=params.dataset_version,
                job_id=request.job.job_id,
                pipeline_run_id=request.job.pipeline_run_id,
            )
            if not registered:
                _require_same(record, ref)
            artifact_ids.append(artifact_id)
        return artifact_ids


def _payload_ref(planned: PlannedPayload, uri: str) -> ArtifactRef:
    return ArtifactRef(
        kind=ArtifactKind.OBSERVATION_PAYLOAD,
        uri=uri,
        media_type=planned.media_type,
        checksum=planned.checksum,
        size_bytes=planned.size_bytes,
    )


__all__ = [
    "ArtifactRecordConflictError",
    "BuildRecordingEpisodesJobHandler",
    "episode_manifest_artifact_id",
]
