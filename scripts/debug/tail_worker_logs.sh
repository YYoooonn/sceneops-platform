#!/usr/bin/env bash
set -euo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.local.yml}"
SERVICE="${SERVICE:-worker-celery}"
TAIL="${TAIL:-200}"

docker compose -f "${COMPOSE_FILE}" logs -f --tail="${TAIL}" "${SERVICE}"
