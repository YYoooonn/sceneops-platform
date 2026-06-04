# sceneops-core

SceneOps 플랫폼의 핵심 도메인 라이브러리. 모든 다른 패키지와 앱이 의존하는 공유 타입, 계약(Protocol), 경로 유틸리티를 제공

런타임 I/O 없이 순수 Python 타입과 Pydantic 모델만으로 구성

## Structure

```
sceneops_core/
  artifacts/          ← artifact type (ArtifactStore contract, ArtifactKind, ArtifactRef)
  common/             ← common type (SceneOpsBaseModel, type alias, ID utils)
  config.py           ← runtime settings (ArtifactSettings, ExecutionSettings .etc)
  constants/          ← platform constants (sensor name, queue name)
  datasets/           ← dataset domain (Record, Manifest, Run, Validation schema)
  evaluations/        ← evaluation comain (Metric, Leaderboard, History schema)
  executions/         ← Execution Backend schema (Celery / Airflow)
  inference/          ← Inference domain (Detection 스키마, 백엔드 타입)
  jobs/               ← Job domain (JobManifest, JobEvent, 파라미터, 스텝 레지스트리)
  labels/             ← AutoLabel domain schema
  models/             ← Model Registry Schema (Record, Artifact)
  observations/       ← Raw Observation domain (RawLog, frame, sensor frame)
  operations/         ← Operations schema
  paths/              ← URI generation pure functions
  pipelines/          ← Pipeline domain (definitions, manifests, pipeline steps)
  runs/               ← Common Run Schema (RunStatus, RunType, RunRef)
  scenarios/          ← Scenario Domain (Candidates, Predicates, Mining schema)
  scenes/             ← Scene Domain (Manifest, WorldState, Segment schema)
  sensors/            ← Sensor enums (camera, LiDAR .etc)
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
obj.to_db_dict()        # DB 저장용
```

### ArtifactStore contract

스토리지 백엔드의 Protocol 인터페이스. `sceneops-storage` 패키지가 구현

```python
from sceneops_core.artifacts.contracts import ArtifactStore

# Protocol이므로 duck typing으로 동작
# 구체 구현은 sceneops-storage 참고
```

### paths — URI generation pure functions

아티팩트 경로를 일관되게 생성하는 무상태 함수 모음.

```python
from sceneops_core.paths import (
    dataset_manifest_uri,
    scene_manifest_uri,
    inference_run_manifest_uri,
)

uri = dataset_manifest_uri(root_uri, dataset_id, dataset_version)
# → "{root_uri}/datasets/{dataset_id}/versions/{dataset_version}/manifest.json"
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
