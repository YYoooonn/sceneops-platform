# SceneOps 프로젝트 소개

## 1. SceneOps란

SceneOps는 **local-first 로보틱스 데이터 / MLOps 플랫폼**입니다.

로봇(또는 외부 데이터셋)의 센서 데이터가 **불변 원본 녹화**로 들어오고, 이것이 **source-faithful한 Scene / Episode**로 정규화되며, 그 위에서 라벨 · 샘플 뷰 · ScenarioSet · 추론 · 평가 · 학습 데이터 export 같은 **파생 워크플로**가 실행됩니다. 모든 파생 결과는 자신이 소비한 입력의 정확한 revision을 pin하므로, 어떤 결과든 "무엇으로부터 만들어졌는지"를 재현하고 검증할 수 있습니다.

nuScenes mini를 현실적인 fixture로 사용하며, FastAPI 컨트롤 플레인 · PostgreSQL · 오브젝트 스토리지 · Celery 잡 실행 · Kafka 스트리밍 수집을 production 형태로 구현했습니다. 목표는 처리량이 아니라 **아키텍처와 정합성 검증**입니다.

---

## 2. 핵심 설계 원칙

| 원칙 | 내용 |
| --- | --- |
| 플랫폼 primitive는 범용, 도메인 의미는 명시적 | Job / Pipeline / ArtifactStore 같은 인프라는 공유하되 `Scene` · `Episode` · `RobotRun`을 하나의 "DataUnit"으로 뭉개지 않는다. 구체적인 두 번째 사용 사례가 생기기 전에는 추상화하지 않는다. |
| 정본 → 파생 | 파생 산출물(manifest, index, profile, export, Parquet)은 정본과 내구성 있는 원본으로부터 재생산 가능해야 하며, 조용히 identity가 되어서는 안 된다. |
| 저장소별 책임 분리 | PostgreSQL = identity · 메타데이터 · 트랜잭션 상태 · lineage 참조 / 오브젝트 스토리지 = 물리 아티팩트 / Parquet = 분석용 표현 / Redis = 일시적 큐 / Kafka = 전송과 bounded replay (정본 아님) |
| 실패 의미론도 설계의 일부 | retry, 중복 실행, partial write, crash, 동시성, 순서, 멱등성, 복구를 happy path 이후가 아니라 설계 단계에서 다룬다. |
| 상태 먼저, 메시지 나중 | 상태를 먼저 커밋하고 그다음 메시지를 보낸다. 메시지는 "상태가 준비됐다"는 알림일 뿐이다. |
| 같은 재시도는 수렴, 충돌하는 재시도는 즉시 실패 | 결정적 identity와 내용 검증으로 멱등성을 얻는다. |
| 측정 후 인프라 추가 | Spark / Flink / Kubernetes 등은 실제 병목을 측정한 뒤에만 도입한다. |

---

## 3. 전체 구조

### 3.1 데이터 계층

```text
L0  transport        Kafka → capture → MCAP + receipt     정본 아님, bounded replay
L1  RobotRun         불변 녹화(MCAP) + manifest           등록·검증됨
L2  Scene / Episode  정본, source-faithful                checksum-pinned manifest
L3  derived          labels, sample views, ScenarioSets, predictions, evaluations,
                     aligned episodes, learning exports   입력을 pin하는 불변 revision
```

```text
Acquisition → RobotRun ─┬→ Scene   → Labels / SampleView / ScenarioSet → Inference / Evaluation
                        └→ Episode → AlignedEpisode → LearningDataExport
```

**RobotRun이 유일한 ingestion 경계**입니다. 배치 import로 들어왔는지 스트리밍으로 들어왔는지는 RobotRun 아래쪽에서 구분되지 않습니다.

### 3.2 코드 구조

```text
apps/          실행 프로세스      api, worker, capture, publisher
packages/      재사용 프로덕션 코드  core, db, storage, streaming, recording, execution, acquisition,
                                  scenes, episodes, derived, inference, evaluation, analytics
integrations/  외부 세계 어댑터    ros2-kafka-bridge, groundingdino-server, dataset-acquisition, lerobot
tools/         개발·CI·벤치마크    checks, e2e, baselines, reference, benchmarks, dev
```

