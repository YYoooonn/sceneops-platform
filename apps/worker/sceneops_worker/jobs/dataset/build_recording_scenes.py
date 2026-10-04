"""BUILD_RECORDING_SCENES: the producer task of RECORDING_SCENE_BUILDING
(ADR-007 §17.3, §29.10).

    robot_run_id
      -> resolve_recording()                  verified local copy (never a caller URI)
      -> check_l1_recording()                 the step-6 L1 conformance suite
      -> plan_recording_scenes()              complete SceneManifest set + payload plan
      -> payload bytes, write-once            {payload_root}/{robot_run_id}/{artifact_id}
      -> OBSERVATION_PAYLOAD ArtifactRecords  deterministic ids; reuse or conflict
      -> manifests, write-once                checksum-qualified keys
      -> SCENE_MANIFEST ArtifactRecords       deterministic ids; reuse or conflict
      -> commit; result.manifest_artifact_ids -> REGISTER_SCENES

The builder owns its output bytes and ArtifactRecords; it never writes a
SceneRecord or DatasetVersion state (§17.5 rule 2).

Failure and retry: every key and artifact id is deterministic, so a retry
after a crash between writing bytes and committing ArtifactRecords
converges: identical bytes are reused, an existing ArtifactRecord with the
same location and integrity is reused, and anything that differs fails the
job. Bytes written by a failed build stay as unreferenced objects (no
garbage collection, §19).
"""

from __future__ import annotations

import asyncio
import hashlib

from sceneops_core.artifacts.schemas import ArtifactKind, ArtifactRecord
from sceneops_core.artifacts.schemas.owner import ArtifactOwnerType
from sceneops_core.artifacts.schemas.refs import ArtifactRef
from sceneops_core.common.canonical_json import canonical_json_bytes
from sceneops_core.common.schemas import JsonDict
from sceneops_core.jobs.schemas import (
    BuildRecordingScenesJobParams,
    BuildRecordingScenesJobResult,
    JobType,
)
from sceneops_core.pipelines.schemas import PipelineTaskInputs
from sceneops_core.scenes.schemas import scene_id_for
from sceneops_integrations.recording import check_l1_recording

from sceneops_worker.core.context import WorkerContext
from sceneops_worker.jobs.base import JobHandler, JobHandlerRequest
from sceneops_worker.robots.resolver import resolve_recording
from sceneops_worker.scenes.recording_builder import (
    PlannedPayload,
    RecordingRevision,
    RecordingSceneBuildError,
    SceneBuildPlan,
    iter_planned_payloads,
    plan_recording_scenes,
)

SCENE_MANIFEST_ARTIFACT_ID_SCHEMA_V1 = "sceneops.scene_manifest_artifact_id/v1"


class ArtifactRecordConflictError(RuntimeError):
    """A deterministic artifact id is registered with different content."""


def scene_manifest_artifact_id(*, scene_id: str, checksum: str) -> str:
    document = {
        "artifact_id_schema": SCENE_MANIFEST_ARTIFACT_ID_SCHEMA_V1,
        "scene_id": scene_id,
        "checksum": checksum,
    }
    digest = hashlib.sha256(canonical_json_bytes(document)).hexdigest()
    return f"scene-manifest-{digest[:32]}"


def _require_same(existing: ArtifactRecord, ref: ArtifactRef) -> None:
    mismatched = [
        name
        for name in ("kind", "uri", "checksum", "size_bytes", "media_type")
        if getattr(existing, name) != getattr(ref, name)
    ]
    if mismatched:
        raise ArtifactRecordConflictError(
            f"ArtifactRecord {existing.artifact_id} already exists with different "
            f"{mismatched}; deterministic artifact ids are write-once"
        )


