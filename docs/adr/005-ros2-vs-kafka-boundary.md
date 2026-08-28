# ADR-005: ROS2는 robot runtime 통신, Kafka는 data platform 이벤트 스트림

## Status

Proposed — 아직 어느 쪽 코드도 구현되지 않았다 (ROS2: 로드맵 Phase 4, Kafka: Phase 7 둘 다 미착수).
이 ADR은 두 로드맵 항목이 착수되기 전에 경계를 먼저 명시해두기 위한 forward-looking 결정이며,
Phase 4/Phase 7 착수 시점에 실제 구현 경험을 반영해 재검토한다.

## Context

로드맵은 ROS2(Phase 4)와 Kafka(Phase 7)를 모두 "메시지를 주고받는 미들웨어"로 다루기 때문에,
설계 초기에 이 둘을 서로 대체 가능한 선택지로 오인하기 쉽다. 실제로는 계층이 다르다: ROS2는
로봇 위에서 노드 간 실시간 pub/sub·서비스·액션을 담당하는 로봇 런타임 통신 계층이고, Kafka는
데이터 플랫폼이 여러 로봇/서비스로부터 이벤트를 수집·영속화·재처리하기 위한 스트림이다. 이 경계를
문서화하지 않으면 "ROS2 토픽을 Kafka로 그대로 브릿지하면 되는가", "Kafka로 로봇을 직접 제어할
수 있는가" 같은 잘못된 설계 질문이 나올 수 있다.

## Decision

```text
Robot Runtime Communication → ROS2
Data Platform Event Stream   → Kafka
```

ROS2와 Kafka를 서로 대체하는 기술로 보지 않는다 (로드맵 §4.3). 경계는 물리적 로봇 경계에
둔다:

```text
Robot Runtime
────────────────
ROS2 Topics
     ↓
ROS2 Data Gateway   ← 이 경계에서 ROS2 → Kafka 변환이 일어난다 (Phase 7 설계)

Data Platform
────────────────
     ↓
Kafka
```

즉 로봇 내부(노드 간 odometry, IMU, 제어 명령 등)는 항상 ROS2로 통신하고, 그 데이터를 데이터
플랫폼으로 내보낼 때만 Data Gateway가 ROS2 토픽을 구독해 Kafka 토픽(`robot.telemetry.v1`,
`robot.mission.v1` 등)으로 발행한다. 대용량 바이너리(카메라/LiDAR/rosbag)는 Kafka에 직접
싣지 않고 Object Storage에 쓴 뒤 Kafka에는 메타데이터/URI/이벤트만 흘린다 (로드맵 §13).

## Consequences

- Phase 4(ROS2)는 Kafka 유무와 무관하게 독립적으로 진행할 수 있다 — CAN replay, rosbag2/MCAP
  기록, `RosbagAdapter`를 통한 SceneOps 등록까지는 순수 batch(rosbag 파일 → object storage →
  ingestion pipeline) 경로로 완결되며, [ADR-003](./003-batch-first-architecture.md)의 batch-first
  원칙과 일치한다.
- Data Gateway는 두 시스템의 프로토콜(ROS2 DDS ↔ Kafka)을 모두 이해해야 하는 유일한 컴포넌트가
  된다 — 이 경계를 넘는 코드가 늘어나면 Gateway를 별도 서비스로 분리해 책임을 명확히 유지한다.
- 이 결정은 아직 검증되지 않았다 — Phase 4에서 실제 ROS2 노드를 붙여보고, Phase 7에서 Gateway를
  구현하면서 partitioning(`robot_id` 기준), 순서 보장, 중복 전달 같은 실제 제약이 이 경계 설계와
  충돌하는지 재확인해야 한다.
