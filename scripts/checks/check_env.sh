#!/usr/bin/env bash
set -euo pipefail

test -f .env.local || {
  echo ".env.local not found"
  exit 1
}

test -f uv.lock || {
  echo "uv.lock not found. Run: uv lock"
  exit 1
}

uv --version >/dev/null
docker compose version >/dev/null

grep -q "SCENEOPS_API_DATABASE_URL" .env.local || echo "WARN: missing SCENEOPS_API_DATABASE_URL"
grep -q "SCENEOPS_API_EXECUTION__BACKEND" .env.local || echo "WARN: missing SCENEOPS_API_EXECUTION__BACKEND"
grep -q "SCENEOPS_WORKER_ARTIFACT__ROOT_URI" .env.local || echo "WARN: missing SCENEOPS_WORKER_ARTIFACT__ROOT_URI"

echo "env check ok"
