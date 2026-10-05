from __future__ import annotations

from sceneops_core.jobs.schemas import JobType
from sceneops_core.pipelines.registry import PipelineDefinitionRegistry
from sceneops_core.pipelines.schemas import (
    PipelineDefinition,
    PipelineTaskDefinition,
    PipelineTaskOutputKind,
    PipelineTaskOutputSpec,
    PipelineTaskQualityRule,
    PipelineTaskQualityRuleType,
    PipelineType,
)

# ── Shared output / quality-rule declarations ──────────────────────────────────
# Reused across pipeline definitions that share the same task type.

_REF = PipelineTaskOutputKind.REF
_SUMMARY = PipelineTaskOutputKind.SUMMARY
_METRIC = PipelineTaskOutputKind.METRIC
_ARTIFACT = PipelineTaskOutputKind.ARTIFACT

_BUILD_RECORDING_SCENES_OUTPUTS = [
    # The complete Scene set of the recording scope, consumed by
    # register_scenes → REF.
    PipelineTaskOutputSpec(
        name="manifest_artifact_ids", kind=_REF, source="manifest_artifact_ids"
    ),
    PipelineTaskOutputSpec(name="robot_run_id", kind=_REF, source="robot_run_id"),
    PipelineTaskOutputSpec(
        name="producer_fingerprint", kind=_SUMMARY, source="producer_fingerprint"
    ),
    PipelineTaskOutputSpec(name="scene_count", kind=_SUMMARY, source="scene_count"),
    PipelineTaskOutputSpec(
        name="observation_count", kind=_SUMMARY, source="observation_count"
    ),
    PipelineTaskOutputSpec(name="pose_count", kind=_SUMMARY, source="pose_count"),
    PipelineTaskOutputSpec(
        name="payload_artifact_count", kind=_SUMMARY, source="payload_artifact_count"
    ),
    PipelineTaskOutputSpec(
        name="created_payload_count", kind=_SUMMARY, source="created_payload_count"
    ),
    PipelineTaskOutputSpec(name="channels", kind=_SUMMARY, source="channels"),
]

_REGISTER_SCENES_OUTPUTS = [
    # scene_ids consumed by validate_scene / profile_scene → REF.
    PipelineTaskOutputSpec(name="scene_ids", kind=_REF, source="scene_ids"),
    PipelineTaskOutputSpec(
        name="registered_manifest_artifact_ids",
        kind=_SUMMARY,
        source="manifest_artifact_ids",
    ),
    PipelineTaskOutputSpec(
        name="created_scene_ids", kind=_SUMMARY, source="created_scene_ids"
    ),
    PipelineTaskOutputSpec(
        name="replaced_scene_ids", kind=_SUMMARY, source="replaced_scene_ids"
    ),
    PipelineTaskOutputSpec(
        name="unchanged_scene_ids", kind=_SUMMARY, source="unchanged_scene_ids"
    ),
    PipelineTaskOutputSpec(
        name="removed_scene_ids", kind=_SUMMARY, source="removed_scene_ids"
    ),
    PipelineTaskOutputSpec(
        name="registered_scene_count", kind=_SUMMARY, source="registered_scene_count"
    ),
]

_VALIDATE_SCENE_OUTPUTS = [
    PipelineTaskOutputSpec(
        name="validation_run_id", kind=_REF, source="validation_run_id"
    ),
    PipelineTaskOutputSpec(
        name="validation_report_uri",
        kind=_ARTIFACT,
        source="report_uri",
        target="validation_report_uri",
    ),
    PipelineTaskOutputSpec(
        name="validation_status",
        kind=_SUMMARY,
        source="status",
        target="validation_status",
    ),
    PipelineTaskOutputSpec(
        name="should_block_pipeline", kind=_SUMMARY, source="should_block_pipeline"
    ),
    PipelineTaskOutputSpec(
        name="checked_scene_count", kind=_SUMMARY, source="checked_scene_count"
    ),
    PipelineTaskOutputSpec(name="issue_count", kind=_SUMMARY, source="issue_count"),
]

