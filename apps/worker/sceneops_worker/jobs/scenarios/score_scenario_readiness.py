"""score_scenario_readiness — scores scenario candidates from a prior mine_scenarios run.

Scoring components (total = 1.0):
  gt           0.30  — has GT and annotation_count > 0
  validation   0.25  — ready:+0.25  warning:+0.15  blocked/unknown:+0
  channels     0.20  — all required_channels present:+0.20  partial:+0.10
  density      0.15  — normalised annotation_count (max across candidates)
  completeness 0.10  — sample_count > 0 AND frame_count > 0

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
from sceneops_core.common.ids import generate_artifact_id, default_readiness_run_id
from sceneops_core.common.time import utc_now
from sceneops_core.jobs.schemas import (
    JobType,
    ScoreScenarioReadinessJobParams,
    ScoreScenarioReadinessJobResult,
)
from sceneops_core.pipelines.schemas import PipelineTaskInputs
from sceneops_core.runs.schemas import RunStatus
from sceneops_core.scenarios.schemas.runs import ScenarioReadinessRunRecord
from sceneops_worker.core.context import WorkerContext
from sceneops_worker.jobs.base import JobHandler, RunRecordHandler


# ── scoring ───────────────────────────────────────────────────────────────────

_READY_THRESHOLD = 0.75
_WARNING_THRESHOLD = 0.40


def _score_candidate(
    candidate: dict[str, Any],
    *,
    required_channels: list[str],
    max_annotation_count: int,
) -> tuple[float, dict[str, float], list[str]]:
    """Returns (total_score, components, reasons)."""
    components: dict[str, float] = {}
    reasons: list[str] = []

    # GT component
    gt_score = 0.0
    if candidate.get("has_ground_truth") and candidate.get("annotation_count", 0) > 0:
        gt_score = 0.30
        reasons.append("has_ground_truth")
    components["gt"] = gt_score

    # Validation component
    val_status = candidate.get("validation_status", "unknown")
    if val_status == "ready":
        val_score = 0.25
        reasons.append("validation_ready")
    elif val_status == "warning":
        val_score = 0.15
        reasons.append("validation_warning")
    else:
        val_score = 0.0
    components["validation"] = val_score

    # Channel component
    channel_score = 0.0
    if required_channels:
        scene_channels = set(candidate.get("channels") or [])
        present = [ch for ch in required_channels if ch in scene_channels]
        if len(present) == len(required_channels):
            channel_score = 0.20
            reasons.append("required_channels_present")
        elif present:
            channel_score = 0.10
            reasons.append("partial_channels_present")
    else:
        # No required channels specified — award full channel component
        channel_score = 0.20
        reasons.append("no_channel_requirements")
    components["channels"] = channel_score

    # Density component (normalised by max annotation count across all candidates)
    density_score = 0.0
    if max_annotation_count > 0:
        density_score = min(
            0.15,
            0.15 * candidate.get("annotation_count", 0) / max_annotation_count,
        )
        if candidate.get("annotation_count", 0) > 0:
            reasons.append("dense_annotations")
    components["density"] = round(density_score, 4)

    # Completeness component
    completeness_score = 0.0
    if candidate.get("sample_count", 0) > 0 and candidate.get("frame_count", 0) > 0:
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
            "dataset_id": inputs.dataset.dataset_id if inputs.dataset else None,
            "dataset_version": inputs.dataset.dataset_version
            if inputs.dataset
            else None,
            **inputs.params,
            # Propagated from mine_scenarios via pipeline refs
            "scenario_set_id": inputs.refs.get("scenario_set_id"),
            "scenario_set_uri": inputs.refs.get("scenario_set_uri"),
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
            dataset_id=params.dataset_id,
            dataset_version=params.dataset_version,
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

        scenario_set_uri = params.scenario_set_uri
        if not scenario_set_uri:
            raise ValueError(
                "score_scenario_readiness requires scenario_set_uri "
                "(passed via params or propagated from mine_scenarios)"
            )

        # ── load candidates ───────────────────────────────────────────────────
        candidates_payload = await context.artifact_store.read_json(scenario_set_uri)
        candidates: list[dict[str, Any]] = candidates_payload.get("candidates", [])
        scenario_set_id = params.scenario_set_id or candidates_payload.get(
            "scenario_set_id"
        )
        dataset_id = params.dataset_id or candidates_payload.get("dataset_id")
        dataset_version = params.dataset_version or candidates_payload.get(
            "dataset_version"
        )

        # ── score ─────────────────────────────────────────────────────────────
        max_annotation_count = max(
            (c.get("annotation_count", 0) for c in candidates), default=0
        )
        required_channels = (
            params.required_channels
            or candidates_payload.get("filters", {}).get("required_channels")
            or []
        )

        scored_scenes: list[dict[str, Any]] = []
        ready_count = 0
        warning_count = 0
        blocked_count = 0
        score_total = 0.0

        for candidate in candidates:
            score, components, reasons = _score_candidate(
                candidate,
                required_channels=required_channels,
                max_annotation_count=max_annotation_count,
            )
            bucket = _readiness_bucket(score)

            if bucket == "ready":
                ready_count += 1
            elif bucket == "warning":
                warning_count += 1
            else:
                blocked_count += 1

            score_total += score
            scored_scenes.append(
                {
                    "scene_id": candidate["scene_id"],
                    "readiness_score": score,
                    "readiness_bucket": bucket,
                    "components": components,
                    "reasons": reasons,
                }
            )

        scored_scene_count = len(scored_scenes)
        average_score = (
            round(score_total / scored_scene_count, 4)
            if scored_scene_count > 0
            else None
        )

        # top scenes: up to 5 ready scenes sorted by score desc
        top_scene_ids = [
            s["scene_id"]
            for s in sorted(
                [s for s in scored_scenes if s["readiness_bucket"] == "ready"],
                key=lambda s: s["readiness_score"],
                reverse=True,
            )[:5]
        ]

        # ── write readiness report ────────────────────────────────────────────
        readiness_report_uri = context.artifact_store.join_uri(
            context.settings.run_root_uri,
            "scenario_readiness",
            run_id,
            "report.json",
        )
        report_payload: dict[str, Any] = {
            "scenario_set_id": scenario_set_id,
            "dataset_id": dataset_id,
            "dataset_version": dataset_version,
            "score_profile": params.score_profile,
            "created_at": utc_now().isoformat(),
            "pipeline_run_id": job.pipeline_run_id,
            "job_id": job.job_id,
            "scored_scene_count": scored_scene_count,
            "summary": {
                "ready_count": ready_count,
                "warning_count": warning_count,
                "blocked_count": blocked_count,
                "average_score": average_score,
                "top_scene_ids": top_scene_ids,
            },
            "scenes": scored_scenes,
        }
        await context.artifact_store.write_json(readiness_report_uri, report_payload)

        # ── register artifact ─────────────────────────────────────────────────
        await context.artifact_record_store.create(
            artifact_id=generate_artifact_id(),
            ref=ArtifactRef(
                kind=ArtifactKind.SCENARIO_READINESS_REPORT,
                uri=readiness_report_uri,
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

        # ── update + persist run record ───────────────────────────────────────
        summary = {
            "score_profile": params.score_profile,
            "buckets": {
                "ready_count": ready_count,
                "warning_count": warning_count,
                "blocked_count": blocked_count,
            },
            "top_scene_ids": top_scene_ids,
        }

        succeeded_record = initial_record.model_copy(
            update={
                "status": RunStatus.SUCCEEDED,
                "scenario_set_id": scenario_set_id,
                "scenario_set_uri": scenario_set_uri,
                "dataset_id": dataset_id,
                "dataset_version": dataset_version,
                "readiness_report_uri": readiness_report_uri,
                "scenario_count": scored_scene_count,
                "ready_count": ready_count,
                "warning_count": warning_count,
                "blocked_count": blocked_count,
                "average_score": average_score,
                "summary": summary,
                "finished_at": utc_now(),
            }
        )

        return succeeded_record, ScoreScenarioReadinessJobResult(
            scenario_set_id=scenario_set_id,
            readiness_report_uri=readiness_report_uri,
            readiness_run_id=run_id,
            scored_scene_count=scored_scene_count,
            average_score=average_score,
            ready_count=ready_count,
            warning_count=warning_count,
            blocked_count=blocked_count,
            top_scene_ids=top_scene_ids,
            summary=summary,
        )

    async def _upsert(
        self, context: WorkerContext, record: ScenarioReadinessRunRecord
    ) -> ScenarioReadinessRunRecord:
        result = await context.scenario_store.upsert_run(record)
        assert isinstance(result, ScenarioReadinessRunRecord)
        return result
