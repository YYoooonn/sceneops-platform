FROM python:3.11-slim

WORKDIR /workspace

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

COPY apps/inference-server/pyproject.toml ./apps/inference-server/pyproject.toml

# torch를 먼저 별도 설치 (인덱스 URL이 다름)
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cu121

RUN uv pip install --system \
    "fastapi>=0.110" \
    "uvicorn[standard]>=0.29" \
    "pydantic>=2.0" \
    "transformers>=4.39" \
    "pillow>=10.0"

COPY apps/inference-server/inference_server ./inference_server

CMD ["uvicorn", "inference_server.main:app", "--host", "0.0.0.0", "--port", "8001"]