class BuildRecordingScenesJobHandler(
    JobHandler[BuildRecordingScenesJobParams, BuildRecordingScenesJobResult]
):
    @property
    def job_type(self) -> JobType:
        return JobType.BUILD_RECORDING_SCENES

    @property
    def params_model(self) -> type[BuildRecordingScenesJobParams]:
        return BuildRecordingScenesJobParams

    def build_job_params(self, inputs: PipelineTaskInputs) -> JsonDict:
        return {
            "dataset_id": inputs.dataset.dataset_id if inputs.dataset else None,
            "dataset_version": inputs.dataset.dataset_version
            if inputs.dataset
            else None,
            **inputs.params,
        }

    async def run(
        self, request: JobHandlerRequest[BuildRecordingScenesJobParams]
    ) -> BuildRecordingScenesJobResult:
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
                raise RecordingSceneBuildError(
                    f"recording of RobotRun {params.robot_run_id} is not L1-conformant: "
                    f"{details[:5]}"
                )
            revision = RecordingRevision(
                robot_run_id=recording.robot_run_id,
                recording_checksum=recording.checksum,
                recording_clock=recording.source_clock,
            )
            plan = await asyncio.to_thread(
                plan_recording_scenes,
                recording.local_path,
                revision=revision,
                config=params.build_config,
            )
            created_payloads = await self._publish_payloads(request, plan, recording)

        manifest_artifact_ids = await self._publish_manifests(request, plan)
        await context.commit()

        return BuildRecordingScenesJobResult(
            dataset_id=params.dataset_id,
            dataset_version=params.dataset_version,
            robot_run_id=params.robot_run_id,
            recording_checksum=revision.recording_checksum,
            producer_fingerprint=plan.producer.producer_fingerprint,
            unit_keys=[s.unit_key for s in plan.scenes],
            manifest_artifact_ids=manifest_artifact_ids,
            scene_count=len(plan.scenes),
            observation_count=plan.observation_count,
            pose_count=plan.pose_count,
            payload_artifact_count=len(plan.payloads),
            created_payload_count=created_payloads,
            channels=sorted(c.topic for c in params.build_config.channels),
        )

    # ── payloads ───────────────────────────────────────────────────────────────

    async def _publish_payloads(
        self,
        request: JobHandlerRequest[BuildRecordingScenesJobParams],
        plan: SceneBuildPlan,
        recording,
    ) -> int:
        context = request.context
        store = context.scene_artifact_store
        existing = await context.artifact_record_store.get_many(
            sorted(p.artifact_id for p in plan.payloads.values())
        )
        created = 0
        for planned, data in iter_planned_payloads(
            recording.local_path, plan, request.params.build_config
        ):
            uri, _ = await store.publish_observation_payload(
                robot_run_id=request.params.robot_run_id,
                artifact_id=planned.artifact_id,
                data=data,
                checksum=planned.checksum,
            )
            ref = self._payload_ref(planned, uri)
            record = existing.get(planned.artifact_id)
            if record is not None:
                _require_same(record, ref)
                continue
            await context.artifact_record_store.create(
                artifact_id=planned.artifact_id,
                ref=ref,
                owner_type=ArtifactOwnerType.ROBOT_RUN,
                owner_id=request.params.robot_run_id,
                job_id=request.job.job_id,
                pipeline_run_id=request.job.pipeline_run_id,
            )
            created += 1
        return created

    @staticmethod
    def _payload_ref(planned: PlannedPayload, uri: str) -> ArtifactRef:
        return ArtifactRef(
            kind=ArtifactKind.OBSERVATION_PAYLOAD,
            uri=uri,
            media_type=planned.media_type,
            checksum=planned.checksum,
            size_bytes=planned.size_bytes,
        )

    # ── manifests ──────────────────────────────────────────────────────────────

    async def _publish_manifests(
        self,
        request: JobHandlerRequest[BuildRecordingScenesJobParams],
        plan: SceneBuildPlan,
    ) -> list[str]:
        params = request.params
        context: WorkerContext = request.context
        artifact_ids: list[str] = []
        for scene in plan.scenes:
            scene_id = scene_id_for(
                dataset_id=params.dataset_id,
                dataset_version=params.dataset_version,
                source=scene.manifest.lineage.source,
            )
            published = await context.scene_artifact_store.publish_canonical_manifest(
                dataset_id=params.dataset_id,
                dataset_version=params.dataset_version,
                scene_id=scene_id,
                manifest=scene.manifest,
            )
            artifact_id = scene_manifest_artifact_id(
                scene_id=scene_id, checksum=published.checksum
            )
            ref = ArtifactRef(
                kind=ArtifactKind.SCENE_MANIFEST,
                uri=published.uri,
                media_type="application/json",
                checksum=published.checksum,
                size_bytes=published.size_bytes,
            )
            existing = await context.artifact_record_store.get(artifact_id)
            if existing is not None:
                _require_same(existing, ref)
            else:
                await context.artifact_record_store.create(
                    artifact_id=artifact_id,
                    ref=ref,
                    owner_type=ArtifactOwnerType.SCENE,
                    owner_id=scene_id,
                    dataset_id=params.dataset_id,
                    dataset_version=params.dataset_version,
                    scene_id=scene_id,
                    job_id=request.job.job_id,
                    pipeline_run_id=request.job.pipeline_run_id,
                )
            artifact_ids.append(artifact_id)
        return artifact_ids


__all__ = [
    "ArtifactRecordConflictError",
    "BuildRecordingScenesJobHandler",
    "scene_manifest_artifact_id",
]
