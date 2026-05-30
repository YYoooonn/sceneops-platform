#!/usr/bin/env bash
set -euo pipefail

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
API_PREFIX="${API_PREFIX:-}"

echo "== Inference runs =="
curl -sS "${API_BASE_URL}${API_PREFIX}/runs/inference" | python -m json.tool || true

echo ""
echo "== Evaluation runs =="
curl -sS "${API_BASE_URL}${API_PREFIX}/runs/evaluations" | python -m json.tool || true