_VALIDATE_SCENE_QUALITY_RULES = [
    PipelineTaskQualityRule(
        rule_type=PipelineTaskQualityRuleType.BLOCK_IF_TRUE,
        source="summary.should_block_pipeline",
        message="Scene validation blocked pipeline",
        code="validate_scene_blocked",
    ),
]

_PROFILE_SCENE_OUTPUTS = [
    PipelineTaskOutputSpec(name="profile_run_id", kind=_REF, source="profile_run_id"),
    PipelineTaskOutputSpec(
        name="profile_report_uri",
        kind=_ARTIFACT,
        source="report_uri",
        target="profile_report_uri",
    ),
    PipelineTaskOutputSpec(name="scene_count", kind=_SUMMARY, source="scene_count"),
    PipelineTaskOutputSpec(
        name="observation_count", kind=_SUMMARY, source="observation_count"
    ),
    PipelineTaskOutputSpec(
        name="observed_channels", kind=_SUMMARY, source="observed_channels"
    ),
]

_BUILD_RECORDING_EPISODES_OUTPUTS = [
    # The complete Episode set of the recording scope, consumed by
    # register_episodes → REF.
    PipelineTaskOutputSpec(
        name="manifest_artifact_ids", kind=_REF, source="manifest_artifact_ids"
    ),
    PipelineTaskOutputSpec(name="robot_run_id", kind=_REF, source="robot_run_id"),
    PipelineTaskOutputSpec(
        name="producer_fingerprint", kind=_SUMMARY, source="producer_fingerprint"
    ),
    PipelineTaskOutputSpec(name="episode_count", kind=_SUMMARY, source="episode_count"),
    PipelineTaskOutputSpec(
        name="observation_count", kind=_SUMMARY, source="observation_count"
    ),
    PipelineTaskOutputSpec(name="state_count", kind=_SUMMARY, source="state_count"),
    PipelineTaskOutputSpec(name="action_count", kind=_SUMMARY, source="action_count"),
    PipelineTaskOutputSpec(name="event_count", kind=_SUMMARY, source="event_count"),
    PipelineTaskOutputSpec(
        name="payload_artifact_count", kind=_SUMMARY, source="payload_artifact_count"
    ),
    PipelineTaskOutputSpec(
        name="created_payload_count", kind=_SUMMARY, source="created_payload_count"
    ),
    PipelineTaskOutputSpec(name="unit_keys", kind=_SUMMARY, source="unit_keys"),
    PipelineTaskOutputSpec(name="topics", kind=_SUMMARY, source="topics"),
]

_REGISTER_EPISODES_OUTPUTS = [
    # episode_ids consumed by validate_episode / profile_episode → REF.
    PipelineTaskOutputSpec(name="episode_ids", kind=_REF, source="episode_ids"),
    PipelineTaskOutputSpec(
        name="registered_manifest_artifact_ids",
        kind=_SUMMARY,
        source="manifest_artifact_ids",
    ),
    PipelineTaskOutputSpec(
        name="created_episode_ids", kind=_SUMMARY, source="created_episode_ids"
    ),
    PipelineTaskOutputSpec(
        name="replaced_episode_ids", kind=_SUMMARY, source="replaced_episode_ids"
    ),
    PipelineTaskOutputSpec(
        name="unchanged_episode_ids", kind=_SUMMARY, source="unchanged_episode_ids"
    ),
    PipelineTaskOutputSpec(
        name="removed_episode_ids", kind=_SUMMARY, source="removed_episode_ids"
    ),
    PipelineTaskOutputSpec(
        name="registered_episode_count",
        kind=_SUMMARY,
        source="registered_episode_count",
    ),
]

