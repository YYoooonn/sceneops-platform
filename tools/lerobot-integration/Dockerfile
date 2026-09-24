# LeRobot integration container (SceneOps V2 Request 4.2).
#
# Executes the frozen IntegrationRequest -> IntegrationResult contract
# (sceneops_core.integration_runtime, Request 4.1/4.1A) against
# sceneops_analytics.external_adapters.lerobot.entrypoint -- currently
# operation=EXPORT/format=lerobot only.
#
# Built with its OWN committed uv.lock (this directory), never the root
# workspace's -- see pyproject.toml's own comment for why this project must
# stay outside `[tool.uv.workspace]` (lerobot's numpy>=2 vs. apps/worker's
# nuscenes-devkit numpy<2 pin). This image never installs sceneops-db or
# nuscenes-devkit, and the root uv.lock is never touched by anything below.
#
# Build from the REPO ROOT (context: .), because this project's editable
# path sources (sceneops-core/storage/analytics) live outside this
# directory and Docker COPY cannot reach outside its build context:
#
#   docker build -f tools/lerobot-integration/Dockerfile -t sceneops-platform/lerobot-integration:local .
#
# (equivalently: make lerobot-image)

FROM python:3.11-slim

WORKDIR /workspace

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_PROJECT_ENVIRONMENT=/workspace/tools/lerobot-integration/.venv
ENV PATH="/workspace/tools/lerobot-integration/.venv/bin:$PATH"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        build-essential \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

# 1. Resolve/install every THIRD-PARTY dependency (lerobot, torch, ...)
#    first, from lock metadata alone -- cacheable independently of any
#    SceneOps source change. --no-install-local skips the three editable
#    path dependencies below; their source isn't copied in yet.
COPY tools/lerobot-integration/pyproject.toml tools/lerobot-integration/uv.lock ./tools/lerobot-integration/
WORKDIR /workspace/tools/lerobot-integration
RUN uv sync --frozen --no-dev --no-install-local

# 2. Copy exactly the SceneOps source this project's editable path sources
#    point at (sceneops-core/storage/analytics) plus this project itself --
#    never apps/worker, never sceneops-db, never nuscenes-devkit.
WORKDIR /workspace
COPY packages/sceneops-core ./packages/sceneops-core
COPY packages/sceneops-storage ./packages/sceneops-storage
COPY packages/sceneops-analytics ./packages/sceneops-analytics
COPY tools/lerobot-integration ./tools/lerobot-integration

# 3. Install the local editable packages now that their source exists.
WORKDIR /workspace/tools/lerobot-integration
RUN uv sync --frozen --no-dev

# --no-sync: the venv above is already exactly what the committed
# uv.lock/--no-dev build produced -- never re-check or mutate it (no
# network access, no dev-group drift) on every container run.
ENTRYPOINT ["uv", "run", "--no-sync", "python", "-m", "sceneops_analytics.external_adapters.lerobot.entrypoint"]
