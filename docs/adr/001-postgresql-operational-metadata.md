# ADR-001: Operational Metadata는 PostgreSQL을 system of record로 둔다

## Status

Accepted

## Context

SceneOps가 관리하는 엔티티(`Dataset`/`DatasetVersion`, `SceneRecord`, `ScenarioSet`,
`PipelineRun`/`PipelineTaskRun`, `Job`/`JobEvent`, `InferenceRun`, `EvaluationRun`, `Artifact`,
`ExecutionRecord`)는 대부분 상태(status)·통계(count)·참조(uri)로 구성되고, 조회 시 조인·필터·
트랜잭션 일관성이 필요하다. 반면 실제 페이로드(scene manifest, validation report, prediction
결과, rosbag/이미지 등 바이너리)는 크기가 크고 스키마가 가변적이다. 이 둘을 같은 저장소에
두면 관계형 쿼리 성능과 바이너리 저장 효율 중 하나를 포기해야 한다.

## Decision

PostgreSQL은 **모든 엔티티의 상태·메타데이터·통계**만 담는 system of record로 한정한다.
실제 페이로드는 저장하지 않고, `*_uri` 컬럼(예: `DatasetVersion.manifest_uri`,
`SceneRecord.scene_manifest_uri`, `InferenceRun.predictions_root_uri`)으로 ArtifactStore
상의 위치만 참조한다 ([architecture.md](../architecture.md) §4).

이 분리는 실행 계보(lineage) 추적에도 적용된다: `ExecutionRecord`는 Job/Pipeline이라는
"논리적 리소스"(PostgreSQL에 상태가 있음)와 Celery/Airflow라는 "물리적 실행"(외부 시스템의
task id)을 명시적으로 분리해서 기록한다 (`execution_backend`, `resource_id`, `external_id` —
[data-model.md](../data-model.md) §9). 실행 백엔드가 바뀌어도(Celery→Airflow) 논리적 리소스의
상태 조회 경로는 PostgreSQL 하나로 유지된다.

## Consequences

- API/뷰 레이어(`operations`, `leaderboards`)는 항상 PostgreSQL 하나만 조회하면 되고, 바이너리
  저장소 가용성과 무관하게 상태 조회가 가능하다.
- 모든 쓰기 경로가 "PostgreSQL 커밋 + ArtifactStore 쓰기"라는 두 시스템에 걸치므로, 두 쓰기
  사이의 원자성은 보장되지 않는다 — 현재는 ArtifactStore 쓰기 후 PostgreSQL에 URI를 커밋하는
  순서로 구현되어 있어, 커밋 실패 시 orphan 아티팩트가 남을 수 있다 (별도 정리 로직 없음, 아직
  실제 이슈로 발현되지는 않음).
- 새 엔티티를 추가할 때도 이 원칙을 따라야 한다 — 큰 payload를 컬럼에 직접 넣지 않고 반드시
  ArtifactStore URI로 참조한다.
