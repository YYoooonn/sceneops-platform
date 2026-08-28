# SceneOps Data Model

> Phase 0 문서. `packages/sceneops-db/sceneops_db/models/*.py`를 기준으로 실제 스키마를
> 기록한다. 필드는 전부 나열하지 않고 엔티티의 역할과 핵심 관계·상태값 위주로 정리한다.
> 전체 필드는 각 모델 파일을 직접 참고.

## 1. 엔티티 개요

```text
Dataset
 └─ DatasetVersion
     └─ DatasetRunRecord (validation / profile / distribution / export)

SceneRecord (scenes 테이블)
 └─ SceneRunRecord (validation / profile / comparison / reconstruction / package_export)

ScenarioSet
 └─ ScenarioRunRecord (mining / readiness)

PipelineRun
 └─ PipelineTaskRun (순서·의존성 포함)

Job
 └─ JobEvent (job 실행 로그)

ExecutionRecord   (Job/Pipeline을 실제 백엔드에 보낸 기록)

InferenceRun (PredictionRun)
EvaluationRun

Artifact          (모든 리소스가 참조하는 바이너리/JSON 산출물 메타데이터)
```

로드맵이 상정한 `SceneManifest`, `SceneLineage`는 별도 테이블이 아니라 `SceneRecord`의
필드(`scene_manifest_uri`, `lineage` JSONB)로 흡수되어 있다. `PredictionRun`은 코드에서
`InferenceRunModel`(테이블명 `inference_runs`)로 존재한다.

## 2. Dataset / DatasetVersion

`datasets` — 논리적 데이터셋 (예: `nuscenes`). `dataset_id`가 PK, `default_version` 포인터를 가짐.

`dataset_versions` — 버전별 스냅샷. `(dataset_id, version)` unique.

핵심 필드:
- `status`: `registered` 등 lifecycle 상태
- `scene_count` / `sample_count` / `frame_count`: 버전 통계
- `channels` / `required_channels`: 센서 채널 목록 (JSONB)
- `manifest_uri`, `raw_source_root_uri`: ArtifactStore 참조
- `latest_validation_run_id` / `validation_status` / `should_block_pipeline` /
  `validation_report_uri`: 최신 VALIDATE 결과를 역참조 캐시로 보관
- `latest_profile_run_id` / `profile_report_uri`, `latest_distribution_run_id` /
  `distribution_report_uri`: 동일 패턴으로 PROFILE, distribution 결과 캐시

즉 `DatasetVersion`은 최신 quality run의 결과를 스스로 캐싱해서, 매번
`dataset_run_records`를 조인하지 않고도 "이 버전은 지금 사용 가능한가?"에 즉시 답할 수
있게 설계되어 있다.

`dataset_run_records` — dataset 범위 run을 `type` 컬럼으로 통합한 테이블
(`dataset_validation` / `dataset_profile` / `dataset_distribution` / `dataset_export`).
공통 필드: `params`/`result`/`error`/`summary`/`metrics` (모두 JSONB),
`pipeline_run_id`/`pipeline_task_run_id`/`job_id`로 실행 계보 연결.

## 3. SceneRecord

`scenes` — SceneOps의 핵심 단위. 로드맵의 `SceneRecord`/`SceneManifest`/`SceneLineage`를
한 테이블로 표현한다.

핵심 필드:
- `origin_type`, `generation_method`: scene이 어디서 왔는지 (raw ingestion vs
  reconstruction vs simulation 등)
- `parent_scene_id`, `lineage` (JSONB): scene 계보 — 재구성/파생 scene의 부모 추적
- `scene_manifest_uri`, `world_state_manifest_uri`, `artifact_root_uri`: ArtifactStore 참조
- `has_ground_truth` / `ground_truth_source`: GT 존재 여부와 출처
- `sample_count` / `frame_count` / `annotation_count` / `channels`: scene 통계

`scene_run_records` — scene 범위 run 통합 테이블 (`scene_validation` / `scene_profile` /
`scene_comparison` / `scene_reconstruction` / `scene_package_export`). `source_scene_id`/
`target_scene_id` 필드가 있어 scene 간 비교·재구성 run도 같은 테이블로 표현한다.

## 4. ScenarioSet

`scenario_sets` — 특정 `(dataset_id, dataset_version)`에서 큐레이션된 시나리오 묶음.
`scenario_set_uri`로 실제 목록을 참조, `tags`로 분류.

`scenario_run_records` — `scenario_mining` / `scenario_readiness` 두 타입.
readiness run은 `ready_count`/`blocked_count`/`warning_count`/`average_score` 같은
전용 집계 컬럼을 가진다 — [Scenario Curation Pipeline](../memory/project_scenario_curation_pipeline.md)에서
`mine_scenarios` → `score_scenario_readiness`로 이어지는 흐름과 대응.

## 5. PipelineRun / PipelineTaskRun

`pipeline_runs` — 하나의 파이프라인 실행. `type`은 `PipelineType` enum
(`dataset_scene_ingestion`, `raw_log_scene_building`, `scene_reconstruction`,
`scene_registration`, `scenario_curation`, `generated_dataset_preparation`,
`detection_evaluation`).

