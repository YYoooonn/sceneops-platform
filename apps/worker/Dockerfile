FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

COPY packages/sceneops-core ./packages/sceneops-core

COPY apps/worker/pyproject.toml ./apps/worker/pyproject.toml
COPY apps/worker/sceneops_worker ./apps/worker/sceneops_worker

RUN pip install --no-cache-dir -e ./packages/sceneops-core \
    && pip install --no-cache-dir -e ./apps/worker

WORKDIR /app/apps/worker

RUN pip install --no-cache-dir -e .

CMD ["sceneops-worker", "--help"]