_VALIDATE_EPISODE_OUTPUTS = [
    PipelineTaskOutputSpec(
        name="validation_run_id", kind=_REF, source="validation_run_id"
    ),
    PipelineTaskOutputSpec(
        name="validation_report_uri",
        kind=_ARTIFACT,
        source="report_uri",
        target="validation_report_uri",
    ),
    PipelineTaskOutputSpec(
        name="validation_status",
        kind=_SUMMARY,
        source="status",
        target="validation_status",
    ),
    PipelineTaskOutputSpec(
        name="should_block_pipeline", kind=_SUMMARY, source="should_block_pipeline"
    ),
    PipelineTaskOutputSpec(
        name="checked_episode_count", kind=_SUMMARY, source="checked_episode_count"
    ),
    PipelineTaskOutputSpec(name="issue_count", kind=_SUMMARY, source="issue_count"),
]

_VALIDATE_EPISODE_QUALITY_RULES = [
    PipelineTaskQualityRule(
        rule_type=PipelineTaskQualityRuleType.BLOCK_IF_TRUE,
        source="summary.should_block_pipeline",
        message="Episode validation blocked pipeline",
        code="validate_episode_blocked",
    ),
]

_PROFILE_EPISODE_OUTPUTS = [
    PipelineTaskOutputSpec(name="profile_run_id", kind=_REF, source="profile_run_id"),
    PipelineTaskOutputSpec(
        name="profile_report_uri",
        kind=_ARTIFACT,
        source="report_uri",
        target="profile_report_uri",
    ),
    PipelineTaskOutputSpec(
        name="checked_episode_count", kind=_SUMMARY, source="checked_episode_count"
    ),
    PipelineTaskOutputSpec(
        name="observation_count", kind=_SUMMARY, source="observation_count"
    ),
    PipelineTaskOutputSpec(name="state_count", kind=_SUMMARY, source="state_count"),
    PipelineTaskOutputSpec(name="action_count", kind=_SUMMARY, source="action_count"),
    PipelineTaskOutputSpec(name="event_count", kind=_SUMMARY, source="event_count"),
]

_PREDICT_DETECTION_OUTPUTS = [
    # inference_run_id consumed by evaluate_detection → REF.
    PipelineTaskOutputSpec(
        name="inference_run_id", kind=_REF, source="inference_run_id"
    ),
    # The prediction revision evaluate_detection pins → REF.
    PipelineTaskOutputSpec(
        name="prediction_manifest_checksum",
        kind=_REF,
        source="prediction_manifest_checksum",
    ),
    # Prediction file URIs not consumed downstream → ARTIFACT.
    PipelineTaskOutputSpec(
        name="prediction_manifest_uri", kind=_ARTIFACT, source="prediction_manifest_uri"
    ),
    PipelineTaskOutputSpec(
        name="predictions_root_uri", kind=_ARTIFACT, source="predictions_root_uri"
    ),
    PipelineTaskOutputSpec(name="sample_count", kind=_SUMMARY, source="sample_count"),
    PipelineTaskOutputSpec(
        name="prediction_count", kind=_SUMMARY, source="prediction_count"
    ),
]

