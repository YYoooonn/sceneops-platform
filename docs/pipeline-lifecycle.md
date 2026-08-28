# SceneOps Pipeline Lifecycle

> Phase 0 문서. `packages/sceneops-core/sceneops_core/pipelines/builtin.py`와
> `apps/worker/sceneops_worker/pipelines/`, `apps/worker/sceneops_worker/jobs/`를 기준으로
> 실제 실행 lifecycle을 기록한다.

## 1. 두 개의 실행 단위: Pipeline과 Job

```text
PipelineRun
 └─ PipelineTaskRun (order, depends_on)
     └─ 위임 → Job
             └─ 실제 handler 실행 (JobType별)
```

Pipeline은 여러 Job을 정해진 순서로 엮은 것이고, Job은 실제 작업을 수행하는 최소 단위다.
API는 Job을 단독으로도, Pipeline으로 묶어서도 dispatch할 수 있다.

## 2. 내장 Pipeline 정의 (`PipelineType` → task 체인)

로드맵 섹션 2의 8개 operation(INGEST/BUILD/REGISTER/VALIDATE/PROFILE/CURATE/PREDICT/
EVALUATE)은 실제로는 7개의 named pipeline으로 조합되어 있다:

```text
DATASET_SCENE_INGESTION
  ingest_scenes → register_scene → validate_scene → profile_scene
  → build_scene_index → build_dataset_manifest

RAW_LOG_SCENE_BUILDING
  build_scenes → register_scene → validate_scene → profile_scene
  → build_scene_index → build_dataset_manifest

SCENE_RECONSTRUCTION
  build_scenes → validate_scene → profile_scene → export_scene_package

SCENE_REGISTRATION
  register_scene → validate_scene → profile_scene → compare_scenes

SCENARIO_CURATION
  mine_scenarios → score_scenario_readiness

GENERATED_DATASET_PREPARATION
  register_scene → compare_scenes → auto_label_scene
  → build_dataset_manifest → check_distribution → export_dataset

DETECTION_EVALUATION
  predict_detection → evaluate_detection
```

같은 `validate_scene → profile_scene` 패턴이 3개 파이프라인(ingestion, raw-log-building,
reconstruction/registration)에 공통으로 나타난다 — VALIDATE/PROFILE이 데이터 출처와
무관하게 재사용되는 공통 quality 단계로 설계되어 있다는 뜻이다.

## 3. Task 간 데이터 전달: Output Kind

각 task의 출력은 `PipelineTaskOutputKind`로 분류된다 (`builtin.py`):

- `REF`: 다음 task가 입력으로 소비하는 참조값 (예: `scene_manifest_uris`,
  `validation_run_id`) — task 간 실질적 연결 지점
- `SUMMARY`: 집계 통계, DB 컬럼에 그대로 기록 (예: `scene_count`, `issue_count`)
- `METRIC`: 평가 지표류
- `ARTIFACT`: 다운스트림에서 소비되지 않는 산출물 URI (리포트, 로그성 파일)

이 구분 덕분에 어떤 출력이 "파이프라인을 흐르는 데이터"이고 어떤 것이 "그냥 기록되는
부산물"인지 정의 시점에 명시적으로 구분된다.

## 4. Quality Gate

`PipelineTaskQualityRule`로 task 성공 이후에도 파이프라인을 막을 수 있다. 예:

```python
PipelineTaskQualityRule(
    rule_type=PipelineTaskQualityRuleType.BLOCK_IF_TRUE,
    source="summary.should_block_pipeline",
    message="Scene validation blocked pipeline",
    code="validate_scene_blocked",
)
```

`validate_scene` task가 `should_block_pipeline=true`를 반환하면, task 자체는
`SUCCEEDED`여도 파이프라인은 `PipelineRunStatus.BLOCKED`로 종료된다. 이것이 로드맵
섹션 9.3의 `QUARANTINED` 상태에 대응하는 실제 구현이다 — 별도 quarantine 상태 대신
"파이프라인 진행을 막는다"는 형태로 구현되어 있다.

## 5. 실행 흐름

### PipelineRunner (`sceneops_worker/pipelines/runner.py`)

