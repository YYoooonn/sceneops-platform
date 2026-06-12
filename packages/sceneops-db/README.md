# sceneops-db

SceneOps 플랫폼의 데이터베이스 접근 레이어. PostgreSQL 기반의 비동기 SQLAlchemy 구현을 제공

## Folder Structure

```
sceneops_db/
  base.py             ← SQLAlchemy DeclarativeBase
  config.py           ← DB 연결 설정 (env: SCENEOPS_DATABASE_URL)
  session.py          ← 프로세스-로컬 async 엔진 및 세션 관리

  models/             ← SQLAlchemy ORM 모델 (DB 테이블 매핑)
  converters/         ← ORM 모델 ↔ Domain Record 변환 함수
  repositories/       ← Repository Protocol 인터페이스 (계약)
  postgres/           ← PostgreSQL Repository 구현체
```

## Layer Architecture

```
repositories/   ←  Protocol (Interface)
     ↑
postgres/       ←  Implementation (AsyncSession Injection)
     ↑
converters/     ←  ORM ↔ Domain Record 변환
     ↑
models/         ←  SQLAlchemy ORM 모델
```

## Repository list

| domain | Protocol | PostgreSQL 구현체 |
|--------|----------|-----------------|
| job | `JobRepository`, `JobEventRepository` | `PostgresJobRepository`, `PostgresJobEventRepository` |
| pipeline | `PipelineRunRepository`, `PipelineStepRunRepository` | `PostgresPipelineRunRepository`, `PostgresPipelineStepRunRepository` |
| execution | `ExecutionRecordRepository` | `PostgresExecutionRecordRepository` |
| dataset | `DatasetRepository`, `DatasetVersionRepository`, `DatasetRunRepository` | `PostgresDatasetRepository`, ... |
| scene | `SceneRepository`, `SceneRunRepository` | `PostgresSceneRepository`, `PostgresSceneRunRepository` |
| scenario | `ScenarioSetRepository`, `ScenarioRunRepository` | `PostgresScenarioSetRepository`, `PostgresScenarioRunRepository` |
| inference | `InferenceRunRepository` | `PostgresInferenceRunRepository` |
| evaluation | `EvaluationRunRepository` | `PostgresEvaluationRunRepository` |
| label | `LabelRunRepository` | `PostgresLabelRunRepository` |
| model registry | `ModelRepository`, `ModelVersionRepository` | `PostgresModelRepository`, `PostgresModelVersionRepository` |
| artifact | `ArtifactRefRepository` | `PostgresArtifactRefRepository` |

## Usage

### Session management

```python
from sceneops_db.session import async_session_scope
from sceneops_db.postgres import PostgresJobRepository

async with async_session_scope() as session:
    repo = PostgresJobRepository(session)
    job = await repo.get(job_id)
    await session.commit()
```

### FastAPI DI pattern

```python
from sceneops_db.session import get_db_session
from sqlalchemy.ext.asyncio import AsyncSession

async def get_job_repo(session: AsyncSession = Depends(get_db_session)):
    return PostgresJobRepository(session)
```

### Protocol type hints

서비스 레이어는 Protocol 타입으로 의존성을 선언하면 테스트 시 mock으로 교체 가능

```python
from sceneops_db.repositories import JobRepository

class JobService:
    def __init__(self, repo: JobRepository) -> None:
        self._repo = repo
```

## env varibles

| variable | description |
|------|------|
| `SCENEOPS_DATABASE_URL` | SQLAlchemy async DSN (예: `postgresql+asyncpg://user:pass@host/db`) |

## dependencies

- `sceneops-core`
- `sqlalchemy[asyncio] >= 2`
- `asyncpg`
- `pydantic-settings`
