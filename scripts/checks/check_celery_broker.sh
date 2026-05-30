#!/usr/bin/env bash
set -euo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.local.yml}"

docker compose -f "$COMPOSE_FILE" exec redis redis-cli ping

docker compose -f "$COMPOSE_FILE" exec worker-celery \
  celery -A sceneops_worker.celery_app:celery_app inspect ping || {
    echo "Celery inspect failed."
    docker compose -f "$COMPOSE_FILE" logs --tail=100 worker-celery
    exit 1
  }