1. `pipeline_run_id`로 run 로드, 상태 검증 (`SUCCEEDED`/`RUNNING`/`CANCELLED`이면
   재실행 거부; `BLOCKED`/`FAILED`는 재실행 허용 — quality gate가 막았거나
   실패한 pipeline을 원인 수정 후 재시도할 수 있어야 하기 때문. API 레벨
   `PipelineService.validate_executable`과 이 규칙을 일치시켰다 — 과거엔
   worker가 `BLOCKED` 재실행을 거부해 API와 어긋났던 버그가 있었다)
2. `RUNNING`으로 전이
3. `PipelineDefinition.tasks`를 `order`로 정렬해 **순차** 실행 (`depends_on_task_ids`는
   현재 스케줄링에 쓰이지 않음 — 항상 order 순서). 이미 `SUCCEEDED`인 task는
   `PipelineTaskRunner`가 재실행 없이 스킵한다(`_handle_pre_execution_state`) —
   즉 재실행은 실패/블록된 task부터 재개되는 **부분 재시도**가 이미 기본 동작이다
4. task가 blocked면 즉시 파이프라인을 `BLOCKED`로 종료
5. 예외 발생 시 완료된 task 목록과 함께 파이프라인을 `FAILED`로 종료 후 re-raise
   (→ Celery의 `autoretry_for`가 잡아서 전체 파이프라인을 처음부터 재시도)

### JobRunner (`sceneops_worker/jobs/runner.py`)

1. `claim_for_run`으로 job을 `PENDING`/`QUEUED` 상태에서만 claim (동시 실행 방지)
2. `RUNNING` 전이 → handler 실행 → 결과 기록
3. 예외 시 컨텍스트 rollback 후 실패 기록, re-raise

## 6. Reliability 상태 — 로드맵 Phase 2 대비 gap

로드맵 Phase 2(Airflow)는 다음 4가지를 완료 기준으로 요구한다. 실제 코드를
두 차례 정밀 조사한 결과, 처음 문서화했을 때 예상한 것보다 gap이 좁았다 —
아래는 조사 후 구현까지 마친 현재 상태:

| 요구사항 | 현재 구현 |
|---|---|
| Retry (task 단위) | Celery `autoretry_for=(Exception,)`가 파이프라인/Job 전체를 재시도하는 것과는 별개로, `PipelineTaskRunner`가 이미 `SUCCEEDED` task를 스킵하기 때문에 **사람이 명시적으로 재dispatch하면 실패한 task부터만 재개**된다. Job 자체(standalone) 재시도는 `jobs.retry_count`/`max_retries`를 `JobService.mark_queued`가 실제로 증가·검증하도록 wiring — `FAILED` job을 cap 초과해서 재시도하면 `ValueError`로 막힌다 |
| Idempotency | `execution_key = sha256(kind, type, dataset_id, dataset_version, model_id, model_version, params)` (`sceneops_core.executions.compute_execution_key`)로 구현. `Job`/`PipelineRun` 생성 시 동일 키의 `PENDING`/`QUEUED`/`RUNNING`/`SUCCEEDED` 레코드가 있으면 새로 만들지 않고 기존 것을 반환한다. `force: true`로 강제 재생성 가능 |
| Partial failure | `PipelineTaskRunner._handle_pre_execution_state`가 이미 `SUCCEEDED`인 task를 재실행 없이 skip — 재dispatch가 곧 부분 재시도. `PipelineRunner._validate_runnable`이 `BLOCKED` pipeline의 재실행을 막던 버그를 수정해 quality gate에 걸린 pipeline도 (원인 수정 후) 재개 가능하게 했다 |
| Backfill | 새 기능 없음 — SceneOps는 시간 파티션 실행(daily DAG run) 개념이 없고, 모든 pipeline_run이 이미 `(dataset_id, dataset_version)` 단위로 독립 scope다. 이 도메인에서 backfill은 "그 버전에 대해 새 pipeline_run을 하나 더 dispatch한다"와 동일해서 별도 구현이 불필요하다고 판단했다 |

구현 위치: `packages/sceneops-core/sceneops_core/executions/key.py`,
`apps/api/app/platform/jobs/service.py`(`create_job`, `mark_queued`),
`apps/api/app/platform/pipelines/service.py`(`create_pipeline_run`),
`apps/worker/sceneops_worker/pipelines/runner.py`(`_validate_runnable`).
자세한 배경은 [ADR 004](./adr/004-airflow-vs-celery.md).

