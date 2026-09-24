# nuScenes integration container (SceneOps V2 Request 4.5, HTTP transport
# in Request 4.6A).
#
# Executes the frozen IntegrationRequest -> IntegrationResult contract
# (sceneops_core.integration_runtime, Request 4.1/4.1A) against
# sceneops_integrations.nuscenes -- operation=INGEST/format=nuscenes only.
#
# Two entrypoints share this one image (Request 4.6A §5 -- the CLI path is
# kept, not deleted):
#   default command  -> HTTP service (service.py), what the worker calls
#                        in production (compose/integrations.yaml)
#   overridden command -> CLI entrypoint (entrypoint.py), used by
#                        `make nuscenes-container-smoke` and direct local
#                        debugging: `docker run <image> -m
#                        sceneops_integrations.nuscenes.entrypoint
#                        --request-file ... --raw-log-id ... ...`
#
# Built with its OWN committed uv.lock (this directory), never the root
# workspace's -- see pyproject.toml's own comment for why this project
# stays outside `[tool.uv.workspace]`: a minimal, reproducible, DB/Celery-
# free dependency closure for the image, not (unlike ../lerobot-
# integration) primarily a numpy-version conflict. This image never
# installs sceneops-db, Celery, sceneops-worker, or lerobot, and the root
# uv.lock is never touched by anything below.
#
# Build from the REPO ROOT (context: .), because this project's editable
# path sources (sceneops-core/storage/integrations) live outside this
# directory and Docker COPY cannot reach outside its build context:
#
#   docker build -f tools/nuscenes-integration/Dockerfile -t sceneops-platform/nuscenes-integration:local .
#
# (equivalently: make nuscenes-image)

FROM python:3.11-slim

WORKDIR /workspace

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_PROJECT_ENVIRONMENT=/workspace/tools/nuscenes-integration/.venv
ENV PATH="/workspace/tools/nuscenes-integration/.venv/bin:$PATH"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

# 1. Resolve/install every THIRD-PARTY dependency (nuscenes-devkit, numpy,
#    opencv-python-headless, ...) first, from lock metadata alone --
#    cacheable independently of any SceneOps source change.
#    --no-install-local skips the three editable path dependencies below;
#    their source isn't copied in yet.
COPY tools/nuscenes-integration/pyproject.toml tools/nuscenes-integration/uv.lock ./tools/nuscenes-integration/
WORKDIR /workspace/tools/nuscenes-integration
RUN uv sync --frozen --no-dev --no-install-local

# 2. Copy exactly the SceneOps source this project's editable path sources
#    point at (sceneops-core/storage/integrations) plus this project itself
#    -- never apps/worker, never sceneops-db, never lerobot.
WORKDIR /workspace
COPY packages/sceneops-core ./packages/sceneops-core
COPY packages/sceneops-storage ./packages/sceneops-storage
COPY packages/sceneops-integrations ./packages/sceneops-integrations
COPY tools/nuscenes-integration ./tools/nuscenes-integration

# 3. Install the local editable packages now that their source exists.
WORKDIR /workspace/tools/nuscenes-integration
RUN uv sync --frozen --no-dev

# --no-sync: the venv above is already exactly what the committed
# uv.lock/--no-dev build produced -- never re-check or mutate it (no
# network access, no dev-group drift) on every container run.
#
# ENTRYPOINT has no module baked in (unlike Request 4.5) so CMD can be
# overridden wholesale to switch between the HTTP service (default, what
# compose/integrations.yaml runs) and the CLI entrypoint (smoke/debug,
# `docker run <image> -m sceneops_integrations.nuscenes.entrypoint ...`).
ENTRYPOINT ["uv", "run", "--no-sync", "python"]
CMD ["-m", "uvicorn", "sceneops_integrations.nuscenes.service:app", "--host", "0.0.0.0", "--port", "8080"]
