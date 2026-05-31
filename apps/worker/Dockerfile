FROM python:3.11-slim

WORKDIR /workspace

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_PROJECT_ENVIRONMENT=/workspace/.venv
ENV PATH="/workspace/.venv/bin:$PATH"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        build-essential \
        libgeos-dev \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

# 1. dependency resolution에 필요한 파일만 먼저 복사
COPY pyproject.toml uv.lock ./

# workspace package metadata만 먼저 복사
COPY packages/sceneops-core/pyproject.toml ./packages/sceneops-core/pyproject.toml
COPY packages/sceneops-db/pyproject.toml ./packages/sceneops-db/pyproject.toml
COPY packages/sceneops-storage/pyproject.toml ./packages/sceneops-storage/pyproject.toml
COPY apps/worker/pyproject.toml ./apps/worker/pyproject.toml

# 2. dependency 먼저 설치
RUN uv sync --package sceneops-worker --frozen --no-dev --no-install-workspace

# 3. 실제 소스 복사
COPY packages ./packages
COPY apps/worker ./apps/worker

# 4. workspace package만 설치
RUN uv sync --package sceneops-worker --frozen --no-dev

WORKDIR /workspace/apps/worker

CMD ["sceneops-worker", "--help"]
