"""score_scenario_readiness: scores the members of one pinned ScenarioSet.

Scoring components (total = 1.0):
  labels       0.30  - the member carries at least one label
  validation   0.25  - ready:+0.25  warning:+0.15  blocked/unknown:+0
  channels     0.20  - all required_channels present:+0.20  partial:+0.10
  density      0.15  - label count normalised by the set's maximum
  completeness 0.10  - at least one selected sample on at least one channel

Readiness buckets:
  ready    score >= 0.75
  warning  0.40 <= score < 0.75
  blocked  score < 0.40
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sceneops_core.artifacts.schemas.enums import ArtifactKind
from sceneops_core.artifacts.schemas.owner import ArtifactOwnerType
from sceneops_core.artifacts.schemas.refs import ArtifactRef
from sceneops_core.common.ids import default_readiness_run_id, generate_artifact_id
from sceneops_core.common.time import utc_now
from sceneops_core.jobs.schemas import (
    JobType,
    ScoreScenarioReadinessJobParams,
    ScoreScenarioReadinessJobResult,
)
from sceneops_core.pipelines.schemas import PipelineTaskInputs
from sceneops_core.runs.schemas import RunStatus
from sceneops_core.scenarios import ScenarioMember
from sceneops_core.scenarios.schemas.runs import ScenarioReadinessRunRecord
from sceneops_worker.core.context import WorkerContext
from sceneops_worker.derived.resolution import resolve_scenario_set
from sceneops_worker.jobs.base import JobHandler, RunRecordHandler

# ── scoring ───────────────────────────────────────────────────────────────────

_READY_THRESHOLD = 0.75
_WARNING_THRESHOLD = 0.40


def _score_member(
    member: ScenarioMember,
    *,
    required_channels: list[str],
    max_label_count: int,
) -> tuple[float, dict[str, float], list[str]]:
    """Returns (total_score, components, reasons)."""
    components: dict[str, float] = {}
    reasons: list[str] = []

    labels_score = 0.0
    if member.label_count > 0:
        labels_score = 0.30
        reasons.append("has_labels")
    components["labels"] = labels_score

    if member.readiness == "ready":
        val_score = 0.25
        reasons.append("validation_ready")
    elif member.readiness == "warning":
        val_score = 0.15
        reasons.append("validation_warning")
    else:
        val_score = 0.0
    components["validation"] = val_score

    channel_score = 0.0
    if required_channels:
        present = [ch for ch in required_channels if ch in member.channels]
        if len(present) == len(required_channels):
            channel_score = 0.20
            reasons.append("required_channels_present")
        elif present:
            channel_score = 0.10
            reasons.append("partial_channels_present")
    else:
        channel_score = 0.20
        reasons.append("no_channel_requirements")
    components["channels"] = channel_score

    density_score = 0.0
    if max_label_count > 0:
        density_score = min(0.15, 0.15 * member.label_count / max_label_count)
        if member.label_count > 0:
            reasons.append("dense_labels")
    components["density"] = round(density_score, 4)

    completeness_score = 0.0
    if member.sample_ids and member.channels:
        completeness_score = 0.10
        reasons.append("complete_sequence")
    components["completeness"] = completeness_score

    total = sum(components.values())
    return round(total, 4), components, reasons


def _readiness_bucket(score: float) -> str:
    if score >= _READY_THRESHOLD:
        return "ready"
    if score >= _WARNING_THRESHOLD:
        return "warning"
    return "blocked"


# ── handler ───────────────────────────────────────────────────────────────────


class ScoreScenarioReadinessJobHandler(
    RunRecordHandler[
        ScoreScenarioReadinessJobParams,
        ScoreScenarioReadinessJobResult,
        ScenarioReadinessRunRecord,
    ],
    JobHandler[ScoreScenarioReadinessJobParams, ScoreScenarioReadinessJobResult],
):
    @property
    def job_type(self) -> JobType:
        return JobType.SCORE_SCENARIO_READINESS

    @property
    def params_model(self) -> type[ScoreScenarioReadinessJobParams]:
        return ScoreScenarioReadinessJobParams

    def build_job_params(self, inputs: PipelineTaskInputs) -> dict:
        return {
            **inputs.params,
            # Propagated from mine_scenarios via pipeline refs
            "scenario_set_id": inputs.refs.get("scenario_set_id"),
        }

    def build_initial_record(
        self,
        *,
        job: Any,
        params: ScoreScenarioReadinessJobParams,
        started_at: datetime,
    ) -> ScenarioReadinessRunRecord:
        return ScenarioReadinessRunRecord(
            run_id=default_readiness_run_id(job.job_id),
            scenario_set_id=params.scenario_set_id,
            status=RunStatus.RUNNING,
            pipeline_run_id=job.pipeline_run_id,
            pipeline_task_run_id=job.pipeline_task_run_id,
            job_id=job.job_id,
            started_at=started_at,
        )

    async def execute(
        self,
        *,
        job: Any,
        params: ScoreScenarioReadinessJobParams,
        context: WorkerContext,
        initial_record: ScenarioReadinessRunRecord,
        started_at: datetime,
    ) -> tuple[ScenarioReadinessRunRecord, ScoreScenarioReadinessJobResult]:
        run_id = initial_record.run_id
        if not params.scenario_set_id:
            raise ValueError(
                "score_scenario_readiness requires scenario_set_id "
                "(passed via params or propagated from mine_scenarios)"
            )
        resolved = await resolve_scenario_set(context, params.scenario_set_id)
        manifest = resolved.manifest
        scenario_set_id = manifest.scenario_set_id
        dataset_id = manifest.dataset_id
        dataset_version = manifest.dataset_version

        max_label_count = max((m.label_count for m in manifest.members), default=0)
        required_channels = params.required_channels or list(
            manifest.curation.required_channels
        )

        scored: list[dict[str, Any]] = []
        counts = {"ready": 0, "warning": 0, "blocked": 0}
        score_total = 0.0
        for member in manifest.members:
            score, components, reasons = _score_member(
                member,
                required_channels=required_channels,
                max_label_count=max_label_count,
            )
            bucket = _readiness_bucket(score)
            counts[bucket] += 1
            score_total += score
            scored.append(
                {
                    "scene_id": member.scene_id,
                    "readiness_score": score,
                    "readiness_bucket": bucket,
                    "components": components,
                    "reasons": reasons,
                }
            )

        scored_scene_count = len(scored)
        average_score = (
            round(score_total / scored_scene_count, 4) if scored_scene_count else None
        )
        top_scene_ids = [
            s["scene_id"]
            for s in sorted(
                (s for s in scored if s["readiness_bucket"] == "ready"),
                key=lambda s: s["readiness_score"],
                reverse=True,
            )[:5]
        ]

        report_uri = context.artifact_store.join_uri(
            context.settings.run_root_uri, "scenario_readiness", run_id, "report.json"
        )
        await context.artifact_store.write_json(
            report_uri,
            {
                "scenario_set": resolved.ref.model_dump(mode="json"),
                "dataset_id": dataset_id,
                "dataset_version": dataset_version,
                "score_profile": params.score_profile,
                "created_at": utc_now().isoformat(),
                "pipeline_run_id": job.pipeline_run_id,
                "job_id": job.job_id,
                "scored_scene_count": scored_scene_count,
                "summary": {
                    "ready_count": counts["ready"],
                    "warning_count": counts["warning"],
                    "blocked_count": counts["blocked"],
                    "average_score": average_score,
                    "top_scene_ids": top_scene_ids,
                },
                "scenes": scored,
            },
        )
        await context.artifact_record_store.create(
            artifact_id=generate_artifact_id(),
            ref=ArtifactRef(
                kind=ArtifactKind.SCENARIO_READINESS_REPORT,
                uri=report_uri,
                media_type="application/json",
            ),
            owner_type=ArtifactOwnerType.SCENARIO_READINESS_RUN,
            owner_id=run_id,
            scenario_set_id=scenario_set_id,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            run_id=run_id,
            job_id=job.job_id,
            pipeline_run_id=job.pipeline_run_id,
        )

        summary = {
            "score_profile": params.score_profile,
            "scenario_set": resolved.ref.model_dump(mode="json"),
            "buckets": {
                "ready_count": counts["ready"],
                "warning_count": counts["warning"],
                "blocked_count": counts["blocked"],
            },
            "top_scene_ids": top_scene_ids,
        }
        succeeded_record = initial_record.model_copy(
            update={
                "status": RunStatus.SUCCEEDED,
                "scenario_set_id": scenario_set_id,
                "scenario_set_uri": resolved.record.scenario_set_uri,
                "dataset_id": dataset_id,
                "dataset_version": dataset_version,
                "readiness_report_uri": report_uri,
                "scenario_count": scored_scene_count,
                "ready_count": counts["ready"],
                "warning_count": counts["warning"],
                "blocked_count": counts["blocked"],
                "average_score": average_score,
                "summary": summary,
                "finished_at": utc_now(),
            }
        )
        return succeeded_record, ScoreScenarioReadinessJobResult(
            scenario_set_id=scenario_set_id,
            readiness_report_uri=report_uri,
            readiness_run_id=run_id,
            scored_scene_count=scored_scene_count,
            average_score=average_score,
            ready_count=counts["ready"],
            warning_count=counts["warning"],
            blocked_count=counts["blocked"],
            top_scene_ids=top_scene_ids,
            summary=summary,
        )

    async def _upsert(
        self, context: WorkerContext, record: ScenarioReadinessRunRecord
    ) -> ScenarioReadinessRunRecord:
        result = await context.scenario_store.upsert_run(record)
        assert isinstance(result, ScenarioReadinessRunRecord)
        return result