_EVALUATE_DETECTION_OUTPUTS = [
    # Run IDs kept as REFs for cross-referencing.
    PipelineTaskOutputSpec(
        name="evaluation_run_id", kind=_REF, source="evaluation_run_id"
    ),
    PipelineTaskOutputSpec(
        name="inference_run_id", kind=_REF, source="inference_run_id"
    ),
    # File URIs not consumed downstream → ARTIFACT.
    PipelineTaskOutputSpec(
        name="evaluation_manifest_uri", kind=_ARTIFACT, source="evaluation_manifest_uri"
    ),
    PipelineTaskOutputSpec(name="metrics_uri", kind=_ARTIFACT, source="metrics_uri"),
    # Counts / summary fields.
    PipelineTaskOutputSpec(name="sample_count", kind=_SUMMARY, source="sample_count"),
    PipelineTaskOutputSpec(
        name="prediction_count", kind=_SUMMARY, source="prediction_count"
    ),
    PipelineTaskOutputSpec(
        name="evaluable_prediction_count",
        kind=_SUMMARY,
        source="evaluable_prediction_count",
    ),
    PipelineTaskOutputSpec(
        name="lifting_failed_prediction_count",
        kind=_SUMMARY,
        source="lifting_failed_prediction_count",
    ),
    PipelineTaskOutputSpec(
        name="ground_truth_count", kind=_SUMMARY, source="ground_truth_count"
    ),
    PipelineTaskOutputSpec(
        name="evaluation_unit", kind=_SUMMARY, source="evaluation_unit"
    ),
    PipelineTaskOutputSpec(
        name="primary_metric_name", kind=_METRIC, source="primary_metric_name"
    ),
    PipelineTaskOutputSpec(
        name="primary_metric_value", kind=_METRIC, source="primary_metric_value"
    ),
]

# ── Pipeline definitions ───────────────────────────────────────────────────────

RECORDING_SCENE_BUILDING_PIPELINE = PipelineDefinition(
    type=PipelineType.RECORDING_SCENE_BUILDING,
    name="Recording Scene Building",
    description=(
        "Build the canonical Scenes of one registered RobotRun recording "
        "(robot_run_id + build_config), register them as the complete set "
        "of that recording scope in the DatasetVersion, then validate and "
        "profile the registered revisions. Builds from one RobotRun per run; "
        "a DatasetVersion spanning several RobotRuns takes several runs."
    ),
    tasks=[
        PipelineTaskDefinition(
            pipeline_task_id="build_recording_scenes",
            name="Build recording scenes",
            order=0,
            job_type=JobType.BUILD_RECORDING_SCENES,
            outputs=_BUILD_RECORDING_SCENES_OUTPUTS,
        ),
        PipelineTaskDefinition(
            pipeline_task_id="register_scenes",
            name="Register scenes",
            order=1,
            job_type=JobType.REGISTER_SCENES,
            depends_on_pipeline_task_ids=["build_recording_scenes"],
            outputs=_REGISTER_SCENES_OUTPUTS,
        ),
        PipelineTaskDefinition(
            pipeline_task_id="validate_scene",
            name="Validate scenes",
            order=2,
            job_type=JobType.VALIDATE_SCENE,
            depends_on_pipeline_task_ids=["register_scenes"],
            outputs=_VALIDATE_SCENE_OUTPUTS,
            quality_rules=_VALIDATE_SCENE_QUALITY_RULES,
        ),
        PipelineTaskDefinition(
            pipeline_task_id="profile_scene",
            name="Profile scenes",
            order=3,
            job_type=JobType.PROFILE_SCENE,
            depends_on_pipeline_task_ids=["register_scenes"],
            optional=True,
            outputs=_PROFILE_SCENE_OUTPUTS,
        ),
    ],
)


RECORDING_EPISODE_BUILDING_PIPELINE = PipelineDefinition(
    type=PipelineType.RECORDING_EPISODE_BUILDING,
    name="Recording Episode Building",
    description=(
        "Build the canonical Episodes of one registered RobotRun recording "
        "(robot_run_id + build_config), register them as the complete set "
        "of that recording scope in the DatasetVersion, then validate and "
        "profile the registered revisions. A sibling of "
        "recording_scene_building: Episodes are built from the recording, "
        "never from Scenes. Builds from one RobotRun per run."
    ),
    tasks=[
        PipelineTaskDefinition(
            pipeline_task_id="build_recording_episodes",
            name="Build recording episodes",
            order=0,
            job_type=JobType.BUILD_RECORDING_EPISODES,
            outputs=_BUILD_RECORDING_EPISODES_OUTPUTS,
        ),
        PipelineTaskDefinition(
            pipeline_task_id="register_episodes",
            name="Register episodes",
            order=1,
            job_type=JobType.REGISTER_EPISODES,
            depends_on_pipeline_task_ids=["build_recording_episodes"],
            outputs=_REGISTER_EPISODES_OUTPUTS,
        ),
        PipelineTaskDefinition(
            pipeline_task_id="validate_episode",
            name="Validate episodes",
            order=2,
            job_type=JobType.VALIDATE_EPISODE,
            depends_on_pipeline_task_ids=["register_episodes"],
            outputs=_VALIDATE_EPISODE_OUTPUTS,
            quality_rules=_VALIDATE_EPISODE_QUALITY_RULES,
        ),
        PipelineTaskDefinition(
            pipeline_task_id="profile_episode",
            name="Profile episodes",
            order=3,
            job_type=JobType.PROFILE_EPISODE,
            depends_on_pipeline_task_ids=["register_episodes"],
            optional=True,
            outputs=_PROFILE_EPISODE_OUTPUTS,
        ),
    ],
)


