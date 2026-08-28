# SceneOps Architecture

> SceneOps 2.0 로드맵 Phase 0 문서. 현재(2026-08-20, `dev` 브랜치) 코드베이스를 기준으로
> 실제로 구현되어 있는 구조를 기록한다. 목표 아키텍처(Airflow/Spark/Kafka 등)는
> [`sceneops_2_0_ros2_roadmap.md`](../.claude/sceneops_2_0_ros2_roadmap.md)를 참고.

## 1. 시스템 개요

SceneOps는 세 개의 layer로 구성된다.

```text
Control Plane (apps/api)
        │
        ▼
Execution (apps/worker, apps/inference-server)
        │
        ▼
Storage (PostgreSQL + ArtifactStore)
```

세 layer는 각각 독립 배포 가능한 프로세스이며, `packages/` 아래의 공유 라이브러리로 연결된다.

```text
packages/
  sceneops-core     도메인 스키마, 계약(Protocol), pipeline/job 정의 — 순수 Python, I/O 없음
  sceneops-db       SQLAlchemy 모델, repository, converter — PostgreSQL 접근
  sceneops-storage  ArtifactStore 구현체(Local/S3) — 바이너리/JSON 아티팩트 접근
```

`sceneops-core`는 API/worker 어디서도 직접 DB나 스토리지에 접근하지 않고, `ArtifactStore`
Protocol과 pydantic 스키마만 정의한다. 실제 구현은 `sceneops-db`, `sceneops-storage`가
제공하고 API/worker가 이를 주입받아 사용하는 port-adapter 구조다.

## 2. Control Plane — `apps/api`

FastAPI 애플리케이션. 도메인 리소스에 대한 CRUD/조회 API와, 실행을 큐에 넣는 "dispatch"
API로 나뉜다.

```text
apps/api/app/
  domains/           리소스 중심 도메인 API
    datasets/        Dataset, DatasetVersion, quality
    scenes/           SceneRecord, quality
    scenarios/        ScenarioSet
    inference/         PredictionRun (InferenceRun)
    evaluations/       EvaluationRun
    labels/             라벨링
    models/             모델 레지스트리
  platform/          실행 인프라 API
    jobs/            Job 생성·조회·dispatch
    pipelines/       PipelineRun 생성·조회·dispatch, 내장 파이프라인 정의
    executions/      Job/Pipeline을 실제 백엔드(Celery)로 보내는 dispatch facade
    artifacts/       Artifact 메타데이터 조회
  views/             집계/조합 API (여러 도메인을 조인)
    leaderboards/    모델 비교 리더보드
    operations/       운영 대시보드용 뷰
```

각 도메인은 `router.py` → `service.py` → (`sceneops-db` repository) 형태로 계층화되어
있다. `dependencies.py`가 FastAPI DI로 세션과 repository를 조립한다.

### Dispatch 흐름

API는 실행을 직접 수행하지 않는다. `JobDispatchFacade` / `PipelineDispatchFacade`가
1) DB에 `QUEUED` 상태로 커밋 → 2) `ExecutionService`를 통해 백엔드로 실행 요청 전송,
순서로 동작한다 (`apps/api/app/platform/jobs/dispatch_facade.py`,
`apps/api/app/platform/pipelines/dispatch_facade.py`).

커밋을 백엔드 dispatch보다 먼저 하는 이유는 주석에 명시되어 있다: worker가 이미
`RUNNING`/`SUCCEEDED`로 갱신한 상태를 API의 지연된 `QUEUED` 커밋이 덮어쓰는 것을
방지하기 위함이다. dispatch가 실패하면 Job은 `QUEUED`로 남아 재전송(redispatch) 가능하다.

`ExecutionService`는 job/pipeline 각각에 대해 백엔드를 선택할 수 있는 구조로
이미 설계되어 있었다 (`apps/api/app/platform/executions/dependencies.py`의
`get_pipeline_execution_backend`가 `settings.execution.pipeline_backend`
값으로 `CeleryPipelineExecutionBackend` 또는 `AirflowPipelineExecutionBackend`를
선택). 현재 실제로 구현되어 있는 건:

```text
Job      → Celery만 지원 (job_backend는 celery 고정)
Pipeline → Celery 또는 Airflow (pipeline_backend로 전환 가능)
```

Pipeline을 Airflow로 보낼 때는 `AirflowPipelineExecutionBackend.dispatch_pipeline`이
Airflow REST API(`POST /api/v1/dags/{dag_id}/dagRuns`)를 호출해 DAG run을
트리거한다 (`dag_run_id`를 SceneOps `pipeline_run_id`와 동일하게 지정해 1:1
추적 가능). 두 백엔드 모두 동일하게 `ExecutionRecord`에 기록되므로
(`execution_backend` 컬럼으로 구분), API/UI 조회 경로는 백엔드와 무관하게 동일하다.

## 3. Execution — `apps/worker`, `apps/inference-server`

### Worker

Celery 기반. 두 종류의 태스크만 존재한다 (`sceneops_worker/tasks/`):

```text
run_job_task(job_id)               → JobRunner.run(job_id)
run_pipeline_task(pipeline_run_id) → PipelineRunner.run(pipeline_run_id)
```

