FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

COPY packages/sceneops-core ./packages/sceneops-core

COPY apps/api/requirements.txt ./apps/api/requirements.txt

RUN pip install --no-cache-dir -e ./packages/sceneops-core \
    && pip install --no-cache-dir -r ./apps/api/requirements.txt

COPY apps/api/app ./apps/api/app

WORKDIR /app/apps/api

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
