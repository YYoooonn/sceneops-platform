# SceneOps Storage Layout

> Phase 0 문서. `packages/sceneops-storage`, `packages/sceneops-core/sceneops_core/config.py`
> 기준으로 실제 아티팩트 저장 구조를 기록한다.

## 1. ArtifactStore 인터페이스

`sceneops_core.artifacts.contracts.ArtifactStore`가 정의하는 Protocol:

```python
join_uri(root, *parts) -> ArtifactUri
exists(uri) -> bool
read_json(uri) / write_json(uri, payload)
read_bytes(uri) / write_bytes(uri, data)
list_json(uri) -> list[ArtifactUri]
delete_prefix(uri) -> None
public_url(uri) -> str
```

로드맵 섹션 7.2가 제안한 `put/get/exists/delete/list/get_uri` 인터페이스와 형태는
다르지만 (JSON과 binary를 구분한 read/write, `delete` 대신 `delete_prefix`) 동일한
목적을 충족한다. 모든 메서드가 `async`다 — object storage 구현이 네트워크 호출이기
때문.

## 2. 구현체

```text
ArtifactStore
├── LocalArtifactStore   packages/sceneops-storage/sceneops_storage/backends/local.py
└── S3ArtifactStore      packages/sceneops-storage/sceneops_storage/backends/s3.py
```

- `LocalArtifactStore`: `file://` 또는 scheme 없는 경로를 `pathlib.Path`로 다룸.
  JSON은 `json.dump(indent=2)`, 디렉터리는 `mkdir(parents=True, exist_ok=True)`로 자동 생성.
- `S3ArtifactStore`: `boto3` 기반, `s3://<bucket>/<key>` URI. `endpoint_url` 설정 시
  path-style addressing으로 전환되어 MinIO도 동일 클래스로 지원. 모든 호출은
  `asyncio.to_thread`로 래핑된 동기 boto3 호출.
- 선택은 `create_artifact_store(settings)` 팩토리가 `ArtifactBackend` enum
  (`LOCAL`/`S3`/`MINIO`, `MINIO`도 `S3ArtifactStore`로 매핑)으로 결정.

두 구현 모두 `join_uri()`가 동일한 `sceneops_storage.uri.join_uri` 함수를 쓰기 때문에,
백엔드를 바꿔도 URI 조합 로직(호출부 코드)은 변경할 필요가 없다 — 로드맵이 목표로 하는
"local에서 개발 후 S3로 교체 가능"이 이미 이 지점에서 달성되어 있다.

## 3. 현재 URI 레이아웃

`.env.example` / `ArtifactSettings`(`sceneops_core/config.py`) 기준 실제 prefix 구조:

```text
{ARTIFACT_ROOT_URI}/
  datasets/     ArtifactSettings.dataset_prefix
  runs/         ArtifactSettings.run_prefix
  models/       ArtifactSettings.model_prefix
  analytical/   ArtifactSettings.analytics_prefix   (Parquet analytical layer)
```

로컬: `/data/artifacts/{datasets,runs,models,analytical}/...`
S3/MinIO: `s3://sceneops/artifacts/{datasets,runs,models,analytical}/...`

`analytical/{dataset_id}/{dataset_version}/{table_name}.parquet` 형태로, `scenes`/
`samples`/`sensor_frames`/`annotations` 4개 테이블을 `EXPORT_ANALYTICS_SNAPSHOT` job이
`sceneops-analytics`의 `AnalyticsTableWriter`를 통해 기록한다. 재실행 시 같은 URI를
덮어쓴다 (`build_dataset_manifest`의 idempotent-rebuild 패턴과 동일 — §4 참고).

원본 데이터(raw dataset)는 별도 `RawSourceSettings`로 완전히 독립적으로 설정된다
(읽기 전용, 별도 root):

```text
로컬: /data/raw/nuscenes
S3:   s3://sceneops/raw/nuscenes
```

## 4. 로드맵 제안 레이아웃과의 차이

로드맵 섹션 7.3은 다음 레이아웃을 제안한다:

```text
sceneops/
  raw/datasets/
  curated/datasets/
  predictions/runs/
  evaluations/runs/
  models/
  artifacts/
```

실제 구현은 **가공 단계(raw/curated)가 아니라 리소스 종류(datasets/runs/models)** 로
분기한다. `datasets/`가 raw와 curated를 모두 포함하고, `predictions`/`evaluations`도
별도 최상위 디렉터리가 아니라 `runs/` 아래 `run_id`로 묶여 있을 가능성이 높다(각
worker 모듈의 실제 하위 경로 조합은 `sceneops_worker/*/artifacts.py`에서 개별적으로
구성되므로 이 문서에서 전수 조사하지 않음 — 필요 시 별도 조사).

**Gap**: raw/curated 구분이 경로 레벨에 없다. 현재는 `DatasetVersion.status`나
`validation_status` 같은 DB 컬럼으로 "이 버전이 curated인지"를 표현하고, 스토리지
경로 자체는 가공 단계와 무관하다. Parquet 분석 레이어(Phase 1 미착수)를 붙일 때
`curated/` 같은 물리적 구분이 필요해질 수 있다 — 그때 재검토.

## 5. Object Storage 계정/인프라

로컬 개발은 MinIO(S3 호환)를 사용하고(`docker-compose.local.yml`, `.env.example`의
`MINIO_ROOT_USER`), 실제 AWS S3로 교체 시 `endpoint_url`을 비우고 자격 증명만 바꾸면
된다 — 코드 변경 없이 backend 전환 가능한 구조가 이미 검증되어 있다.

## 6. 미해결 항목

- ~~Parquet analytical layer(로드맵 7.4)~~ — `packages/sceneops-analytics` +
  `EXPORT_ANALYTICS_SNAPSHOT` job으로 v1 구현 완료 (`scenes`/`samples`/
  `sensor_frames`/`annotations`, `analytical/` prefix). `predictions.parquet`는
  prediction shard enumeration이 별도로 필요해 fast-follow로 남겨둠.
- `Artifact` 테이블의 `checksum`/`size_bytes`가 쓰기 경로에서 실제로 채워지는지 미검증
  (data-model.md 8절 참고) — `ExportAnalyticsSnapshotJobHandler`가 기록하는
  `analytics_table` artifact도 이 두 필드는 채우지 않는다.
