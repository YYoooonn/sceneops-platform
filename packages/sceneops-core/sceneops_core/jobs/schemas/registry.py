from __future__ import annotations

from typing import TypeAlias

from sceneops_core.common.schemas import JsonDict

from .enums import JobType
from .params import (
    AlignEpisodeJobParams,
    BaseJobParams,
    BuildSceneSampleViewsJobParams,
    ImportLabelsJobParams,
    BuildRecordingEpisodesJobParams,
    BuildRecordingScenesJobParams,
    CurateEpisodesJobParams,
    EvaluateDetectionJobParams,
    ExportAnalyticsSnapshotJobParams,
    ExportLearningDataJobParams,
    ExportRobotAnalyticsSnapshotJobParams,
    MineScenariosJobParams,
    IngestRobotStatesJobParams,
    RegisterRobotRunJobParams,
    PredictDetectionJobParams,
    ProfileAlignedEpisodeJobParams,
    ProfileEpisodeJobParams,
    ProfileSceneJobParams,
    RegisterEpisodesJobParams,
    RegisterScenesJobParams,
    ScoreScenarioReadinessJobParams,
    ValidateAlignedEpisodeJobParams,
    ValidateEpisodeJobParams,
    ValidateSceneJobParams,
)
from .results import (
    AlignEpisodeJobResult,
    BaseJobResult,
    BuildSceneSampleViewsJobResult,
    ImportLabelsJobResult,
    BuildRecordingEpisodesJobResult,
    BuildRecordingScenesJobResult,
    CurateEpisodesJobResult,
    EvaluateDetectionJobResult,
    ExportAnalyticsSnapshotJobResult,
    ExportLearningDataJobResult,
    ExportRobotAnalyticsSnapshotJobResult,
    IngestRobotStatesJobResult,
    RegisterRobotRunJobResult,
    MineScenariosJobResult,
    PredictDetectionJobResult,
    ProfileAlignedEpisodeJobResult,
    ProfileEpisodeJobResult,
    ProfileSceneJobResult,
    RegisterEpisodesJobResult,
    RegisterScenesJobResult,
    ScoreScenarioReadinessJobResult,
    ValidateAlignedEpisodeJobResult,
    ValidateEpisodeJobResult,
    ValidateSceneJobResult,
)

JobParamsModel: TypeAlias = type[BaseJobParams]
JobResultModel: TypeAlias = type[BaseJobResult]

JOB_PARAM_SCHEMA_BY_TYPE: dict[JobType, JobParamsModel] = {
    JobType.BUILD_RECORDING_SCENES: BuildRecordingScenesJobParams,
    JobType.IMPORT_LABELS: ImportLabelsJobParams,
    JobType.BUILD_SCENE_SAMPLE_VIEWS: BuildSceneSampleViewsJobParams,
    JobType.VALIDATE_SCENE: ValidateSceneJobParams,
    JobType.PROFILE_SCENE: ProfileSceneJobParams,
    JobType.REGISTER_SCENES: RegisterScenesJobParams,
    JobType.MINE_SCENARIOS: MineScenariosJobParams,
    JobType.SCORE_SCENARIO_READINESS: ScoreScenarioReadinessJobParams,
    JobType.EXPORT_ANALYTICS_SNAPSHOT: ExportAnalyticsSnapshotJobParams,
    JobType.PREDICT_DETECTION: PredictDetectionJobParams,
    JobType.EVALUATE_DETECTION: EvaluateDetectionJobParams,
    JobType.REGISTER_ROBOT_RUN: RegisterRobotRunJobParams,
    JobType.INGEST_ROBOT_STATES: IngestRobotStatesJobParams,
    JobType.EXPORT_ROBOT_ANALYTICS_SNAPSHOT: ExportRobotAnalyticsSnapshotJobParams,
    JobType.BUILD_RECORDING_EPISODES: BuildRecordingEpisodesJobParams,
    JobType.REGISTER_EPISODES: RegisterEpisodesJobParams,
    JobType.VALIDATE_EPISODE: ValidateEpisodeJobParams,
    JobType.PROFILE_EPISODE: ProfileEpisodeJobParams,
    JobType.ALIGN_EPISODE: AlignEpisodeJobParams,
    JobType.VALIDATE_ALIGNED_EPISODE: ValidateAlignedEpisodeJobParams,
    JobType.PROFILE_ALIGNED_EPISODE: ProfileAlignedEpisodeJobParams,
    JobType.EXPORT_LEARNING_DATA: ExportLearningDataJobParams,
    JobType.CURATE_EPISODES: CurateEpisodesJobParams,
}

JOB_RESULT_SCHEMA_BY_TYPE: dict[JobType, JobResultModel] = {
    JobType.BUILD_RECORDING_SCENES: BuildRecordingScenesJobResult,
    JobType.IMPORT_LABELS: ImportLabelsJobResult,
    JobType.BUILD_SCENE_SAMPLE_VIEWS: BuildSceneSampleViewsJobResult,
    JobType.VALIDATE_SCENE: ValidateSceneJobResult,
    JobType.PROFILE_SCENE: ProfileSceneJobResult,
    JobType.REGISTER_SCENES: RegisterScenesJobResult,
    JobType.MINE_SCENARIOS: MineScenariosJobResult,
    JobType.SCORE_SCENARIO_READINESS: ScoreScenarioReadinessJobResult,
    JobType.EXPORT_ANALYTICS_SNAPSHOT: ExportAnalyticsSnapshotJobResult,
    JobType.PREDICT_DETECTION: PredictDetectionJobResult,
    JobType.EVALUATE_DETECTION: EvaluateDetectionJobResult,
    JobType.REGISTER_ROBOT_RUN: RegisterRobotRunJobResult,
    JobType.INGEST_ROBOT_STATES: IngestRobotStatesJobResult,
    JobType.EXPORT_ROBOT_ANALYTICS_SNAPSHOT: ExportRobotAnalyticsSnapshotJobResult,
    JobType.BUILD_RECORDING_EPISODES: BuildRecordingEpisodesJobResult,
    JobType.REGISTER_EPISODES: RegisterEpisodesJobResult,
    JobType.VALIDATE_EPISODE: ValidateEpisodeJobResult,
    JobType.PROFILE_EPISODE: ProfileEpisodeJobResult,
    JobType.ALIGN_EPISODE: AlignEpisodeJobResult,
    JobType.VALIDATE_ALIGNED_EPISODE: ValidateAlignedEpisodeJobResult,
    JobType.PROFILE_ALIGNED_EPISODE: ProfileAlignedEpisodeJobResult,
    JobType.EXPORT_LEARNING_DATA: ExportLearningDataJobResult,
    JobType.CURATE_EPISODES: CurateEpisodesJobResult,
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
