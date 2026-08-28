# ADR-004: Airflow는 스케줄된 파이프라인, Celery는 대화형 단발 작업

## Status

Accepted

## Context

SceneOps는 원래 Celery 하나로 Job과 Pipeline을 모두 실행했다. 로드맵 Phase 2(Reliable Batch)는
production-oriented batch orchestration(retry/timeout/idempotency/partial retry/backfill)을
요구하는데, Celery의 `autoretry_for`는 태스크 전체 재시도만 지원하고 DAG 형태의 의존성·부분
재시작·운영 가시성(UI)은 제공하지 않는다. 반대로 API가 처리하는 단발성 대화형 요청(예: 사용자가
scene 하나를 재검증하는 것)까지 Airflow로 보내면 스케줄러 오버헤드와 지연이 불필요하게 커진다.

## Decision

역할을 명시적으로 분리한다 ([architecture.md](../architecture.md) §3):

```text
Airflow → scheduled / long-running workflow (Pipeline)
Celery  → interactive async application task (Job, 그리고 Pipeline의 기본 백엔드)
```

`ExecutionService`가 job/pipeline 각각에 대해 백엔드를 선택할 수 있는 구조로 설계되어 있고
(`get_pipeline_execution_backend`가 `settings.execution.pipeline_backend` 값으로
`CeleryPipelineExecutionBackend` 또는 `AirflowPipelineExecutionBackend`를 선택), 현재는:

```text
Job      → Celery만 지원 (job_backend는 celery 고정)
Pipeline → Celery 또는 Airflow (pipeline_backend로 전환 가능, PoC)
```

Airflow 경로에서는 `PipelineRunner.run()`이 한 프로세스에서 전체를 순차 실행하는 대신, DAG
(`airflow/dags/sceneops_pipeline_run.py`)의 task마다 `DockerOperator`가 기존 worker 이미지로
`sceneops-worker run-pipeline-task`를 별도 컨테이너로 실행한다. Task 실행과 quality gate 판정
로직(`PipelineTaskRunner.run()`)은 두 백엔드에서 완전히 동일한 코드를 쓰고, pipeline 레벨 상태
전이만 진입점이 다르다 — Celery는 순차 루프 안에서, Airflow는 DAG의 `start`/`finalize`
task(`trigger_rule=all_done`)에서 처리한다 ([pipeline-lifecycle.md](../pipeline-lifecycle.md) §8).

두 백엔드 모두 `ExecutionRecord`에 `execution_backend` 컬럼으로 구분되어 기록되므로, API/UI
조회 경로는 백엔드와 무관하게 동일하다 ([ADR-001](./001-postgresql-operational-metadata.md)).

Reliability 요구사항은 Airflow 도입 여부와 독립적으로, Celery 경로에도 이미 구현했다
([pipeline-lifecycle.md](../pipeline-lifecycle.md) §6):

- **Idempotency**: `execution_key = sha256(kind, type, dataset_id, dataset_version, model_id,
  model_version, params)` — 동일 키의 진행 중/완료 레코드가 있으면 재생성하지 않는다.
- **Partial retry**: `PipelineTaskRunner`가 이미 `SUCCEEDED`인 task를 재실행 없이 skip —
  재dispatch가 곧 부분 재시도다.
- **Retry (Job 단위)**: `jobs.retry_count`/`max_retries`를 `JobService.mark_queued`가 증가·검증.
- **Backfill**: 별도 구현 없음 — [ADR-003](./003-batch-first-architecture.md) 참고.

## Consequences

- 이 primitives(execution_key, 부분 재시도)는 Airflow로 완전히 옮겨가도 재사용된다 — Airflow의
  재시도도 결국 "이미 성공한 작업은 건너뛴다"는 동일한 idempotency 판단에 의존하기 때문이다.
- 현재 Airflow DAG는 `dataset_scene_ingestion` 파이프라인 하나에만 하드코딩되어 있다 (task
  체인이 고정) — 다른 pipeline type을 Airflow로 보내려면 DAG를 일반화하거나 타입별 DAG를 추가해야
  한다. 이 PoC 범위 밖.
- `JobDispatchFacade`가 강제하는 "DB에 QUEUED 커밋 → 백엔드 dispatch" 순서(worker의 late
  overwrite 방지)는 Airflow 경로에도 동일하게 적용해야 하는 제약으로 남는다
  ([pipeline-lifecycle.md](../pipeline-lifecycle.md) §7).
