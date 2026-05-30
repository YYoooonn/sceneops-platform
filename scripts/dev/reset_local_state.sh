#!/usr/bin/env bash
set -euo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.local.yml}"

echo "This will remove local DB/Redis volumes and generated artifacts."
read -r -p "Continue? [y/N] " answer

if [ "$answer" != "y" ]; then
  echo "aborted"
  exit 0
fi

docker compose -f "$COMPOSE_FILE" down -v

rm -rf data/datasets data/runs data/models
mkdir -p data/raw data/datasets data/runs data/models

echo "local state reset"
