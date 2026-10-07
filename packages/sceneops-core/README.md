# sceneops-core

SceneOps 플랫폼의 핵심 도메인 라이브러리. 모든 다른 패키지와 앱이 의존하는 공유 타입, 계약(Protocol), 경로 유틸리티를 제공

런타임 I/O 없이 순수 Python 타입과 Pydantic 모델만으로 구성

## Structure

```
sceneops_core/
  artifacts/          ← ArtifactStore contract, ArtifactKind, ArtifactOwnerType, ArtifactRef
  common/             ← SceneOpsBaseModel, ID utilities, checksums, derived artifact ids
  config.py           ← runtime settings (ArtifactSettings, ExecutionSettings, ...)
  constants/          ← platform constants (task and queue names, streaming constants)
  datasets/           ← Dataset / DatasetVersion schemas
  episodes/           ← Episode domain (manifest, recording build config, alignment, curation, learning export)
  evaluations/        ← evaluation run schemas and contracts
  executions/         ← execution key, ExecutionBackend / ExecutionDispatchResult schemas (Celery)
  inference/          ← inference (detection) schemas
  integration_runtime/← IntegrationRequest / IntegrationResult, ExternalDatasetRef
  jobs/               ← Job domain (JobManifest, JobEvent, params, step registry)
  labels/             ← label set schemas
  models/             ← model registry schemas
  pipelines/          ← pipeline definitions (builtin.py), manifests, registry
  provenance/         ← source / producer provenance and identity
  robots/             ← Robot / RobotRun / Mission / RobotState schemas, RobotRunManifest, capture receipt
  runs/               ← common run schemas (RunStatus, RunType, RunRef)
  sample_views/       ← sample view policy, build and resolution
  scenarios/          ← ScenarioSet schemas
  scenes/             ← Scene domain (canonical SceneManifest, readiness, recording build config)
  sensors/            ← sensor modality enum
  streaming/          ← TelemetryEnvelope, channel registry, run lifecycle control events
```

## Core Concepts

### hierarchy

```
schemas/      ← 순수 Pydantic 모델 (데이터 구조)
contracts.py  ← Protocol 인터페이스 (구현 계약)
```

도메인별로 `schemas/`에 데이터 구조를 두고, 구현이 필요한 경우 `contracts.py`에 Protocol을 정의

### SceneOpsBaseModel

모든 도메인 모델의 기반 클래스. camelCase alias와 직렬화 헬퍼를 제공

```python
from sceneops_core.common.schemas import SceneOpsBaseModel

class MySchema(SceneOpsBaseModel):
    my_field: str

obj = MySchema(my_field="value")
obj.to_artifact_dict()  # camelCase alias 적용, JSON 직렬화
obj.to_api_dict()       # API 응답용
```

### ArtifactStore contract

스토리지 백엔드의 Protocol 인터페이스. `sceneops-storage` 패키지가 구현

```python
from sceneops_core.artifacts.contracts import ArtifactStore

# Protocol이므로 duck typing으로 동작
# 구체 구현은 sceneops-storage 참고
```

### ArtifactRef

타입이 있는 아티팩트 참조. URI에 `ArtifactKind`, 크기, 체크섬, 미디어 타입을 추가로 기록

```python
from sceneops_core.artifacts import ArtifactKind, ArtifactRef

ref = ArtifactRef(
    kind=ArtifactKind.SCENE_MANIFEST,
    uri="s3://bucket/path/to/manifest.json",
    size_bytes=1024,
)
```

## 의존성

- `pydantic >= 2`
- 런타임 외부 의존성 없음 (storage, DB 패키지에 의존하지 않음)
