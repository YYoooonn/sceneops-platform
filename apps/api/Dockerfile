FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PIP_NO_CACHE_DIR=1

COPY packages/sceneops-core ./packages/sceneops-core
COPY packages/sceneops-db ./packages/sceneops-db
COPY packages/sceneops-storage ./packages/sceneops-storage
COPY apps/api ./apps/api

RUN pip install --upgrade pip \
    && pip install ./packages/sceneops-core \
    && pip install ./packages/sceneops-db \
    && pip install ./packages/sceneops-storage \
    && pip install ./apps/api

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
