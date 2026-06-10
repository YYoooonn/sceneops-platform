FROM python:3.11-slim

WORKDIR /workspace

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_PROJECT_ENVIRONMENT=/workspace/.venv
ENV PATH="/workspace/.venv/bin:$PATH"

ARG TORCH_FLAVOR=cpu

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./

COPY packages/sceneops-core/pyproject.toml ./packages/sceneops-core/pyproject.toml
COPY packages/sceneops-db/pyproject.toml ./packages/sceneops-db/pyproject.toml
COPY packages/sceneops-storage/pyproject.toml ./packages/sceneops-storage/pyproject.toml
COPY apps/inference-server/pyproject.toml ./apps/inference-server/pyproject.toml

RUN uv venv

RUN if [ "$TORCH_FLAVOR" = "cu121" ]; then \
        uv pip install --no-cache torch --index-url https://download.pytorch.org/whl/cu121; \
    else \
        uv pip install --no-cache torch; \
    fi

RUN uv sync --package sceneops-inference-server --frozen --no-dev --no-install-workspace

COPY packages ./packages
COPY apps/inference-server ./apps/inference-server

RUN uv sync --package sceneops-inference-server --frozen --no-dev

WORKDIR /workspace/apps/inference-server

CMD ["uvicorn", "inference_server.main:app", "--host", "0.0.0.0", "--port", "8001"]
