#!/usr/bin/env bash
# E2E: GroundingDINO auto-label pipeline
#   1. Dataset ingestion (or reuse existing)
#   2. AUTO_LABEL_DATASET job (calls inference-server for 2D detection + frustum lifting)
#   3. EVALUATE_DETECTION job (center-distance evaluation vs GT)
#
# Prerequisites:
#   - make compose-up          (api, worker, postgres, redis)
#   - make inference-server-up (GPU inference server)
#   - nuScenes dataset ingested: make register-nuscenes-dataset
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
DATASET_ID="${DATASET_ID:-nuscenes}"
DATASET_VERSION="${DATASET_VERSION:-v1.0-mini}"
MODEL_ID="${MODEL_ID:-grounding-dino}"
MODEL_VERSION="${MODEL_VERSION:-tiny}"
MODEL_TASK_TYPE="${MODEL_TASK_TYPE:-autolabel}"
INFERENCE_SERVER_URL="${INFERENCE_SERVER_URL:-http://localhost:8001}"
INFERENCE_SERVER_URL_LOCAL="${INFERENCE_SERVER_URL_LOCAL:-http://sceneops-inference-server-local:8001}"
MAX_SAMPLES="${MAX_SAMPLES:-5}"
POLL_INTERVAL="${POLL_INTERVAL:-5}"
POLL_TIMEOUT="${POLL_TIMEOUT:-300}"

echo "=== SceneOps Auto-Label E2E (GroundingDINO + Frustum LiDAR) ==="
echo "API             : $API_BASE_URL"
echo "Inference server: $INFERENCE_SERVER_URL"
echo "Dataset         : $DATASET_ID:$DATASET_VERSION"
echo "Model           : $MODEL_ID:$MODEL_VERSION"
echo "Max samples     : $MAX_SAMPLES"
echo ""

# ── 0. Verify inference server is reachable ────────────────────────────────
echo "--- Checking inference server health ---"
HEALTH=$(curl -sf "$INFERENCE_SERVER_URL/healthz") || {
  echo "ERROR: inference server not reachable at $INFERENCE_SERVER_URL"
  echo "Run: make inference-server-up"
  exit 1
}
echo "Health: $HEALTH"
echo ""

# ── 1. Register GroundingDINO model (idempotent) ──────────────────────────
echo "--- Registering GroundingDINO model ---"
MODEL_RESPONSE="$(
  curl -sf -X POST "$(api_url "$API_BASE_URL" "/models")" \
    -H "Content-Type: application/json" \
    -d "{
      \"modelId\": \"$MODEL_ID\",
      \"taskType\": \"$MODEL_TASK_TYPE\",
      \"name\": \"GroundingDINO Tiny\",
      \"description\": \"Open-vocabulary 2D detection + frustum LiDAR 3D lifting\",
      \"metadata\": {
        \"e2e\": true,
        \"runtime\": \"mock\"
      }
    }"
)"
echo "$MODEL_RESPONSE" | jq .

MODEL_VERSION_RESP=$(curl -sf -X POST "$(api_url "$API_BASE_URL" "/models/$MODEL_ID/versions")" \
  -H "Content-Type: application/json" \
  -d "{
    \"version\": \"$MODEL_VERSION\",
    \"backend\": \"grounding_dino\",
    \"endpoint_url\": \"$INFERENCE_SERVER_URL_LOCAL\"
  }" 2>/dev/null || echo "{}")

echo "Model registered: $MODEL_ID:$MODEL_VERSION"
echo ""

# ── 2. Create AUTO_LABEL job ───────────────────────────────────────────────
echo "--- Creating AUTO_LABEL_DATASET job ---"
JOB_RESP=$(curl -sS -X POST "$(api_url "$API_BASE_URL" "/jobs")" \
  -H "Content-Type: application/json" \
  -d "{
    \"type\": \"auto_label_dataset\",
    \"params\": {
      \"dataset_id\": \"$DATASET_ID\",
      \"dataset_version\": \"$DATASET_VERSION\",
      \"model_id\": \"$MODEL_ID\",
      \"model_version\": \"$MODEL_VERSION\",
      \"vlm_backend\": \"grounding_dino\",
      \"endpoint_url\": \"$INFERENCE_SERVER_URL_LOCAL\",
      \"max_samples\": $MAX_SAMPLES
    }
  }")
JOB_ID=$(require_json_field "$JOB_RESP" ".job_id // .jobId // .job.id" "job id")
echo "Job created: $JOB_ID"

# ── 3. Execute job ─────────────────────────────────────────────────────────
echo "--- Executing job ---"
curl -sS -X POST "$(api_url "$API_BASE_URL" "/jobs/$JOB_ID/execute")" > /dev/null
echo "Job dispatched to Celery queue"

# ── 4. Poll until done ────────────────────────────────────────────────────
echo "--- Polling job status (timeout: ${POLL_TIMEOUT}s) ---"
elapsed=0
while true; do
  STATUS_RESP=$(curl -sf "$(api_url "$API_BASE_URL" "/jobs/$JOB_ID")")
  STATUS=$(echo "$STATUS_RESP" | jq -r '.status // .job.status')
  echo "  [${elapsed}s] $JOB_ID → $STATUS"

  if [ "$STATUS" = "succeeded" ]; then
    echo ""
    echo "=== AUTO_LABEL succeeded ==="
    RESULT=$(echo "$STATUS_RESP" | jq '.result // .job.result')
    echo "$RESULT" | jq .
    AUTO_LABEL_RUN_ID=$(echo "$RESULT" | jq -r '.auto_label_run_id')
    echo ""
    echo "auto_label_run_id: $AUTO_LABEL_RUN_ID"
    break
  fi

  if [ "$STATUS" = "failed" ]; then
    echo ""
    echo "ERROR: job failed"
    echo "$STATUS_RESP" | jq '.result.error // .error'
    exit 1
  fi

  if [ "$elapsed" -ge "$POLL_TIMEOUT" ]; then
    echo "ERROR: timed out after ${POLL_TIMEOUT}s"
    exit 1
  fi

  sleep "$POLL_INTERVAL"
  elapsed=$((elapsed + POLL_INTERVAL))
done

echo ""
echo "=== Done ==="
echo "Auto-label predictions are stored under runs/inference/$AUTO_LABEL_RUN_ID/"
echo "To evaluate: run EVALUATE_DETECTION with inference_run_id=$AUTO_LABEL_RUN_ID"
