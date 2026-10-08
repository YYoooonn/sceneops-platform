FROM python:3.11-slim

WORKDIR /workspace

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_PROJECT_ENVIRONMENT=/workspace/.venv
ENV PATH="/workspace/.venv/bin:$PATH"

RUN pip install --no-cache-dir uv

# 1. Dependencies first: only the manifests of the Publisher and the packages it uses.
#    The image holds no database, Celery or Kafka client: the Publisher is
#    database-free by construction.
COPY pyproject.toml uv.lock ./
COPY packages/sceneops-core/pyproject.toml ./packages/sceneops-core/pyproject.toml
COPY packages/sceneops-storage/pyproject.toml ./packages/sceneops-storage/pyproject.toml
COPY packages/sceneops-recording/pyproject.toml ./packages/sceneops-recording/pyproject.toml
COPY apps/publisher/pyproject.toml ./apps/publisher/pyproject.toml
RUN uv sync --package sceneops-publisher --frozen --no-dev --no-install-workspace

# 2. Sources of the same members.
COPY packages/sceneops-core ./packages/sceneops-core
COPY packages/sceneops-storage ./packages/sceneops-storage
COPY packages/sceneops-recording ./packages/sceneops-recording
COPY apps/publisher ./apps/publisher
RUN uv sync --package sceneops-publisher --frozen --no-dev

ENTRYPOINT ["python", "-m", "sceneops_publisher"]
CMD ["--help"]