- 의존 방향은 `apps / integrations → packages` 한 방향입니다. packages는 apps · integrations · tools를 import하지 않고, apps끼리도 import하지 않으며, 이를 `make check-boundaries`가 강제합니다.
- 패키지는 capability 이름으로 짓습니다. `common` / `utils` / `shared` 패키지는 두지 않습니다.
- `sceneops-core`는 I/O가 없습니다. `ArtifactStore` Protocol과 스키마만 정의하고, DB와 스토리지 구현은 API와 worker가 주입합니다(port/adapter). 그래서 로컬 파일시스템과 MinIO/S3 전환에 호출부 변경이 필요 없습니다.

| 프로세스 | 역할 |
| --- | --- |
| `apps/api` | FastAPI 컨트롤 플레인. datasets, scenes, episodes, scenarios, jobs, pipelines, robots, artifacts 등의 REST |
| `apps/worker` | Celery worker. `PipelineOrchestrator`, `JobRunner`, 도메인 핸들러, 복구·acquisition용 CLI |
| `apps/capture` | Kafka → MCAP + capture receipt. run당 one-shot 프로세스 하나 |
| `apps/publisher` | finalized MCAP + receipt → published RobotRun 객체. DB에 접근하지 않음 |

---

## 4. 핵심 기능

### 4.1 로봇 데이터 수집

두 경로가 같은 RobotRun 경계에서 만납니다.

```text
Batch      MCAP → Publisher → published 객체 → registration / reconciliation → RobotRun

Streaming  (외부)    로봇 / 시뮬레이터 / replay → ROS 2 → ros2-kafka-bridge → Kafka
           (SceneOps) Kafka → Capture → finalized MCAP + receipt → Publisher → registration → RobotRun
```

- **SceneOps는 Kafka 토픽에서 시작**합니다. 로봇 쪽 producer와 ROS 2 bridge는 외부 연동이며, ROS 2는 여러 소스 중 하나일 뿐입니다.
- **Capture는 `robot_run_id`당 one-shot 프로세스**이고, 라이프사이클이 완결된 run은 `RUN_END`를 받은 뒤에만 finalize합니다. 멀티-run 라우터는 두지 않습니다.
- Capture와 Publisher의 핸드오프는 알림이 아니라 **내구성 있는 상태**(finalized MCAP + receipt)입니다. Capture가 죽어 있어도 Publisher가 발견해 발행할 수 있습니다. 발행과 등록은 복구 가능한 one-shot 명령입니다.
- 녹화 안의 시간 사실을 섞지 않습니다: 관측 시간은 payload 안에 원본 그대로, 전송 ingest 시간은 MCAP `publish_time`, 캡처 수신 시간은 `log_time`, 전송 sequence는 MCAP `sequence`.
- DDS가 작은 샘플 뒤에 붙이는 0~3바이트 정렬 padding은 증명 가능한 경우에만 제거해, 배치와 스트림으로 들어온 같은 메시지의 바이트가 동일하게 유지됩니다.

### 4.2 Scene / Episode 정규화

- **Scene**: 채널별 관측 전부를 각자의 clock 그대로 보관하고 calibration과 pose를 더한 정본 단위입니다. 라벨, annotation, 동기화를 포함하지 않습니다.
- **Episode**: 같은 RobotRun의 task 지향 projection입니다. observation / state / action / event 스트림을 비동기 그대로 유지하며, 시간 정렬은 별도의 파생 단계입니다.
- 두 도메인은 **형제**입니다. Jobs / Pipelines / ArtifactStore 인프라는 공유하지만 모델은 공유하지 않습니다.

### 4.3 파생 레이어

