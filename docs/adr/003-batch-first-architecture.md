# ADR-003: Batch-first, Streaming은 필요가 확인된 이후에 추가한다

## Status

Accepted

## Context

로드맵(§4.2)은 "Reliable Batch → Distributed Batch → Streaming" 순서로 진화하는 것을 원칙으로
제시한다. SceneOps의 현재 워크로드(INGEST/VALIDATE/PROFILE/CURATE/PREDICT/EVALUATE)는 전부
`(dataset_id, dataset_version)` 단위로 한 번 실행되고 끝나는 batch 작업이고, 실시간으로 도착하는
이벤트 스트림을 처리해야 하는 요구사항은 아직 없다. 그럼에도 로드맵 후반부(Phase 7 Kafka,
Phase 8 Debezium)는 streaming 구성 요소를 목표로 명시하고 있어, 지금부터 두 방식을 혼합하면
"실시간이 필요하지도 않은데 Kafka부터 들여오는" 과잉 설계로 이어질 위험이 있다.

## Decision

기술을 먼저 넣지 않고 "문제 발생 → 현재 한계 → 기술 선택 → 측정" 순서를 따른다 (로드맵 §4.1).
구체적으로:

- 파이프라인 실행 단위는 항상 `(dataset_id, dataset_version)` 같은 명시적 scope를 갖는 1회성
  batch job이다 — 현재 `PipelineRunner`(순차 task 실행, [pipeline-lifecycle.md](../pipeline-lifecycle.md))와
  Airflow PoC(DAG 1회 트리거)가 이미 이 형태로 구현되어 있다.
- Kafka/Debezium 같은 streaming 구성 요소는 "실제 robot이 실시간으로 telemetry를 쏘기 시작하는
  시점"(로드맵 Phase 7)에만 도입한다. 그 전까지 batch 파이프라인의 신뢰성(retry/idempotency/
  partial failure — [ADR-004](./004-airflow-vs-celery.md))을 먼저 완성한다.
- ROS2(Phase 4)도 streaming 도입과 별개다 — robot runtime 통신 계층이지 데이터 플랫폼의 이벤트
  스트림이 아니다 ([ADR-005](./005-ros2-vs-kafka-boundary.md)).

## Consequences

- 로드맵 Phase 2(Airflow)까지는 "언제 실행되는가"를 스케줄러가 아니라 API dispatch가 결정한다 —
  cron 기반 스케줄, backfill 같은 시간 파티션 개념이 아직 없다 (실제로 로드맵 Phase 2 completion
  criteria에서 backfill을 "새 pipeline_run을 하나 더 dispatch하는 것과 동일하다"고 판단해 별도
  구현을 생략했다 — [pipeline-lifecycle.md](../pipeline-lifecycle.md) §6).
- 향후 Kafka를 도입할 때도 batch 파이프라인을 완전히 대체하는 게 아니라, robot telemetry처럼
  실시간성이 실제로 필요한 데이터 소스에만 부분 적용한다.