- `JobRunner` (`sceneops_worker/jobs/runner.py`): 단일 Job을 claim → 시작 → handler 실행
  → 완료 기록. Handler는 `JobHandlerRegistry`에 `JobType`별로 등록되어 있다
  (INGEST_SCENES, BUILD_SCENES, VALIDATE_SCENE, PROFILE_SCENE, REGISTER_SCENE,
  MINE_SCENARIOS, SCORE_SCENARIO_READINESS, PREDICT_DETECTION, EVALUATE_DETECTION 등,
  전체 목록은 [data-model.md](./data-model.md) 참고).
- `PipelineRunner` (`sceneops_worker/pipelines/runner.py`): `PipelineDefinition`에 정의된
  task 순서대로 **순차 실행**하는 local/dev 오케스트레이터. 태스크 하나가 quality gate에
  의해 `BLOCKED`로 판정되면 파이프라인 전체를 `BLOCKED`로 종료한다.

Idempotency(`execution_key`)와 부분 재시도(이미 `SUCCEEDED`인 task는 재실행 시
스킵)는 이미 구현되어 있다 — 자세한 내용은
[pipeline-lifecycle.md](./pipeline-lifecycle.md) §6, 배경은
[ADR 004](./adr/004-airflow-vs-celery.md) 참고.

### Airflow (Pipeline 전용, PoC)

`pipeline_backend=airflow`일 때는 `PipelineRunner.run()`이 한 프로세스 안에서
전체 파이프라인을 순차 실행하는 대신, Airflow DAG(`airflow/dags/sceneops_pipeline_run.py`)의
task마다 `DockerOperator`가 기존 worker 이미지(`apps/worker/Dockerfile`)로
`sceneops-worker` CLI를 한 번씩 별도 컨테이너로 실행한다:

```text
start → ingest_scenes → register_scene → validate_scene
      → profile_scene → build_scene_index → build_dataset_manifest → finalize
```

각 pipeline task는 `sceneops-worker run-pipeline-task --task-id <id>`로 실행되고,
이는 `PipelineTaskRunner.run()`을 직접 호출한다 — quality gate 평가, task별 상태
기록은 Celery 경로와 완전히 동일한 코드다. 다만 `PipelineRunner.run()`의 순차
루프 안에서만 일어나던 **pipeline 레벨** 상태 전이(`RUNNING`/`SUCCEEDED`/
`BLOCKED`/`FAILED`)는 개별 task 호출들 사이에 없기 때문에, `PipelineRunner`에
새로 추가된 `start()`/`finalize()`가 그 역할을 대신한다:

```text
start()     : 실행 가능 여부 검증 후 status=RUNNING
finalize()  : 저장된 task run 상태들을 조회해 BLOCKED > FAILED > SUCCEEDED
              순으로 최종 status 확정 (trigger_rule=all_done으로 항상 실행)
```

`start`/`finalize`는 새 상태 전이 로직이 아니라 `run()`이 이미 쓰던
private 메서드(`_start_pipeline`/`_succeed_pipeline`/`_block_pipeline`/
`_fail_pipeline`, 그리고 공유 헬퍼 `build_pipeline_result_from_task_runs`)를
그대로 재사용한다 — 로직 중복이 아니라 다른 진입점에서 재조합한 것.

현재 PoC는 `dataset_scene_ingestion` 파이프라인 하나에만 하드코딩되어 있고
(DAG의 task 체인이 고정), Job은 Airflow로 보내지 않는다 (`job_backend`는
Celery 고정 — 로드맵/ADR 004가 원래 의도한 "Airflow=스케줄된 장시간
워크플로, Celery=대화형 단발 작업" 구분과 일치).

### Inference Server

`apps/inference-server`는 별도 FastAPI 프로세스로, GroundingDINO(torch/transformers)
추론만 담당한다. Worker의 `PREDICT_DETECTION` job이 HTTP로 호출한다. 무거운 ML 의존성을
worker 프로세스에서 분리하기 위한 구조.

## 4. Storage

```text
                 SceneOps
                    │
      ┌─────────────┴─────────────┐
      ▼                           ▼
 PostgreSQL                 ArtifactStore
 (sceneops-db)              (sceneops-storage)

 operational / relational   binary / JSON artifacts
 - datasets, scenes         - scene manifests
 - pipeline/job runs        - dataset manifests
 - prediction/eval runs     - prediction/evaluation outputs
 - artifact metadata        - validation/profile reports
```

- PostgreSQL: 모든 엔티티의 상태·메타데이터·통계(count, status, uri 참조)를 담는
  system of record. 실제 페이로드(매니페스트, 리포트, 예측 결과)는 저장하지 않고
  ArtifactStore URI만 참조한다 (`*_uri` 컬럼들).
- ArtifactStore: `LocalArtifactStore` / `S3ArtifactStore`(MinIO 포함) 두 구현체가 있고
  `create_artifact_store(settings)` 팩토리가 `ArtifactBackend` 설정값으로 선택한다.
  자세한 레이아웃과 인터페이스는 [storage-layout.md](./storage-layout.md) 참고.

Analytical layer(Parquet/DuckDB/Polars)는 아직 없다 — 로드맵 Phase 1의 미완료 항목.

## 5. 로드맵과의 관계

이 문서가 기술하는 구조는 로드맵의 "Current Architecture"(섹션 2)에 대응하되, 실제
구현은 로드맵이 스케치한 것보다 세분화되어 있다 (예: `operations`/`leaderboards`
같은 view 레이어, run record를 타입별로 통합한 `*_run_records` 테이블 패턴 등).
로드맵 섹션 3의 "Target Architecture"(Airflow/Kafka/Spark/dbt/Kubernetes)는 아직
어떤 구성요소도 도입되지 않았다.
