#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${ENV_FILE:-.env.local}"
COMPOSE=(docker compose --env-file "$ENV_FILE")

"${COMPOSE[@]}" exec redis redis-cli ping

# worker-pipeline and worker-jobs are separate Celery workers (different
# queues, see compose/workers.yaml) — ping both.
for worker in worker-pipeline worker-jobs; do
  "${COMPOSE[@]}" exec "$worker" \
    celery -A sceneops_worker.celery_app:celery_app inspect ping || {
      echo "Celery inspect failed for $worker."
      "${COMPOSE[@]}" logs --tail=100 "$worker"
      exit 1
    }
done
