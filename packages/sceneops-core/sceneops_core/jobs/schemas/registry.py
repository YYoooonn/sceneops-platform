from __future__ import annotations

from typing import TypeAlias

from sceneops_core.common.schemas import JsonDict

from .enums import JobType
from .params import (
    BaseJobParams,
    BuildDatasetManifestJobParams,
    BuildEpisodesJobParams,
    BuildSceneIndexJobParams,
    BuildScenesJobParams,
    EvaluateDetectionJobParams,
    ExportAnalyticsSnapshotJobParams,
    ExportRobotAnalyticsSnapshotJobParams,
    IngestScenesJobParams,
    MineScenariosJobParams,
    IngestRobotStatesJobParams,
    PredictDetectionJobParams,
    ProfileSceneJobParams,
    RegisterEpisodeJobParams,
    RegisterSceneJobParams,
    ScoreScenarioReadinessJobParams,
    ValidateSceneJobParams,
)
from .results import (
    BaseJobResult,
    BuildDatasetManifestJobResult,
    BuildEpisodesJobResult,
    BuildSceneIndexJobResult,
    BuildScenesJobResult,
    EvaluateDetectionJobResult,
    ExportAnalyticsSnapshotJobResult,
    ExportRobotAnalyticsSnapshotJobResult,
    IngestRobotStatesJobResult,
    IngestScenesJobResult,
    MineScenariosJobResult,
    PredictDetectionJobResult,
    ProfileSceneJobResult,
    RegisterEpisodeJobResult,
    RegisterSceneJobResult,
    ScoreScenarioReadinessJobResult,
    ValidateSceneJobResult,
)

JobParamsModel: TypeAlias = type[BaseJobParams]
JobResultModel: TypeAlias = type[BaseJobResult]

JOB_PARAM_SCHEMA_BY_TYPE: dict[JobType, JobParamsModel] = {
    JobType.INGEST_SCENES: IngestScenesJobParams,
    JobType.BUILD_SCENES: BuildScenesJobParams,
    JobType.BUILD_DATASET_MANIFEST: BuildDatasetManifestJobParams,
    JobType.BUILD_SCENE_INDEX: BuildSceneIndexJobParams,
    JobType.VALIDATE_SCENE: ValidateSceneJobParams,
    JobType.PROFILE_SCENE: ProfileSceneJobParams,
    JobType.REGISTER_SCENE: RegisterSceneJobParams,
    JobType.MINE_SCENARIOS: MineScenariosJobParams,
    JobType.SCORE_SCENARIO_READINESS: ScoreScenarioReadinessJobParams,
    JobType.EXPORT_ANALYTICS_SNAPSHOT: ExportAnalyticsSnapshotJobParams,
    JobType.PREDICT_DETECTION: PredictDetectionJobParams,
    JobType.EVALUATE_DETECTION: EvaluateDetectionJobParams,
    JobType.INGEST_ROBOT_STATES: IngestRobotStatesJobParams,
    JobType.EXPORT_ROBOT_ANALYTICS_SNAPSHOT: ExportRobotAnalyticsSnapshotJobParams,
    JobType.BUILD_EPISODES: BuildEpisodesJobParams,
    JobType.REGISTER_EPISODE: RegisterEpisodeJobParams,
}

JOB_RESULT_SCHEMA_BY_TYPE: dict[JobType, JobResultModel] = {
    JobType.INGEST_SCENES: IngestScenesJobResult,
    JobType.BUILD_SCENES: BuildScenesJobResult,
    JobType.BUILD_DATASET_MANIFEST: BuildDatasetManifestJobResult,
    JobType.BUILD_SCENE_INDEX: BuildSceneIndexJobResult,
    JobType.VALIDATE_SCENE: ValidateSceneJobResult,
    JobType.PROFILE_SCENE: ProfileSceneJobResult,
    JobType.REGISTER_SCENE: RegisterSceneJobResult,
    JobType.MINE_SCENARIOS: MineScenariosJobResult,
    JobType.SCORE_SCENARIO_READINESS: ScoreScenarioReadinessJobResult,
    JobType.EXPORT_ANALYTICS_SNAPSHOT: ExportAnalyticsSnapshotJobResult,
    JobType.PREDICT_DETECTION: PredictDetectionJobResult,
    JobType.EVALUATE_DETECTION: EvaluateDetectionJobResult,
    JobType.INGEST_ROBOT_STATES: IngestRobotStatesJobResult,
    JobType.EXPORT_ROBOT_ANALYTICS_SNAPSHOT: ExportRobotAnalyticsSnapshotJobResult,
    JobType.BUILD_EPISODES: BuildEpisodesJobResult,
    JobType.REGISTER_EPISODE: RegisterEpisodeJobResult,
}


def parse_job_params(job_type: JobType, params: JsonDict) -> BaseJobParams:
    schema = JOB_PARAM_SCHEMA_BY_TYPE.get(job_type)
    if schema is None:
        raise ValueError(f"Unsupported job type: {job_type}")
    return schema.model_validate(params)


def parse_job_result(job_type: JobType, result: JsonDict) -> BaseJobResult:
    schema = JOB_RESULT_SCHEMA_BY_TYPE.get(job_type)
    if schema is None:
        raise ValueError(f"Unsupported job type: {job_type}")
    return schema.model_validate(result)
