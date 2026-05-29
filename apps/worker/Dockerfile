FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PIP_NO_CACHE_DIR=1

COPY packages/sceneops-core ./packages/sceneops-core
COPY packages/sceneops-db ./packages/sceneops-db
COPY packages/sceneops-storage ./packages/sceneops-storage
COPY apps/worker ./apps/worker

RUN pip install --upgrade pip \
    && pip install ./packages/sceneops-core \
    && pip install ./packages/sceneops-db \
    && pip install ./packages/sceneops-storage \
    && pip install ./apps/worker

CMD ["sceneops-worker", "--help"]