| 객체 | 설명 |
| --- | --- |
| LabelSet | 관측에 anchor된 사후 라벨과 어노테이션한 coverage. Scene에 쓰이지 않고 별도 revision으로 import됨 |
| SceneSampleView | 정책 기반 동기화. 기준 채널, 연관 채널(nearest / previous + tolerance), ego pose, 라벨 revision을 지정. clock을 넘어 연관하거나 보간하지 않음 |
| ScenarioSet | 명시적 기준으로 sample view를 선별한 불변 revision |
| Prediction / Evaluation | 소비한 prediction · label · view revision을 pin. 서로 다른 프레임의 예측과 라벨은 비교하지 않고 실패 |
| AlignedEpisode / LearningDataExport | 명시적 타임라인으로 정렬한 Episode revision과 샤딩된 Parquet export (LeRobot은 export의 다운스트림 출력) |
| DatasetVersion | 정본 membership과 scope. 채널 요구사항, 소스 위치, 자유 메타데이터를 갖지 않음 |

**Revision과 pin**: 파생 manifest의 identity는 canonical JSON의 checksum이며 `manifest-<hex>.json`으로 write-once 저장됩니다. 같은 작업을 다시 등록하면 no-op이고, 충돌하는 중복은 실패합니다. **"latest" 포인터는 존재하지 않으며**, 소비자는 항상 pin을 해석합니다.

### 4.4 Job / Pipeline 실행

Pipeline은 다단계 오케스트레이션 · retry · lineage가 정당화되는 곳에만 두며, 정확히 4개입니다.

| Pipeline | 단계 |
| --- | --- |
| `recording_scene_building` | build → register → validate → profile (Scene) |
| `recording_episode_building` | build → register → validate → profile (Episode) |
| `scene_ml_evaluation` | sample view 생성 → 시나리오 마이닝 → readiness 점수 → detection 예측 → 평가 |
| `episode_learning_data_building` | align → learning data export |

단일 작업은 원자적 Job(`POST /jobs`)이고, pipeline의 stage인 Job은 원자적 Job과 **같은 핸들러**를 씁니다. 새 경로를 병렬로 만들지 않고 검증된 경로를 재사용합니다.

```text
PipelineRun → PipelineTaskRun → durable Job → 비동기 dispatch → Celery worker → JobRunner → domain handler
```

- Celery + Redis가 유일한 실행 백엔드이고, Celery 태스크는 `run_job_task`와 `advance_pipeline_task` 두 개뿐입니다.
- `PipelineOrchestrator`는 핸들러를 직접 실행하지 않고 durable 상태 위에서 **짧고 반복 가능한 한 걸음**만 진행합니다. Job 실행의 유일한 진입점은 `JobRunner`입니다.
- 실행 identity는 `execution_key`입니다. 멱등 생성, key당 in-flight Job 최대 1개이며, `force`도 성공한 레코드를 재사용하지 않습니다. 재dispatch는 첫 실패/블록 task부터 재개하고, quality gate로 pipeline을 막을 수 있습니다.

### 4.5 품질과 준비도

Dataset · Scene · Episode 단위로 validation / profile 요약과 readiness를 계산해 API로 노출합니다. Scene 품질 목록은 readiness 버킷, GT 유무, 선택 가능성(selectability) 집계를 제공합니다.

### 4.6 API

`/api/v1` 아래에 데이터 카탈로그(Datasets, Scenes, Episodes, Scenarios, Models), 로봇(Robots, RobotRuns, Missions, RobotStates), 실행(Pipelines, Jobs, Executions), 실행 결과(Inference, Evaluations, Artifacts), 운영(Operations, Leaderboards)이 있습니다. 웹 UI는 없습니다.

---

## 5. 신뢰성 설계

이 프로젝트에서 가장 많은 설계와 실험이 들어간 부분입니다.

### 5.1 API 트랜잭션 경계

- 변경 요청 하나는 PostgreSQL 트랜잭션 하나이고, **2xx 응답은 커밋이 끝났다는 뜻**입니다. 응답 전에 커밋하고 예외 시 롤백하며, 서비스와 리포지토리는 flush만 합니다.
- 외부 시스템에 닿는 작업은 **commit → dispatch** 순서입니다. `QUEUED`를 먼저 커밋한 뒤 Celery로 보내므로, worker가 아직 커밋되지 않은 행의 참조를 받지 않고, 늦은 커밋이 이미 진행된 상태를 덮어쓰지도 않습니다.

