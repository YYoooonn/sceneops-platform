FROM python:3.11-slim

WORKDIR /workspace

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_PROJECT_ENVIRONMENT=/workspace/.venv
ENV PATH="/workspace/.venv/bin:$PATH"

# cpu (default) or cu121
ARG TORCH_FLAVOR=cpu

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

# 1. dependency resolution에 필요한 파일만 먼저 복사
COPY pyproject.toml uv.lock ./

COPY packages/sceneops-core/pyproject.toml ./packages/sceneops-core/pyproject.toml
COPY packages/sceneops-db/pyproject.toml ./packages/sceneops-db/pyproject.toml
COPY packages/sceneops-storage/pyproject.toml ./packages/sceneops-storage/pyproject.toml
COPY apps/inference-server/pyproject.toml ./apps/inference-server/pyproject.toml

# 2. torch 설치 (GPU/CPU 분기)
RUN if [ "$TORCH_FLAVOR" = "cu121" ]; then \
        pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cu121; \
    else \
        pip install --no-cache-dir torch; \
    fi

# 3. 나머지 dependency 설치
RUN uv sync --package sceneops-inference-server --frozen --no-dev --no-install-workspace

# 4. 소스 복사
COPY apps/inference-server/inference_server ./apps/inference-server/inference_server

# 5. workspace package 설치
RUN uv sync --package sceneops-inference-server --frozen --no-dev

WORKDIR /workspace/apps/inference-server

CMD ["uvicorn", "inference_server.main:app", "--host", "0.0.0.0", "--port", "8001"]
