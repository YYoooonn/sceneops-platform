#!/usr/bin/env bash
set -euo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.local.yml}"

docker compose -f "$COMPOSE_FILE" exec api python -c "
import app
import sceneops_core
import sceneops_db
import sceneops_storage
import celery
print('api imports ok')
"

docker compose -f "$COMPOSE_FILE" --profile debug run --rm --entrypoint python worker-cli -c "
import sceneops_worker
import sceneops_core
import sceneops_db
import sceneops_storage
import celery
print('worker imports ok')
"
