#!/usr/bin/env bash
# e2e_api_smoke.sh — lightweight smoke test for the SceneOps Platform API
# Tests read endpoints and minimal create operations.
# Does NOT require worker execution to pass.
#
# Usage:
#   API_BASE_URL=http://localhost:8000 bash scripts/e2e/e2e_api_smoke.sh
#
# Dependencies: curl, jq

set -euo pipefail

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
PREFIX="/api/v1"
PASS=0
FAIL=0

# ── helpers ───────────────────────────────────────────────────────────────────

url() { echo "${API_BASE_URL}${PREFIX}${1}"; }

get() {
  local path="$1"
  curl -sf "$(url "$path")" -H "Accept: application/json"
}

post() {
  local path="$1"
  local body="$2"
  curl -sf -X POST "$(url "$path")" \
    -H "Content-Type: application/json" \
    -H "Accept: application/json" \
    -d "$body"
}

check() {
  local label="$1"
  local json="$2"
  if echo "$json" | jq -e . >/dev/null 2>&1; then
    echo "  ✅  $label"
    PASS=$((PASS + 1))
  else
    echo "  ❌  $label — invalid JSON"
    echo "      response: $json"
    FAIL=$((FAIL + 1))
  fi
}

check_field() {
  local label="$1"
  local json="$2"
  local expr="$3"
  local val
  val="$(echo "$json" | jq -r "$expr" 2>/dev/null || echo '')"
  if [ "$val" = "null" ] || [ -z "$val" ]; then
    echo "  ❌  $label — missing field: $expr"
    FAIL=$((FAIL + 1))
  else
    echo "  ✅  $label ($val)"
    PASS=$((PASS + 1))
  fi
}

wait_for_health() {
  local max_attempts=30
  local attempt=0
  echo "⏳ Waiting for API health..."
  while ! curl -sf "${API_BASE_URL}/health" >/dev/null 2>&1; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge "$max_attempts" ]; then
      echo "❌ API did not become healthy after ${max_attempts}s"
      exit 1
    fi
    sleep 1
  done
  echo "✅ API healthy"
}

# ── health ────────────────────────────────────────────────────────────────────

wait_for_health

echo ""
echo "─── health ──────────────────────────────────────────"
health="$(curl -sf "${API_BASE_URL}/health")"
check "GET /health" "$health"
check_field "status=ok" "$health" '.status'

# ── openapi ───────────────────────────────────────────────────────────────────

echo ""
echo "─── openapi ─────────────────────────────────────────"
openapi="$(curl -sf "${API_BASE_URL}/openapi.json")"
check "GET /openapi.json" "$openapi"
check_field "openapi.title" "$openapi" '.info.title'

# ── platform: read endpoints ──────────────────────────────────────────────────

echo ""
echo "─── platform ────────────────────────────────────────"
check "GET /jobs"                        "$(get /jobs)"
check "GET /pipelines/definitions"       "$(get /pipelines/definitions)"
check "GET /pipelines/runs"              "$(get /pipelines/runs)"
check "GET /executions"                  "$(get /executions)"
check "GET /artifacts"                   "$(get /artifacts)"

# ── domains: read endpoints ───────────────────────────────────────────────────

echo ""
echo "─── domains ─────────────────────────────────────────"
check "GET /datasets"                    "$(get /datasets)"
check "GET /scenes"                      "$(get /scenes)"
check "GET /scenario-sets"               "$(get /scenario-sets)"
check "GET /models"                      "$(get /models)"
check "GET /inference/runs"              "$(get /inference/runs)"
check "GET /evaluations/runs"            "$(get /evaluations/runs)"
check "GET /labels/scene-runs"           "$(get /labels/scene-runs)"
check "GET /labels/dataset-runs"         "$(get /labels/dataset-runs)"

# ── views ─────────────────────────────────────────────────────────────────────

echo ""
echo "─── views ───────────────────────────────────────────"
summary="$(get /operations/summary)"
check "GET /operations/summary"          "$summary"
check_field "summary.jobs exists"        "$summary" '.jobs'
check_field "summary.pipelines exists"   "$summary" '.pipelines'
check "GET /operations/timeline"         "$(get /operations/timeline)"
check "GET /operations/failures"         "$(get /operations/failures)"
check "GET /leaderboards/evaluations"    "$(get /leaderboards/evaluations)"

# ── pipeline definitions ──────────────────────────────────────────────────────

echo ""
echo "─── pipeline definitions ────────────────────────────"
defs="$(get /pipelines/definitions)"
check_field "definitions count > 0" "$defs" '.count'
def_count="$(echo "$defs" | jq -r '.count')"
echo "  📋 ${def_count} built-in pipeline definitions"

# ── create: dataset ───────────────────────────────────────────────────────────

echo ""
echo "─── create smoke ────────────────────────────────────"
DATASET_ID="smoke-test-$(date +%s)"

dataset_resp="$(post /datasets "{
  \"dataset_id\": \"${DATASET_ID}\",
  \"name\": \"Smoke Test Dataset\",
  \"type\": \"custom\"
}")"
check "POST /datasets" "$dataset_resp"
check_field "dataset.datasetId" "$dataset_resp" '.dataset.datasetId'

# create dataset version
version_resp="$(post "/datasets/${DATASET_ID}/versions" "{
  \"version\": \"v1.0\",
  \"status\": \"registered\"
}")"
check "POST /datasets/{id}/versions" "$version_resp"
check_field "version.version" "$version_resp" '.version.version'

# read back
check "GET /datasets/{id}" "$(get "/datasets/${DATASET_ID}")"
check "GET /datasets/{id}/versions" "$(get "/datasets/${DATASET_ID}/versions")"
check "GET /datasets/{id}/versions/v1.0" "$(get "/datasets/${DATASET_ID}/versions/v1.0")"
check "GET /datasets/{id}/versions/v1.0/quality" "$(get "/datasets/${DATASET_ID}/versions/v1.0/quality")"

# create model
MODEL_ID="smoke-model-$(date +%s)"
model_resp="$(post /models "{
  \"model_id\": \"${MODEL_ID}\",
  \"name\": \"Smoke Test Model\"
}")"
check "POST /models" "$model_resp"

model_version_resp="$(post "/models/${MODEL_ID}/versions" "{
  \"version\": \"v1.0\",
  \"backend\": \"mock\"
}")"
check "POST /models/{id}/versions" "$model_version_resp"

# create pipeline run (detection_evaluation)
pipe_resp="$(post /pipelines/runs "{
  \"type\": \"detection_evaluation\",
  \"dataset_id\": \"${DATASET_ID}\",
  \"dataset_version\": \"v1.0\",
  \"model_id\": \"${MODEL_ID}\",
  \"model_version\": \"v1.0\"
}")"
check "POST /pipelines/runs" "$pipe_resp"
PIPE_ID="$(echo "$pipe_resp" | jq -r '.pipelineRun.pipelineRunId')"
check_field "pipelineRunId" "$pipe_resp" '.pipelineRun.pipelineRunId'
check "GET /pipelines/runs/{id}" "$(get "/pipelines/runs/${PIPE_ID}")"
check "GET /pipelines/runs/{id}/steps" "$(get "/pipelines/runs/${PIPE_ID}/steps")"

# ── summary ───────────────────────────────────────────────────────────────────

echo ""
echo "═══════════════════════════════════════════════════"
echo "  Smoke test complete: ✅ ${PASS} passed / ❌ ${FAIL} failed"
echo "═══════════════════════════════════════════════════"

[ "$FAIL" -eq 0 ]