`pipeline_task_runs` — 파이프라인 내 개별 태스크. `task_order`로 순서,
`depends_on_task_ids`(JSONB)로 의존성을 표현하지만, 현재 `PipelineRunner`는 이 의존성
그래프를 병렬 스케줄링에 쓰지 않고 `task_order`만으로 순차 실행한다
([architecture.md](./architecture.md) 참고). `job_type`/`job_id`로 실제 실행은 Job에
위임한다.

Status enum (`PipelineRunStatus`): `pending → queued → running → (blocked | succeeded |
failed | cancelled)`. `blocked`는 quality gate가 막은 경우로, 로드맵의
`QUARANTINED`에 해당하는 개념이 파이프라인 레벨로 구현되어 있다.

## 6. Job / JobEvent

`jobs` — 실제 작업 단위. `type`은 `JobType` enum:

```text
INGEST_SCENES, BUILD_SCENES                        # 원본 → scene
BUILD_DATASET_MANIFEST, BUILD_SCENE_INDEX           # dataset 레벨 집계
VALIDATE_SCENE, PROFILE_SCENE, REGISTER_SCENE,
COMPARE_SCENES, AUTO_LABEL_SCENE, EXPORT_SCENE_PACKAGE   # scene 레벨
MINE_SCENARIOS, SCORE_SCENARIO_READINESS            # scenario 레벨
AUTO_LABEL_DATASET, CHECK_DISTRIBUTION, EXPORT_DATASET   # dataset version 레벨
PREDICT_DETECTION, EVALUATE_DETECTION               # detection
```

로드맵 섹션 2의 8개 operation(INGEST/BUILD/REGISTER/VALIDATE/PROFILE/CURATE/PREDICT/
EVALUATE)은 실제로는 이 17개 JobType으로 세분화되어 있다.

`retry_count`/`max_retries`/`worker_id`/`queued_at`/`locked_at`/`heartbeat_at` 필드가
스키마에는 존재하지만, [architecture.md](./architecture.md)에서 확인했듯 `JobRunner`는
이를 이용한 자체 재시도·backfill 로직을 구현하지 않고 있다 — Celery 레벨 재시도만 동작.
스키마는 이미 준비되어 있고 실행 로직만 비어 있는 상태.

`job_events` — job 실행 중 로그/이벤트 스트림. `level`, `attempt`, `job_step_id` 등으로
세밀한 추적이 가능.

## 7. InferenceRun (PredictionRun) / EvaluationRun

`inference_runs` — 모델 추론 실행. `dataset_id`+`dataset_version`+`model_id`+
`model_version`으로 재현성의 4요소를 명시적으로 고정한다. `predictions_root_uri`,
`prediction_manifest_uri`로 결과 위치 참조.

`evaluation_runs` — 평가 실행. `inference_run_id`로 어떤 예측 결과를 평가했는지 연결,
`evaluator_id`(예: `center-distance`), `task_type`(예: `detection`)으로 평가 방식 명시.
`primary_metric_name`/`primary_metric_value`로 대표 지표를 승격, `class_metrics`
(JSONB)로 클래스별 세부 지표 보관.

두 테이블 모두 `dataset_id`+`dataset_version`+`model_id`+`model_version`을 명시적으로
고정하고 있어, 로드맵 Phase 3의 "이 Evaluation은 정확히 어떤 데이터 버전을 사용했는가?"
질문에 이미 답할 수 있는 구조다.

## 8. Artifact

`artifacts` — 모든 바이너리/JSON 산출물의 메타데이터 인덱스. `kind`, `uri`, `backend`로
저장 위치 식별, `owner_type`/`owner_id`(다형적) 또는 `dataset_id`/`scene_id`/
`scenario_set_id`/`run_id`/`job_id`/`pipeline_run_id` 중 하나로 소유 리소스와 연결.
`size_bytes`/`checksum`으로 무결성 검증 가능한 필드는 있으나, 실제로 채워지는지는
worker 쓰기 경로 확인 필요 (미검증).

## 9. ExecutionRecord

`execution_records` — Job/Pipeline을 실제 실행 백엔드(현재는 Celery)로 보낸 기록.
`execution_backend`, `execution_kind`, `resource_id`(job_id 또는 pipeline_run_id),
`external_id`(Celery task id)로 제어 평면의 논리적 리소스와 실행 백엔드의 물리적 실행을
분리한다. 향후 Airflow를 추가 백엔드로 넣을 때 이 테이블이 추상화 지점이 된다
([ADR 004](./adr/004-airflow-vs-celery.md)).

## 10. 로드맵과의 차이

| 로드맵 개념 | 실제 구현 |
|---|---|
| `SceneManifest` (별도 엔티티) | `SceneRecord.scene_manifest_uri` 필드로 흡수 |
| `SceneLineage` (별도 엔티티) | `SceneRecord.lineage` JSONB + `parent_scene_id` |
| `PredictionRun` | `InferenceRunModel` (테이블 `inference_runs`) |
| 8개 operation (INGEST~EVALUATE) | 17개 `JobType`으로 세분화 |
| Quality state (`PASS`/`FAIL`/`QUARANTINED`) | `DatasetVersion.validation_status` +
  `should_block_pipeline` + `PipelineRunStatus.BLOCKED` 조합으로 구현 |
