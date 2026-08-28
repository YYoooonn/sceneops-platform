# ADR-002: 바이너리/JSON 산출물은 ArtifactStore 추상화로 저장한다

## Status

Accepted

## Context

Scene manifest, validation/profile report, prediction/evaluation 결과, 향후 카메라/LiDAR/rosbag
같은 원본 바이너리까지 SceneOps가 다뤄야 할 대상 자산은 종류와 크기가 다양하다. 로컬 개발
환경에서는 파일시스템이 충분하지만, 운영 환경에서는 S3 호환 오브젝트 스토리지가 필요하다.
스토리지 백엔드를 코드 레벨에서 하드코딩하면 로컬→운영 전환 시 호출부 코드를 전부 고쳐야 한다.

## Decision

`sceneops_core.artifacts.contracts.ArtifactStore` Protocol 하나로 스토리지 인터페이스를
고정하고, 구현체는 `LocalArtifactStore` / `S3ArtifactStore` 두 가지를 제공한다
([storage-layout.md](../storage-layout.md) §1-2). 인터페이스는 JSON과 binary를 구분한
`read_json`/`write_json`/`read_bytes`/`write_bytes`, 그리고 `join_uri`/`exists`/`list_json`/
`delete_prefix`/`public_url`로 구성되며 전부 `async`다.

백엔드 선택은 `create_artifact_store(settings)` 팩토리가 `ArtifactBackend` 설정값
(`LOCAL`/`S3`/`MINIO`)으로 결정한다. `MINIO`도 `endpoint_url`만 다른 `S3ArtifactStore`로
매핑되므로, 로컬 개발 시 MinIO를 쓰다가 AWS S3로 옮길 때 `endpoint_url`을 비우고 자격 증명만
바꾸면 된다 — 코드 변경이 필요 없다 ([storage-layout.md](../storage-layout.md) §5).

## Consequences

- 호출부(worker job handler 등)는 `ArtifactStore` Protocol에만 의존하고 백엔드 종류를 알
  필요가 없다 — 새 백엔드(예: GCS)를 추가해도 팩토리와 구현체 하나만 늘리면 된다.
- 모든 스토리지 호출이 `async`이므로 로컬 파일 I/O(`LocalArtifactStore`)도 실제로는 동기
  코드를 감싼 형태다 — 성능상 이득은 없지만 S3 구현체와 동일한 호출 시그니처를 강제해서
  백엔드 전환 시 호출부 수정이 0줄이 되는 이득을 우선했다.
- URI 조합(`join_uri`)이 백엔드 구현이 아니라 `sceneops_storage.uri` 공용 함수에 있어서,
  경로 레이아웃 규칙(§3 [storage-layout.md](../storage-layout.md))을 바꿀 때 한 곳만 고치면 된다.
