# dataset-acquisition: external dataset -> finalized L1 MCAP (ADR-007 §29.13).
#
# The build context is this directory only:
#
#   docker build -t sceneops-platform/dataset-acquisition:local integrations/dataset-acquisition
#   (equivalently: make acquisition-image)
#
# so no SceneOps package source is reachable from the build, and the image
# is installed from this project's own pyproject.toml / uv.lock (--frozen).
# The runtime stage holds the virtualenv only: no compiler, no uv, no
# sources, no tests. It needs no network and no SceneOps credentials.
#
# Two targets share one tool and one lock:
#
#   batch   (default, last stage)  python slim; writes a local MCAP
#   replay                         ROS 2 Jazzy + the tool; publishes the same
#                                  events on ROS 2 topics (streaming mode)
#
#   docker build --target replay -t sceneops-platform/dataset-replay:local integrations/dataset-acquisition
#   (equivalently: make acquisition-image)
#
# Neither target contains a SceneOps package.

FROM ros:jazzy-ros-base AS replay

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/bin/python3

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3-pip build-essential \
    && rm -rf /var/lib/apt/lists/*
RUN pip3 install --break-system-packages --no-cache-dir uv==0.11.17

WORKDIR /src
# The venv sees the distribution's python packages (rclpy, yaml, ...); the
# tool's locked dependencies shadow them where both exist.
RUN uv venv --system-site-packages /opt/venv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable \
    && rm -rf /src
COPY replay-entrypoint.sh /usr/local/bin/replay-entrypoint
RUN chmod 0755 /usr/local/bin/replay-entrypoint

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN useradd --create-home --uid 10001 acquisition
USER acquisition
WORKDIR /home/acquisition
ENTRYPOINT ["replay-entrypoint"]
CMD ["--help"]

FROM python:3.11-slim AS build

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv==0.11.17

WORKDIR /src
# 1. Third-party dependencies from the lock alone (cacheable).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
# 2. The tool itself, installed non-editable so the runtime needs no source.
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.11-slim AS batch

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY --from=build /opt/venv /opt/venv

# Non-root. /recordings is the output staging location; a fresh named
# volume mounted there inherits this ownership.
RUN useradd --create-home --uid 10001 acquisition \
    && mkdir -p /recordings \
    && chown acquisition:acquisition /recordings
USER acquisition
WORKDIR /recordings

ENTRYPOINT ["dataset-acquisition"]
CMD ["--help"]