### 5.2 Job 소유권: lease + fencing (ADR-010)

- **문제**: worker가 죽으면 Job이 영원히 `RUNNING`으로 남고, Celery가 메시지를 재전달해도 이미 `RUNNING`이라 거부됩니다. 재전달은 같은 task id를 반복하므로 `worker_id`로는 소유권을 증명할 수 없고, 일시 정지됐다 돌아온 원래 owner가 새 claim의 상태를 덮어쓰는 경우를 실제 Celery worker로 재현했습니다.
- **해법**: claim마다 증가하는 `lease_generation`을 fencing token으로 쓰고, 모든 쓰기를 `status = RUNNING AND lease_generation = <내 claim>` 조건부로 합니다. claim은 PostgreSQL 시계 기준의 `lease_expires_at`를 가지며 worker가 별도 thread에서 갱신합니다.
- **복구**: stateless 복구 명령이 만료된 lease의 Job을 `QUEUED`로 되돌려 재전송하고, claim 예산(3회)을 넘으면 `JobLeaseExpired`로 실패 처리합니다.
- key당 in-flight Job 1개는 애플리케이션 체크가 아니라 PostgreSQL 제약으로 강제합니다.

### 5.3 유실된 메시지는 상태에서 재도출 (ADR-011)

PostgreSQL과 Redis는 트랜잭션을 공유하지 않으므로 "커밋은 됐는데 send가 실패하거나 유실"되는 경우가 생깁니다. 메시지는 id만 담은 알림이므로, `QUEUED` Job이나 진행 대기 중인 PipelineRun을 **durable 상태에서 다시 계산해 재전송**합니다. 모든 소비자가 멱등(atomic claim, compare-and-set, generation fencing, row-lock된 orchestration step)이어서 중복 전송이 안전합니다.

### 5.4 실행 상태는 durable 상태에서 도출 (ADR-012)

`enqueued_at`과 실행 메트릭으로 큐 대기 시간을 재고, 실행 상태 리포트는 별도 저장소 없이 durable 상태에서 계산합니다.

### 5.5 스트리밍 수집 (ADR-008, 013, 014)

| 문제 | 설계 |
| --- | --- |
| run 경계가 불분명 | `RUN_START` / `RUN_END` 제어 이벤트가 run을 감싼다. `RUN_END`는 `telemetry_count`를 선언하고 Capture가 검증하며, 맞지 않으면 `RunIncompleteError` |
| 메시지 유실이 조용히 묻힘 | bridge가 모든 수신 메시지에 sequence를 먼저 부여한다. 드롭하면 gap이 생기고 Capture가 run을 거부 |
| producer 중복·재정렬 | 멱등 producer(`acks=all`). 전달 실패가 있었다면 `RUN_END`를 보류해 불완전한 run이 완결된 것처럼 보이지 않게 함 |
| producer 큐 포화 | 전달 보고를 먼저 처리하고 제한된 시간만 대기한 뒤 `ProducerQueueFullError`. 포화된 큐가 영구히 막히던 버그를 수정 |
| Capture 재시도의 시작점 | bridge가 `RUN_START` ack 후 Kafka position을 출력하고, Capture가 run-pinned `assign()`으로 그 위치부터 읽는다. run별 flock으로 같은 run의 동시 캡처를 막음 |
| consumer group 부작용 | Capture는 assign-only이며 commit도 group도 없다. 정본은 Kafka가 아니라 finalized bag + receipt |
| replay window | 시간 retention이 보장 기간을 정의한다(최장 run 길이 + 장애 감지 + 재시도 + 읽기 시간으로 도출). 크기 제한은 호스트 보호용 backstop. 만료된 시작점은 즉시 명시적으로 실패 |
| DDS 히스토리 드롭 | keep-last는 가득 차면 신호 없이 오래된 샘플을 버리므로 기본을 keep-all로. depth는 명시적 선택일 때만 |

### 5.6 아티팩트 정합성

