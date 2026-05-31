FROM python:3.11-slim

WORKDIR /workspace

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_PROJECT_ENVIRONMENT=/workspace/.venv
ENV PATH="/workspace/.venv/bin:$PATH"

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

# 1. dependency resolution에 필요한 파일만 먼저 복사
COPY pyproject.toml uv.lock ./

# workspace package metadata만 먼저 복사
COPY packages/sceneops-core/pyproject.toml ./packages/sceneops-core/pyproject.toml
COPY packages/sceneops-db/pyproject.toml ./packages/sceneops-db/pyproject.toml
COPY packages/sceneops-storage/pyproject.toml ./packages/sceneops-storage/pyproject.toml
COPY apps/api/pyproject.toml ./apps/api/pyproject.toml

# 2. dependency 먼저 설치
RUN uv sync --package sceneops-api --frozen --no-dev --no-install-workspace

# 3. 실제 소스 복사
COPY packages ./packages
COPY apps/api ./apps/api

RUN uv sync --package sceneops-api --frozen --no-dev

WORKDIR /workspace/apps/api

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