_MINE_SCENARIOS_OUTPUTS = [
    # REF: consumed by score_scenario_readiness / exposed as pipeline outputs
    PipelineTaskOutputSpec(name="scenario_set_id", kind=_REF, source="scenario_set_id"),
    PipelineTaskOutputSpec(
        name="scenario_set_checksum", kind=_REF, source="scenario_set_checksum"
    ),
    PipelineTaskOutputSpec(name="mining_run_id", kind=_REF, source="mining_run_id"),
    # ARTIFACT: lineage
    PipelineTaskOutputSpec(
        name="mining_report_uri",
        kind=_ARTIFACT,
        source="report_uri",
        target="mining_report_uri",
    ),
    # SUMMARY: task-level human-readable summary
    PipelineTaskOutputSpec(
        name="candidate_count_summary",
        kind=_SUMMARY,
        source="candidate_count",
        target="candidate_count",
    ),
    PipelineTaskOutputSpec(
        name="selected_count_summary",
        kind=_SUMMARY,
        source="selected_count",
        target="selected_count",
    ),
    PipelineTaskOutputSpec(
        name="rejected_count_summary",
        kind=_SUMMARY,
        source="rejected_count",
        target="rejected_count",
    ),
    # METRIC: pipeline-level numeric contract
    PipelineTaskOutputSpec(
        name="candidate_count_metric",
        kind=_METRIC,
        source="candidate_count",
        target="candidate_count",
    ),
    PipelineTaskOutputSpec(
        name="selected_count_metric",
        kind=_METRIC,
        source="selected_count",
        target="selected_count",
    ),
    PipelineTaskOutputSpec(
        name="rejected_count_metric",
        kind=_METRIC,
        source="rejected_count",
        target="rejected_count",
    ),
]

_SCORE_SCENARIO_READINESS_OUTPUTS = [
    # REF
    PipelineTaskOutputSpec(
        name="readiness_run_id", kind=_REF, source="readiness_run_id"
    ),
    # ARTIFACT
    PipelineTaskOutputSpec(
        name="readiness_report_uri",
        kind=_ARTIFACT,
        source="readiness_report_uri",
    ),
    # SUMMARY
    PipelineTaskOutputSpec(
        name="scored_scene_count_summary",
        kind=_SUMMARY,
        source="scored_scene_count",
        target="scored_scene_count",
    ),
    PipelineTaskOutputSpec(
        name="ready_count_summary",
        kind=_SUMMARY,
        source="ready_count",
        target="ready_count",
    ),
    PipelineTaskOutputSpec(
        name="warning_count_summary",
        kind=_SUMMARY,
        source="warning_count",
        target="warning_count",
    ),
    PipelineTaskOutputSpec(
        name="blocked_count_summary",
        kind=_SUMMARY,
        source="blocked_count",
        target="blocked_count",
    ),
    PipelineTaskOutputSpec(
        name="average_score_summary",
        kind=_SUMMARY,
        source="average_score",
        target="average_score",
    ),
    # METRIC
    PipelineTaskOutputSpec(
        name="scored_scene_count_metric",
        kind=_METRIC,
        source="scored_scene_count",
        target="scored_scene_count",
    ),
    PipelineTaskOutputSpec(
        name="ready_count_metric",
        kind=_METRIC,
        source="ready_count",
        target="ready_count",
    ),
    PipelineTaskOutputSpec(
        name="warning_count_metric",
        kind=_METRIC,
        source="warning_count",
        target="warning_count",
    ),
    PipelineTaskOutputSpec(
        name="blocked_count_metric",
        kind=_METRIC,
        source="blocked_count",
        target="blocked_count",
    ),
    PipelineTaskOutputSpec(
        name="average_score_metric",
        kind=_METRIC,
        source="average_score",
        target="average_score",
    ),
    # Optional: small downstream refs
    PipelineTaskOutputSpec(
        name="top_scene_ids",
        kind=_REF,
        source="top_scene_ids",
    ),
]