이 primitives들은 Airflow 도입 여부와 무관하게 유효하다 — Airflow의 재시도도
결국 "이미 성공한 작업은 건너뛴다"는 동일한 idempotency 판단에 의존하므로,
지금 만든 `execution_key`/부분 재시도 로직은 Airflow로 옮겨가도 재사용된다.

## 7. Job dispatch 시 상태 전이 (API ↔ Worker 경합 방지)

`JobDispatchFacade`는 "DB에 QUEUED 커밋 → Celery 전송" 순서를 강제한다
([architecture.md](./architecture.md) 4절). 이 순서를 지키지 않으면 worker가 이미
`RUNNING`으로 옮긴 상태를 API의 지연된 `QUEUED` 커밋이 덮어써버릴 수 있다 — Airflow
도입 시에도 동일한 race condition을 신경 써야 한다.

## 8. Airflow 경로에서의 pipeline 실행 (per-task DAG PoC)

`pipeline_backend=airflow`일 때 `dataset_scene_ingestion`은 `PipelineRunner.run()`
한 프로세스가 아니라, task마다 별도 프로세스(`sceneops-worker run-pipeline-task`,
Airflow `DockerOperator`가 기존 worker 이미지로 실행)로 나뉘어 돈다. 이 경로에서
"파이프라인 레벨 상태를 누가 언제 쓰는가"는 Celery 경로와 다르다:

| 컴포넌트 | Celery 경로 | Airflow 경로 |
|---|---|---|
| Task 실행 + quality gate 평가 | `PipelineTaskRunner.run()` (loop 안에서 호출) | `PipelineTaskRunner.run()` (동일 코드, `run-pipeline-task` CLI가 호출) |
| `pipeline_runs.status = RUNNING` | `PipelineRunner._start_pipeline` (loop 시작 전) | `PipelineRunner.start()` — DAG의 첫 task (`sceneops-worker pipelines start`) |
| `pipeline_runs.status` 최종 확정 | `PipelineRunner._succeed_pipeline`/`_block_pipeline`/`_fail_pipeline` (loop 안에서 즉시) | `PipelineRunner.finalize()` — DAG의 마지막 task, `trigger_rule=all_done`이라 upstream 성공/실패 무관하게 항상 실행. 저장된 task run 상태를 조회해 `BLOCKED` > `FAILED` > `SUCCEEDED` 우선순위로 판정 |

`start`/`finalize`는 `run()`이 이미 쓰던 4개의 private 상태-전이 메서드를 그대로
재사용한다 — 새로운 판정 로직이 아니라 호출 시점만 다르게 재조합한 것
(`apps/worker/sceneops_worker/pipelines/runner.py`).

DAG 구조 (`airflow/dags/sceneops_pipeline_run.py`):

```text
start
  → ingest_scenes → register_scene → validate_scene
  → profile_scene → build_scene_index → build_dataset_manifest
  → finalize  (trigger_rule=all_done)
```

**구현 중 발견한 버그**: `run-pipeline-task`(`sceneops-worker` CLI, "Airflow용으로
설계됨"이라는 주석이 있던 커맨드)는 실제로는 한 번도 정상 동작한 적이 없었다 —
`PipelineTaskRunner.run()`이 반환하는 `PipelineTaskRunResult`(`.outcome`,
`.task_run`)를 `job.job_id`/`job.status.value`처럼 존재하지 않는 속성으로 읽고
있었다(AttributeError). 또한 `BLOCKED` outcome은 예외 없이 정상 반환되므로,
CLI가 그걸 구분하지 않으면 종료 코드가 0이 되어 Airflow의 `>>` 의존성 체인이
막힌 task 뒤에도 계속 진행해버린다. 둘 다 이번에 수정 — `BLOCKED`도 이제
non-zero exit으로 처리한다.

**한계**: 이 DAG는 `dataset_scene_ingestion` 하나에만 하드코딩되어 있다 (task
체인이 고정). 다른 pipeline type을 Airflow로 보내려면 DAG를 일반화하거나
타입별 DAG를 추가해야 한다 — 이번 PoC 범위 밖.
