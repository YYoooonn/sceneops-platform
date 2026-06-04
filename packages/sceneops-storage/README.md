# sceneops-storage

SceneOps 플랫폼의 아티팩트 스토리지 레이어. `sceneops-core`의 `ArtifactStore` Protocol을 구현하며, 로컬 파일시스템과 S3 호환 오브젝트 스토리지(AWS S3, MinIO)를 지원

## 구조

```
sceneops_storage/
  __init__.py         ← public API (ArtifactStore, exceptions, factory, uri 모두 re-export)
  exceptions.py       ← exceptions
  factory.py          ← registry 기반 ArtifactStore 생성
  uri.py              ← URI join util
  backends/
    local.py          ← LocalArtifactStore (local fs)
    s3.py             ← S3ArtifactStore (AWS S3 / MinIO)
```

## 지원 백엔드

| backend | ArtifactBackend type | URI style |
|--------|-------------------|---------|
| Local fs | `local` | `/path/to/dir` 또는 `file:///path/to/dir` |
| AWS S3 | `s3` | `s3://bucket/prefix` |
| MinIO | `minio` | `s3://bucket/prefix` (endpoint_url 별도 지정) |

## Usage

### ArtifactStore generation

```python
from sceneops_core.config import ArtifactSettings
from sceneops_storage import create_artifact_store

# 로컬
settings = ArtifactSettings(backend="local", root_uri="/data")
store = create_artifact_store(settings)

# MinIO
settings = ArtifactSettings(
    backend="minio",
    root_uri="s3://my-bucket",
    endpoint_url="http://minio:9000",
    access_key_id="minioadmin",
    secret_access_key="minioadmin",
)
store = create_artifact_store(settings)
```

### base method

```python
# JSON artifacts
await store.write_json(uri, {"key": "value"})
data = await store.read_json(uri)

# binary artifacts (model weights, image .etc)
await store.write_bytes(uri, model_bytes)
raw = await store.read_bytes(uri)

# existance or delete
exists = await store.exists(uri)
await store.delete_prefix(uri)  # 파일 또는 디렉토리(prefix) 삭제

# URI join
child_uri = store.join_uri(root_uri, "subdir", "file.json")

# JSON list (single depth only)
uris = await store.list_json(prefix_uri)
```

### exception handling

```python
from sceneops_storage import ArtifactNotFoundError, ArtifactReadError, ArtifactWriteError

try:
    data = await store.read_json(uri)
except ArtifactNotFoundError as e:
    # 아티팩트가 존재하지 않음
    print(e.uri)
except ArtifactReadError:
    # I/O 오류 등 읽기 실패
    ...
```

## 예외 계층

```
ArtifactStoreError        ← Base Artifact Store error
  ArtifactNotFoundError   ← uri does not exist
  ArtifactReadError       ← read fail (I/O, decoding 등)
  ArtifactWriteError      ← write fail
```

## to add new backend

1. `backends/` 에 구현 클래스 작성 (`ArtifactStore` Protocol 충족)
2. `sceneops_core.artifacts.schemas.ArtifactBackend`에 새 값 추가
3. `factory.py`의 `_REGISTRY`에 엔트리 추가

```python
# factory.py
_REGISTRY = {
    ...
    ArtifactBackend.GCS: lambda s: GCSArtifactStore(settings=s),
}
```

## dependencies

- `sceneops-core`
- `boto3` (S3/MinIO 백엔드에서만 사용)
