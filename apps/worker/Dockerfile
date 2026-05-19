FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

COPY apps/worker/pyproject.toml ./apps/worker/pyproject.toml
COPY apps/worker/sceneops_worker ./apps/worker/sceneops_worker

WORKDIR /app/apps/worker

RUN pip install --no-cache-dir -e .

CMD ["sceneops-worker", "--help"]