- 파생 아티팩트는 **write-once + checksum-qualified key**로 저장합니다. 같은 재시도는 수렴하고, 내용이 바뀌면 새 revision이 됩니다.
- 스토리지는 성공했는데 DB 커밋이 실패하는 경우, 같은 요청이 두 번 실행되는 경우, 이미 finalize된 출력과 내용이 다른 경우를 각각 명시적으로 처리합니다.
- 플랫폼은 아티팩트를 삭제하지 않습니다.

---

## 6. 검증 체계

인프라 의미가 계약인 부분(Kafka offset / partition, PostgreSQL lock / 제약, MinIO 객체 의미, DDS, Docker 이미지)은 mock이 아니라 **실제 인프라**로 증명합니다.

| 명령 | 증명하는 것 |
| --- | --- |
| `make test` | 의존 방향 체크 + 모든 app / package / tool 단위 테스트 (인프라 불필요) |
| `make test-integration` | 실제 Postgres + MinIO를 **일회용 DB / 버킷**에서 사용. skip은 실패 처리 |
| `make test-infrastructure SUITE=…` | pipeline 계약(일회용 실행 런타임), 장애 주입 복구와 capture → RobotRun 수명주기, Kafka, 외부 연동 경계 |
| `make reference-contract-verify` | **골든 reference contract**: 10 fixture × (Recording Import, Streaming Acquisition) = 20 RobotRun / 20 Scene / 20 Episode. 읽기 전용 |
| `make e2e-streaming-equivalence` | 같은 fixture를 배치와 스트림으로 받은 두 RobotRun이 acquisition · Scene · Episode 수준에서 의미적으로 동등함을 증명 (음성 대조군 포함, 상태를 만들지 않음) |
| `make e2e-scene-ml` | Scene → 라벨 → 뷰 → ScenarioSet → 예측 → 평가 |
| `make e2e-episode-learning` | Episode → 정렬 → 학습 export → 검증 + LeRobot round trip |
| `make e2e-cleanroom` | **재구성의 acceptance**. 생성된 런타임 상태를 지우고 보존된 reference 입력만으로 골든 contract를 다시 구축 (destructive) |

특징적인 점:

- **잠긴 reference corpus**: 소스 데이터는 보존하고 생성 상태는 언제든 reset합니다. 스트리밍 equivalence는 같은 잠긴 MCAP을 두 경로로 넣기 때문에, "변환 두 번"이 아니라 전송 계층 자체의 동등성을 말해 줍니다.
- 버그마다 **실제 실패 메커니즘을 재현하는 회귀 테스트**를 둡니다(동시 트랜잭션 race, offset 조기 ack, 충돌 중복, stale Docker 이미지 등).
- 설계 결정 전에 동시성, worker liveness, lost dispatch, Kafka retention / timestamp / backpressure / producer overload를 **실측 연구**로 확인하고 그 결과를 보존합니다.

---

## 7. 주요 아키텍처 결정

| ADR | 결정 |
| --- | --- |
| 001 | Operational metadata의 system of record는 PostgreSQL |
| 002 | 산출물은 ArtifactStore 추상화(local / MinIO / S3)로 저장 |
| 003 | Batch-first. 스트리밍은 필요가 확인된 뒤 추가 |
| 004 | Airflow vs Celery → superseded (Celery가 유일한 실행 백엔드) |
| 005 | ROS 2는 로봇 런타임 통신, Kafka는 데이터 플랫폼 이벤트 스트림 |
| 006 | 분석 데이터는 Parquet + PyArrow / Polars / DuckDB |
| 007 | Canonical ingestion architecture (RobotRun / Scene / Episode / 파생 레이어) |
| 008 | Acquisition lifecycle reliability |
| 009 | Job 중심 실행과 durable 경계 |
| 010 | Job ownership lease와 fencing |
| 011 | 유실된 메시지의 상태 기반 재dispatch |
| 012 | 실행 상태는 durable 상태에서 도출 |
| 013 | Capture는 Kafka에 진행 상태를 기록하지 않는다 |
| 014 | Kafka replay window는 시간 retention으로 정의 (Proposed) |

---

