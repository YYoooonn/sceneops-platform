#!/usr/bin/env bash
# smoke_streaming.sh — Kafka streaming transport smoke test.
#
# Thin wrapper: overrides the streaming settings to reach the broker via
# its HOST-mapped port (mirrors makefiles/e2e.mk's E2E_BOOTSTRAP_ENV
# localhost-override convention for Postgres/MinIO), then delegates to the
# real logic in tools/e2e/smoke_streaming.py.
#
# Prerequisites (this script does not do either of these for you):
#   make streaming-up      # starts the local Kafka broker (compose/streaming.yaml)
#
# Usage:
#   make smoke-streaming
#   KAFKA_HOST_PORT=9092 tools/e2e/smoke_streaming.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

KAFKA_HOST_PORT="${KAFKA_HOST_PORT:-9092}"

cd "$REPO_ROOT"
SCENEOPS_STREAMING_KAFKA_BOOTSTRAP_SERVERS="localhost:${KAFKA_HOST_PORT}" \
  uv run python tools/e2e/smoke_streaming.py