_BUILD_SCENE_SAMPLE_VIEWS_OUTPUTS = [
    # The pinned view revisions mine_scenarios curates from → REF.
    PipelineTaskOutputSpec(name="views", kind=_REF, source="views"),
    PipelineTaskOutputSpec(name="scene_count", kind=_SUMMARY, source="scene_count"),
    PipelineTaskOutputSpec(name="sample_count", kind=_SUMMARY, source="sample_count"),
    PipelineTaskOutputSpec(
        name="dropped_anchor_count", kind=_SUMMARY, source="dropped_anchor_count"
    ),
    PipelineTaskOutputSpec(name="created_count", kind=_SUMMARY, source="created_count"),
    PipelineTaskOutputSpec(name="reused_count", kind=_SUMMARY, source="reused_count"),
    PipelineTaskOutputSpec(name="skipped", kind=_SUMMARY, source="skipped"),
]

SCENE_ML_EVALUATION_PIPELINE = PipelineDefinition(
    type=PipelineType.SCENE_ML_EVALUATION,
    name="Scene ML Evaluation",
    description=(
        "Registered Scenes + an explicit sample-view policy + pinned label "
        "set revisions -> pinned sample views -> a curated ScenarioSet "
        "revision -> a prediction revision -> an evaluation against the "
        "pinned label set revision. Every stage pins the exact revisions it "
        "consumed; the canonical Scenes are never modified."
    ),
    tasks=[
        PipelineTaskDefinition(
            pipeline_task_id="build_scene_sample_views",
            name="Build scene sample views",
            order=0,
            job_type=JobType.BUILD_SCENE_SAMPLE_VIEWS,
            outputs=_BUILD_SCENE_SAMPLE_VIEWS_OUTPUTS,
        ),
        PipelineTaskDefinition(
            pipeline_task_id="mine_scenarios",
            name="Mine scenarios",
            order=1,
            job_type=JobType.MINE_SCENARIOS,
            depends_on_pipeline_task_ids=["build_scene_sample_views"],
            outputs=_MINE_SCENARIOS_OUTPUTS,
        ),
        PipelineTaskDefinition(
            pipeline_task_id="score_scenario_readiness",
            name="Score scenario readiness",
            order=2,
            job_type=JobType.SCORE_SCENARIO_READINESS,
            depends_on_pipeline_task_ids=["mine_scenarios"],
            outputs=_SCORE_SCENARIO_READINESS_OUTPUTS,
        ),
        PipelineTaskDefinition(
            pipeline_task_id="predict_detection",
            name="Predict detection",
            order=3,
            job_type=JobType.PREDICT_DETECTION,
            depends_on_pipeline_task_ids=["mine_scenarios"],
            default_params={
                "inference_backend": "mock",
            },
            outputs=_PREDICT_DETECTION_OUTPUTS,
        ),
        PipelineTaskDefinition(
            pipeline_task_id="evaluate_detection",
            name="Evaluate detection",
            order=4,
            job_type=JobType.EVALUATE_DETECTION,
            depends_on_pipeline_task_ids=["predict_detection"],
            default_params={
                "evaluator_id": "center-distance",
                "match_distance_m": 2.0,
            },
            outputs=_EVALUATE_DETECTION_OUTPUTS,
        ),
    ],
)


