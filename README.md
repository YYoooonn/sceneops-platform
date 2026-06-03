# SceneOps Platform

자율주행·로보틱스 센서 데이터의 수집·검증·프로파일링부터 모델 추론·평가·자동 레이블링까지를 하나의 파이프라인으로 관리하는 MLOps 플랫폼

---

## 목차

- [핵심 기능](#핵심-기능)
- [시스템 아키텍처](#시스템-아키텍처)
- [데이터 파이프라인 흐름](#데이터-파이프라인-흐름)
- [프로젝트 구조](#프로젝트-구조)
- [인프라 구성](#인프라-구성)
- [빠른 시작](#빠른-시작)
- [주요 API](#주요-api)

---

## 핵심 기능

| 기능 | 설명 |
|------|------|
| **데이터 파이프라인** | Ingest → Validate → Profile 자동화, 품질 게이트로 불량 데이터 차단 |
| **GPU 추론 서버** | GroundingDINO 전용 FastAPI 서버, GPU/CPU 분리 빌드 지원 |
| **모델 추론** | Mock / ONNX Runtime / GroundingDINO 백엔드 추상화, 배치 추론 지원 |
| **평가 & 리더보드** | Detection 메트릭(TP/FP/FN, precision, recall, mAP) 자동 계산, 모델 비교 |
| **자동 레이블링** | GroundingDINO 기반 Auto-label 파이프라인, 레이블 품질 추적 |
| **스토리지 추상화** | 로컬 파일시스템 ↔ S3/MinIO 무중단 전환 |
| **분산 작업 처리** | Celery 기반 파이프라인/잡 워커 분리, 비동기 실행 |

---

## 시스템 아키텍처

```
┌──────────────────────────────────────────────────────────────────┐
│                        Client / Web Dashboard                    │
└────────────────────────────┬─────────────────────────────────────┘
                             │ HTTP REST API
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│                    API Server  (FastAPI · port 8000)             │
│                                                                  │
│  /datasets   /models   /pipelines   /jobs   /runs                │
│  /evaluations   /leaderboards   /operations   /artifacts         │
│                                                                  │
│                    Celery Dispatcher  ──────► Redis Queue        │
└────────────────────────────┬─────────────────────────────────────┘
                             │ Celery Task
              ┌──────────────┴──────────────┐
              ▼                             ▼
┌─────────────────────┐       ┌─────────────────────────────────┐
│  worker-pipeline    │       │  worker-jobs  (concurrency=4)   │
│  (concurrency=1)    │       │                                 │
│                     │       │  ingest · validate · profile    │
│  PipelineRunner     │  ──►  │  predict · evaluate             │
│  StepExecutor       │       │  auto_label ────────────────┐   │
│  QualityGate        │       │                             │   │
└─────────────────────┘       └─────────────────────────────┼───┘
              │                                             │ HTTP
              └──────────┬──────────────────────────────────┘
                         │ Read / Write          ▼
            ┌────────────┴────────────┐  ┌─────────────────────────┐
            ▼                         ▼  │  Inference Server       │
     ┌─────────────┐         ┌──────────┐│  (FastAPI · port 8001)  │
     │ PostgreSQL  │         │ Artifact ││                         │
     │             │         │  Store   ││  GroundingDINO-T        │
     │  metadata   │         │          ││  HuggingFace Transformers│
     │  pipeline   │         │  local FS││                         │
     │  runs evt   │         │    or    ││  GPU (cu121)            │
     └─────────────┘         │  MinIO   ││  or CPU (local dev)     │
                             └──────────┘└─────────────────────────┘
```

### 레이어 분리 원칙

| 레이어 | 책임 |
|--------|------|
| **API** | 요청 수신, 유효성 검사, Celery 작업 디스패치 |
| **Worker** | 파이프라인/잡 실행, 상태 관리, 결과 저장 |
| **Inference Server** | GroundingDINO 모델 서빙, `/v1/detect` HTTP 엔드포인트 제공 |
| **sceneops-core** | 도메인 스키마, 계약(Protocol), 상수 |
| **sceneops-db** | PostgreSQL ORM, 비동기 레포지토리 |
| **sceneops-storage** | 아티팩트 I/O 추상화 (local ↔ S3) |

---

## 데이터 파이프라인 흐름

### 1. Dataset Ingestion Pipeline

```
Raw Data (nuScenes / Waymo / KITTI)
        │
        ▼
  [INGEST]  ─── 매니페스트 생성 (Scene / Sample / Annotation)
        │         결과 → ArtifactStore (JSON)
        ▼
  [VALIDATE] ── 채널 무결성 검사, 샘플 데이터 검증
        │         validation_status: ready / warning / failed
        │         should_block_pipeline → 품질 게이트
        ▼
  [PROFILE]  ── 센서 커버리지, 어노테이션 통계, LiDAR 채널 메트릭
                 sensor_coverage_ratio, empty_annotation_sample_ratio
```

### 2. Detection Validation Pipeline

```
Registered Dataset Version + Model Version
        │
        ▼
  [PREDICT]   ── 배치 추론 (Mock / ONNX Runtime / GroundingDINO)
        │          predictions_root_uri → ArtifactStore
        ▼
  [EVALUATE]  ── Detection 메트릭 계산
                  TP/FP/FN, precision, recall, mean_center_distance
                  class별 메트릭, 리더보드 반영
```

### 3. Auto-Label Pipeline

```
Raw Dataset + GroundingDINO Model
        │
        ▼
  [INGEST]     ── 원본 매니페스트 생성
        ▼
  [AUTO_LABEL] ── worker-jobs → HTTP → Inference Server (/v1/detect)
        │          GroundingDINO 추론 → 자동 레이블 생성
        │          labeled_sample_count, label quality metrics
        ▼
  [VALIDATE]   ── 레이블 품질 검증
```

### 품질 게이트 (PipelineQualityGate)

Validate 단계에서 `should_block_pipeline = true`이면 후속 단계가 자동으로 차단됩니다. 불량 데이터가 추론·평가 단계로 흘러가는 것을 방지합니다.

---

## 프로젝트 구조

```
sceneops-platform/
├── apps/
│   ├── api/                        # FastAPI 제어 평면
│   │   └── app/
│   │       ├── modules/
│   │       │   ├── datasets/       # 데이터셋 관리
│   │       │   ├── models/         # 모델 레지스트리
│   │       │   ├── pipelines/      # 파이프라인 생성·실행
│   │       │   ├── jobs/           # 개별 잡 생성·실행
│   │       │   ├── runs/           # 추론·평가·검증·프로파일 런 조회
│   │       │   ├── evaluations/    # 평가 비교, 히스토리
│   │       │   ├── leaderboards/   # 모델 리더보드
│   │       │   ├── operations/     # 작업 타임라인, 요약
│   │       │   └── artifacts/      # 아티팩트 직접 조회
│   │       └── core/               # DB 세션, 공통 의존성
│   │
│   ├── inference-server/           # GroundingDINO 추론 서버 (FastAPI · port 8001)
│   │   └── inference_server/
│   │       ├── main.py             # /healthz, /v1/detect 엔드포인트
│   │       ├── grounding_dino.py   # 모델 로드 및 추론 로직
│   │       ├── schemas.py          # DetectRequest / DetectResponse
│   │       └── config.py           # HF 캐시, 임계값 설정
│   │
│   └── worker/                     # Celery 실행 런타임
│       └── sceneops_worker/
│           ├── pipelines/          # PipelineRunner, StepExecutor, QualityGate
│           ├── jobs/
│           │   └── handlers/       # ingest / validate / profile / predict / evaluate / auto_label
│           ├── datasets/           # 인제스션 (nuScenes 등)
│           ├── inference/          # Mock / ONNX / GroundingDINO 백엔드
│           ├── evaluation/         # Detection 메트릭
│           ├── registry/           # RuntimeStoreRegistry
│           └── runs/               # 런 아티팩트 I/O
│
├── packages/
│   ├── sceneops-core/              # 도메인 계약, Pydantic 스키마, 상수
│   ├── sceneops-db/                # SQLAlchemy 모델, 비동기 레포지토리
│   └── sceneops-storage/           # LocalArtifactStore, S3ArtifactStore
│
├── migrations/                     # Alembic 마이그레이션
├── scripts/
│   ├── init/                       # MinIO 초기화, 버킷 생성 및 데이터 마이그레이션
│   ├── checks/                     # 환경·Celery·MinIO 헬스체크
│   ├── e2e/                        # E2E 테스트 스크립트
│   └── fixtures/                   # nuScenes 데이터셋 등록 픽스처
│
├── docker-compose.local.yml
├── Makefile
└── pyproject.toml                  # uv 워크스페이스
```

---

## 인프라 구성

| 서비스 | 이미지 | 포트 | 역할 |
|--------|--------|------|------|
| `postgres` | postgres:16 | 5432 | 메타데이터 저장 (데이터셋, 모델, 파이프라인, 런) |
| `redis` | redis:7 | 6379 | Celery 브로커 및 결과 백엔드 |
| `api` | 로컬 빌드 | 8000 | FastAPI 제어 평면 |
| `worker-pipeline` | 로컬 빌드 | - | 파이프라인 오케스트레이션 (concurrency=1) |
| `worker-jobs` | 로컬 빌드 | - | 개별 잡 실행 (concurrency=4) |
| `inference-server` | 로컬 빌드 | 8001 | GroundingDINO 추론 (GPU, `--profile gpu`) |
| `inference-server-local` | 로컬 빌드 | 8001 | GroundingDINO 추론 (CPU, `--profile inference`) |
| `minio` | minio/minio | 9000/9001 | S3 호환 오브젝트 스토리지 (`--profile minio`) |
| `minio-init` | minio/mc | - | 버킷 생성 + 로컬 데이터 자동 마이그레이션 |

### Docker Compose 프로필

| 프로필 | 포함 서비스 | 용도 |
|--------|-------------|------|
| _(기본)_ | postgres, redis, api, worker-pipeline, worker-jobs | 일반 개발 |
| `inference` | inference-server-local | CPU 환경 로컬 추론 |
| `gpu` | inference-server | NVIDIA GPU 환경 추론 |
| `minio` | minio, minio-init | S3 호환 스토리지 |
| `tools` | migrate | DB 마이그레이션 |
| `debug` | worker-cli | Worker CLI 디버깅 |

### Inference Server 빌드 구분

Dockerfile은 `TORCH_FLAVOR` build arg로 GPU/CPU 빌드를 분리합니다.

| 빌드 | TORCH_FLAVOR | 대상 |
|------|-------------|------|
| `inference-server` | `cu121` | NVIDIA GPU 서버 |
| `inference-server-local` | `cpu` (기본값) | CPU / 로컬 개발 |

### Celery 큐 전략

```
sceneops.pipeline_runs  (concurrency=1)
  → 파이프라인 순서 보장, 단계 간 context 전파

sceneops.jobs           (concurrency=4)
  → 개별 잡 병렬 처리, 빠른 스루풋
```

### 스토리지 백엔드 전환

`.env` 한 줄 변경으로 로컬 ↔ MinIO/S3 전환:

```bash
# 로컬 개발
SCENEOPS_WORKER_ARTIFACT__BACKEND=local
SCENEOPS_WORKER_ARTIFACT__ROOT_URI=/data

# MinIO / S3
SCENEOPS_WORKER_ARTIFACT__BACKEND=minio
SCENEOPS_WORKER_ARTIFACT__ROOT_URI=s3://sceneops
SCENEOPS_WORKER_ARTIFACT__ENDPOINT_URL=http://minio:9000
```

---

## 빠른 시작

```bash
# 1. 환경 변수 설정
cp .env.example .env.local

# 2. 의존성 설치
make uv-sync

# 3. DB 실행 및 마이그레이션
docker compose -f docker-compose.local.yml up -d postgres redis
make db-migrate

# 4. API + Worker 실행
make compose-up

# 5. (선택) MinIO 오브젝트 스토리지 실행
make minio-up   # 로컬 data/ 자동 마이그레이션 포함

# 6. nuScenes 데이터셋 등록
make register-nuscenes-dataset
```

### Inference Server (Auto-Label / GroundingDINO)

Auto-Label 파이프라인 실행 시 inference server가 필요합니다. 로컬 환경(CPU)과 GPU 환경을 분리해서 실행

```bash
# 로컬 개발 (CPU, Apple Silicon 포함)
make inference-server-local        # 빌드 + 실행
make inference-server-local-logs   # 로그 확인
make inference-server-local-down   # 종료

# GPU 서버 (NVIDIA CUDA 12.1)
make inference-server-up           # 빌드 + 실행
make inference-server-logs         # 로그 확인
make inference-server-down         # 종료

# 헬스체크
make check-inference-server        # {"status": "ok", "model_loaded": true, ...}
```

> **주의**: worker 컨테이너에서 inference server 접근 시 `localhost`가 아닌
> 컨테이너 서비스명(`http://sceneops-inference-server-local:8001`)을 사용
> model registry의 `endpoint_url` 또는 job params에 서비스명으로 등록 필수

### E2E 테스트

```bash
make e2e-mock-celery      # Mock 추론 파이프라인 전체 실행
make e2e-onnx-celery      # ONNX 추론 파이프라인 전체 실행
make e2e-autolabel        # GroundingDINO Auto-Label 파이프라인 전체 실행
```

---

## 주요 API

| 메서드 | 경로 | 설명 |
|--------|------|------|
| `POST` | `/api/v1/pipelines/runs` | 파이프라인 런 생성 |
| `POST` | `/api/v1/pipelines/runs/{id}/execute` | 파이프라인 실행 (Celery 디스패치) |
| `GET`  | `/api/v1/pipelines/runs/{id}` | 파이프라인 상태 조회 |
| `POST` | `/api/v1/jobs/{id}/execute` | 개별 잡 실행 |
| `GET`  | `/api/v1/runs/profiles` | 데이터셋 프로파일 런 목록 |
| `GET`  | `/api/v1/runs/validations/{id}` | 검증 결과 상세 |
| `GET`  | `/api/v1/runs/evaluations/{id}` | 평가 결과 상세 |
| `GET`  | `/api/v1/leaderboards/detection` | Detection 모델 리더보드 |
| `GET`  | `/api/v1/evaluations/compare` | 데이터셋 버전 간 평가 비교 |
| `GET`  | `/api/v1/operations/summary` | 전체 작업 현황 요약 |

---

## 기술 스택

| 분류 | 기술 |
|------|------|
| **API** | FastAPI, Pydantic v2, Uvicorn |
| **Task Queue** | Celery, Redis |
| **DB** | PostgreSQL 16, SQLAlchemy 2.0 (async), Alembic |
| **Storage** | MinIO (S3 API), boto3 |
| **Inference** | GroundingDINO (HuggingFace Transformers), ONNX Runtime |
| **Data** | nuScenes DevKit |
| **Infra** | Docker Compose, uv (workspace) |
| **Quality** | Ruff, pre-commit |
