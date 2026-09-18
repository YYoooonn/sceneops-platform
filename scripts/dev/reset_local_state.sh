#!/usr/bin/env bash
# reset_local_state.sh — the single implementation behind `make local-reset`.
#
# Destructive: removes the local stack's Postgres/Redis/MinIO volumes and
# generated ./data artifacts, then brings the stack back up clean via
# `make local-up`. Airflow's own Postgres volume and ROS2/inference images
# are untouched — they're opt-in stacks with their own lifecycle, not part
# of "the local stack" this resets.
#
# Set FORCE=1 to skip the interactive confirmation (used by controlled
# verification steps, not for casual use).

set -euo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.local.yml}"
ENV_FILE="${ENV_FILE:-.env.local}"
COMPOSE=(docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE")

echo "This will PERMANENTLY delete local Postgres/Redis/MinIO data and generated artifacts."
if [ "${FORCE:-0}" != "1" ]; then
  read -r -p "Continue? [y/N] " answer
  if [ "$answer" != "y" ]; then
    echo "aborted"
    exit 0
  fi
fi

echo "--- stopping stack and removing volumes (postgres, redis, minio) ---"
"${COMPOSE[@]}" --profile worker --profile tools --profile debug down -v --remove-orphans

echo "--- clearing generated artifacts under ./data ---"
rm -rf data/datasets data/runs data/models data/artifacts
mkdir -p data/raw data/datasets data/runs data/models data/artifacts cache/hf

echo "--- rebuilding clean stack ---"
make local-up COMPOSE_FILE="$COMPOSE_FILE" ENV_FILE="$ENV_FILE"

echo "local state reset complete"