_ALIGN_EPISODE_OUTPUTS = [
    # The aligned revisions export_learning_data pins, in its input shape → REF.
    PipelineTaskOutputSpec(name="export_inputs", kind=_REF, source="export_inputs"),
    PipelineTaskOutputSpec(name="aligned", kind=_SUMMARY, source="aligned"),
    PipelineTaskOutputSpec(name="episode_count", kind=_SUMMARY, source="episode_count"),
    PipelineTaskOutputSpec(name="step_count", kind=_SUMMARY, source="step_count"),
    PipelineTaskOutputSpec(
        name="alignment_config_hash", kind=_SUMMARY, source="alignment_config_hash"
    ),
]

_EXPORT_LEARNING_DATA_OUTPUTS = [
    PipelineTaskOutputSpec(name="export_id", kind=_REF, source="export_id"),
    PipelineTaskOutputSpec(
        name="manifest_artifact_id", kind=_REF, source="manifest_artifact_id"
    ),
    PipelineTaskOutputSpec(name="manifest_uri", kind=_ARTIFACT, source="manifest_uri"),
    PipelineTaskOutputSpec(name="episode_count", kind=_SUMMARY, source="episode_count"),
    PipelineTaskOutputSpec(name="row_counts", kind=_SUMMARY, source="row_counts"),
    PipelineTaskOutputSpec(name="shard_counts", kind=_SUMMARY, source="shard_counts"),
]

EPISODE_LEARNING_DATA_BUILDING_PIPELINE = PipelineDefinition(
    type=PipelineType.EPISODE_LEARNING_DATA_BUILDING,
    name="Episode Learning Data Building",
    description=(
        "Pinned registered Episodes + an explicit alignment config -> one "
        "AlignedEpisode revision per Episode -> a learning data export of "
        "exactly those aligned revisions. The export validates every aligned "
        "input and fails rather than publishing an export over an invalid "
        "one. The canonical Episodes are never modified."
    ),
    tasks=[
        PipelineTaskDefinition(
            pipeline_task_id="align_episode",
            name="Align episodes",
            order=0,
            job_type=JobType.ALIGN_EPISODE,
            outputs=_ALIGN_EPISODE_OUTPUTS,
        ),
        PipelineTaskDefinition(
            pipeline_task_id="export_learning_data",
            name="Export learning data",
            order=1,
            job_type=JobType.EXPORT_LEARNING_DATA,
            depends_on_pipeline_task_ids=["align_episode"],
            outputs=_EXPORT_LEARNING_DATA_OUTPUTS,
        ),
    ],
)


BUILTIN_PIPELINE_DEFINITIONS = [
    RECORDING_SCENE_BUILDING_PIPELINE,
    RECORDING_EPISODE_BUILDING_PIPELINE,
    SCENE_ML_EVALUATION_PIPELINE,
    EPISODE_LEARNING_DATA_BUILDING_PIPELINE,
]


def create_builtin_pipeline_definition_registry() -> PipelineDefinitionRegistry:
    return PipelineDefinitionRegistry(BUILTIN_PIPELINE_DEFINITIONS)


_BUILTIN_REGISTRY: PipelineDefinitionRegistry | None = None


def get_pipeline_definition(
    pipeline_type: PipelineType,
) -> PipelineDefinition:
    global _BUILTIN_REGISTRY
    if _BUILTIN_REGISTRY is None:
        _BUILTIN_REGISTRY = create_builtin_pipeline_definition_registry()
    return _BUILTIN_REGISTRY.get(pipeline_type)
