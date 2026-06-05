#!/usr/bin/env bash
# register_nuscenes_dataset.sh
#
# Idempotent: creates the nuScenes dataset and v1.0-mini version if they
# don't already exist. Safe to run multiple times.
#
# Usage:
#   bash scripts/fixtures/register_nuscenes_dataset.sh
#
# Env overrides:
#   API_BASE_URL      (default: http://localhost:8000)
#   DATASET_ID        (default: nuscenes)
#   DATASET_VERSION   (default: v1.0-mini)

set -euo pipefail

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
API_PREFIX="${API_PREFIX:-/api/v1}"
DATASET_ID="${DATASET_ID:-nuscenes}"
DATASET_VERSION="${DATASET_VERSION:-v1.0-mini}"

url() { echo "${API_BASE_URL}${API_PREFIX}${1}"; }

http_status() {
  curl -sS -o /dev/null -w "%{http_code}" "$(url "$1")"
}

http_post() {
  local path="$1"
  local body="$2"
  curl -sS -X POST "$(url "$path")" \
    -H "Content-Type: application/json" \
    -d "$body"
}

echo "=== register nuScenes dataset ==="
echo "  API_BASE_URL=$API_BASE_URL"
echo "  DATASET_ID=$DATASET_ID  DATASET_VERSION=$DATASET_VERSION"
echo ""

# ── Dataset ───────────────────────────────────────────────────────────────────

echo "--- dataset ---"
if [ "$(http_status "/datasets/$DATASET_ID")" = "200" ]; then
  echo "  already exists"
  curl -sS "$(url "/datasets/$DATASET_ID")" | jq '.dataset | {datasetId, type, status}'
else
  echo "  creating..."
  http_post "/datasets" "{
    \"dataset_id\": \"$DATASET_ID\",
    \"name\": \"nuScenes\",
    \"description\": \"nuScenes autonomous driving dataset\",
    \"type\": \"nuscenes\",
    \"metadata\": {}
  }" | jq '.dataset | {datasetId, type, status}'
fi
echo ""

# ── Dataset version ───────────────────────────────────────────────────────────

echo "--- dataset version ---"
if [ "$(http_status "/datasets/$DATASET_ID/versions/$DATASET_VERSION")" = "200" ]; then
  echo "  already exists"
  curl -sS "$(url "/datasets/$DATASET_ID/versions/$DATASET_VERSION")" \
    | jq '.version | {version, status, sceneCount, manifestUri}'
else
  echo "  creating..."
  http_post "/datasets/$DATASET_ID/versions" "{
    \"version\": \"$DATASET_VERSION\",
    \"metadata\": {}
  }" | jq '.version | {version, status}'
fi
echo ""

echo "=== done ==="
