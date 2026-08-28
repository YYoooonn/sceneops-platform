# ADR-006: 분석용 데이터는 Parquet + PyArrow/Polars/DuckDB로 다룬다

## Status

Accepted

## Context

PostgreSQL은 운영 메타데이터(상태, 카운트, URI 참조)에는 적합하지만 ([ADR-001](./001-postgresql-operational-metadata.md)),
scene/sample/sensor_frame/annotation 단위의 대량 레코드를 컬럼 단위로 스캔·집계·조인하는
분석 워크로드(예: dataset 전체의 채널별 분포, 품질 프로파일링)에는 비효율적이다. 로드맵
Phase 1(§7)은 이런 분석 워크로드를 위한 별도 analytical layer를 요구한다.

## Decision

PostgreSQL/ArtifactStore와 별개로 **Parquet 분석 레이어**를 둔다. 역할 분담은:

```text
PyArrow → schema / Parquet IO
Polars  → filter / join / aggregation / profiling
DuckDB  → local SQL / debugging
```

저장 위치는 ArtifactStore의 `analytical/{dataset_id}/{dataset_version}/{table_name}.parquet`
prefix ([storage-layout.md](../storage-layout.md) §3) — 별도 저장소를 새로 두지 않고 기존
`ArtifactStore` 추상화([ADR-002](./002-object-storage-for-assets.md))를 그대로 재사용한다.
`scenes`/`samples`/`sensor_frames`/`annotations` 4개 테이블을 `packages/sceneops-analytics`의
`AnalyticsTableWriter`가 `EXPORT_ANALYTICS_SNAPSHOT` job을 통해 기록하며, 재실행 시 같은 URI를
덮어쓰는 idempotent-rebuild 패턴을 따른다 (`build_dataset_manifest`와 동일 패턴).

## Consequences

- v1 구현 완료 범위는 `scenes`/`samples`/`sensor_frames`/`annotations` 4개 테이블이다.
  `predictions.parquet`/`evaluations.parquet`는 prediction shard enumeration이 별도로 필요해
  fast-follow로 남아있다 ([storage-layout.md](../storage-layout.md) §6).
- Parquet 파일은 스냅샷이지 append-only 로그가 아니다 — 매 export가 전체를 재작성한다. 증분
  업데이트나 시간 파티셔닝이 필요해지면(로드맵 Phase 5 Spark 단계) 이 스냅샷 방식을 재검토해야
  한다.
- 로드맵이 제안한 raw/curated 물리적 경로 구분([storage-layout.md](../storage-layout.md) §4 gap)은
  아직 적용하지 않았다 — 현재 `analytical/` prefix는 가공 단계와 무관하게 리소스 종류로만
  분기한다. Spark 단계에서 파티셔닝 전략을 설계할 때 함께 재검토한다.
