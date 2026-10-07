#!/usr/bin/env bash
# reset_local_state.sh — the single implementation behind `make local-reset`.
#
# Destructive: removes every piece of generated runtime state — the Postgres,
# Redis and MinIO volumes, the Kafka log, the acquisition-recordings capture
# volume and the generated ./data artifacts — then brings the stack back up
# clean via `make local-up`. Preserved: the source dataset (data/raw), the
# reference corpus cache (data/reference) and everything under config/, so the
# reference environment is rebuilt from the locked corpus alone
# (`make reference-contract-bootstrap`). The ROS2/inference images are
# untouched — they're opt-in stacks with their own lifecycle, not part of "the
# local stack" this resets.
#
# Set FORCE=1 to skip the interactive confirmation (used by controlled
# verification steps, not for casual use).

set -euo pipefail

ENV_FILE="${ENV_FILE:-.env.local}"
COMPOSE=(docker compose --env-file "$ENV_FILE")

echo "This will PERMANENTLY delete local Postgres/Redis/MinIO/Kafka/capture data and generated artifacts."
echo "Kept: data/raw, data/reference, config/ (the reference corpus)."
if [ "${FORCE:-0}" != "1" ]; then
  read -r -p "Continue? [y/N] " answer
  if [ "$answer" != "y" ]; then
    echo "aborted"
    exit 0
  fi
fi

echo "--- stopping stack and removing volumes (postgres, redis, minio, kafka, acquisition-recordings) ---"
# The streaming and acquisition profiles are named so that their volumes (the Kafka log
# and the transient capture volume) are removed with the rest of the runtime state.
"${COMPOSE[@]}" --profile worker --profile tools --profile debug --profile streaming --profile acquisition --profile ros2 --profile recovery down -v --remove-orphans

echo "--- clearing generated artifacts under ./data ---"
rm -rf data/datasets data/runs data/models data/artifacts
mkdir -p data/raw data/datasets data/runs data/models data/artifacts data/inputs cache/hf

echo "--- rebuilding clean stack ---"
make local-up ENV_FILE="$ENV_FILE"

echo "local state reset complete"