## 8. 설계가 진화해 온 방향

1. **도메인 ingestion 재설계**: 배치 중심 모델에서 `RobotRun` 단일 경계와 Scene / Episode 형제 도메인으로 전환. 하위 호환보다 깨끗한 최종 구조를 택했습니다.
2. **파생 레이어 불변화**: 라벨 · 샘플 뷰 · ScenarioSet · 평가를 pin 기반 immutable revision과 write-once 아티팩트로 정리했습니다.
3. **Reference corpus / golden contract**: 임의 fixture 대신 잠긴 10개 장면 코퍼스와 20 RobotRun 계약을 도입하고, 테스트를 일회용 DB / 버킷으로 분리했습니다.
4. **실행 모델 단일화**: Job이 유일한 실행 단위, Celery가 유일한 백엔드. 그 위에 lease / fencing, 상태 기반 재dispatch, 실행 상태 도출을 차례로 얹었습니다.
5. **스트리밍 수집 단순화와 경화**: 멀티-run 라우터를 걷어내고 run당 one-shot Capture로 단순화한 뒤, RUN_START / RUN_END 계약, 멱등 producer, 큐 포화 복구, assign-only 캡처, replay window 정의를 추가했습니다.
6. **저장소 토폴로지 정리**: `apps / packages / integrations / tools` 구조와 import 경계 checker를 확정했습니다. 이동 시 호환 shim을 남기지 않습니다.

---

## 9. 현재 한계

- 기본 fixture는 nuScenes mini이고, journey는 한 장면(`scene-0061`) 기준으로 검증되었습니다. 스트리밍 측정(8,897 메시지, 약 356 MB, 20 채널, Apple-silicon Docker Desktop 단일 호스트)은 그 워크로드에 대한 측정일 뿐 지원 처리량의 한계가 아닙니다.
- local-first이며 대규모 처리량이 아니라 아키텍처 검증에 맞춰져 있습니다.
- GroundingDINO 결과는 통합 신호이지 모델 벤치마크가 아니고, 기본 Scene ML journey는 mock backend를 씁니다.
- Sample view는 nearest / previous 연관만 지원하고 pose 보간이 없으며, 평가는 예측과 라벨 사이에 프레임 변환을 적용하지 않습니다.
- Episode에는 라벨 세트가 없고, 학습 export는 숫자 scalar / vector만 지원합니다.
- 스트리밍 캡처는 복구 가능한 one-shot 명령을 거쳐 RobotRun이 되며, Capture를 감독하는 주체와 실시간 로봇 제어는 없습니다.
- worker나 메시지 유실 복구는 복구 루프가 도는 동안에만 일어나고, pipeline task는 엄격히 직렬로 실행됩니다.
- Kafka 메시지 크기는 stock 제한(약 1 MB)이며, 그보다 큰 센서 payload는 producer / topic / broker 설정을 함께 올려야 합니다.
- 웹 UI가 없습니다.

---

## 10. 기술 스택과 시작하기

| 영역 | 사용 기술 |
| --- | --- |
| API | FastAPI, Pydantic v2, Uvicorn |
| 실행 | Celery, Redis 7 |
| DB | PostgreSQL 16, SQLAlchemy 2.0 async, Alembic |
| 아티팩트 | MinIO (S3 API), boto3 |
| 분석 | Polars, PyArrow, DuckDB |
| 추론 (선택) | GroundingDINO, HuggingFace Transformers |
| 로봇 데이터 | ROS 2 Jazzy, MCAP, Kafka |
| 도구 | uv workspace, Ruff, pre-commit, Docker Compose |

```bash
cp .env.example .env.local
make setup                          # 의존성 + pre-commit
make local-up                       # Postgres + Redis + MinIO + migrate + API + workers
make test                           # 단위 테스트 (인프라 불필요)
make reference-data-bootstrap       # nuScenes mini → 잠긴 MCAP (최초 1회)
make reference-contract-bootstrap   # 골든 contract (20 RobotRun) 구성
make e2e-scene-ml                   # Scene ML journey
make e2e-episode-learning           # Episode learning journey
```
