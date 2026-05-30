#!/usr/bin/env bash
set -euo pipefail

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
API_PREFIX="${API_PREFIX:-}"
PIPELINE_RUN_ID="${PIPELINE_RUN_ID:?PIPELINE_RUN_ID is required}"

curl -sS "${API_BASE_URL}${API_PREFIX}/pipelines/runs/${PIPELINE_RUN_ID}" | python -m json.tool
