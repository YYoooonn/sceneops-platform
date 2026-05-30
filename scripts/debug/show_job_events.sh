#!/usr/bin/env bash
set -euo pipefail

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
API_PREFIX="${API_PREFIX:-}"
JOB_ID="${JOB_ID:?JOB_ID is required}"

curl -sS "${API_BASE_URL}${API_PREFIX}/jobs/${JOB_ID}/events" | python -m json.tool
