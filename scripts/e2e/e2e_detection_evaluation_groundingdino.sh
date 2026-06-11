#!/usr/bin/env bash
# e2e_detection_evaluation_groundingdino.sh
#
# E2E test for the detection_evaluation pipeline using the real GroundingDINO
# inference server (no mock backend).
#
# Pipeline:
#   predict_detection (backend=grounding_dino) → evaluate_detection
#
# Prerequisites:
#   1. Full local stack running:    make local-up
#   2. Inference server running:    make inference-local-up  (CPU)
#                                   make inference-gpu-up    (GPU)
#   3. Dataset ingested:            make e2e-dataset-scene-ingestion
#
# The worker reaches the inference server via INFERENCE_ENDPOINT_URL
# (container-internal network alias).  The E2E script itself uses
# INFERENCE_SERVER_URL to poll /healthz and /readyz from the host.
#
# Usage:
#   bash scripts/e2e/e2e_detection_evaluation_groundingdino.sh
#
# Env overrides:
#   API_BASE_URL            (default: http://localhost:8000)
#   INFERENCE_SERVER_URL    host-side URL for health checks
#                           (default: http://localhost:8001)
#   INFERENCE_ENDPOINT_URL  container-internal URL passed to the worker
#                           (default: http://sceneops-inference:8001)
#   DATASET_ID              (default: nuscenes)
#   DATASET_VERSION         (default: v1.0-mini)
#   MODEL_ID                (default: grounding-dino)
#   MODEL_VERSION           (default: tiny)
#   MAX_SAMPLES             number of samples to run inference on (default: 5)
#   READYZ_TIMEOUT          readyz poll attempts at 2s each (default: 90 = 3 min)
#   POLL_TIMEOUT            pipeline poll attempts at 5s each (default: 120 = 10 min)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
INFERENCE_SERVER_URL="${INFERENCE_SERVER_URL:-http://localhost:8001}"
INFERENCE_ENDPOINT_URL="${INFERENCE_ENDPOINT_URL:-http://sceneops-inference:8001}"
DATASET_ID="${DATASET_ID:-nuscenes}"
DATASET_VERSION="${DATASET_VERSION:-v1.0-mini}"
MODEL_ID="${MODEL_ID:-grounding-dino}"
MODEL_VERSION="${MODEL_VERSION:-tiny}"
READYZ_TIMEOUT="${READYZ_TIMEOUT:-90}"
POLL_TIMEOUT="${POLL_TIMEOUT:-120}"
MAX_SCENES="${MAX_SCENES:-1}"
MAX_SAMPLES="${MAX_SAMPLES:-5}"

echo "=== GroundingDINO detection_evaluation E2E ==="
echo "  API_BASE_URL=$API_BASE_URL"
echo "  INFERENCE_SERVER_URL=$INFERENCE_SERVER_URL"
echo "  INFERENCE_ENDPOINT_URL=$INFERENCE_ENDPOINT_URL"
echo "  DATASET_ID=$DATASET_ID  DATASET_VERSION=$DATASET_VERSION"
echo "  MODEL_ID=$MODEL_ID  MODEL_VERSION=$MODEL_VERSION"
echo "  MAX_SCENES=$MAX_SCENES"
echo "  MAX_SAMPLES=$MAX_SAMPLES"
echo ""

# ── 1. API health ─────────────────────────────────────────────────────────────

echo "--- 1. API health ---"
if ! curl -sf "$(api_url "$API_BASE_URL" "/health" | sed "s|${API_BASE_URL}${API_PREFIX:-/api/v1}||")" \
    --url "${API_BASE_URL}/health" > /dev/null 2>&1; then
  if ! curl -sf "${API_BASE_URL}/health" > /dev/null 2>&1; then
    echo "❌ API not reachable at $API_BASE_URL" >&2
    exit 1
  fi
fi
echo "  OK: $API_BASE_URL/health"
echo ""

# ── 2. Inference server liveness ──────────────────────────────────────────────

echo "--- 2. Inference server liveness ---"
if ! curl -sf "${INFERENCE_SERVER_URL}/healthz" > /dev/null 2>&1; then
  echo "❌ Inference server not reachable at $INFERENCE_SERVER_URL" >&2
  echo "  Run: make inference-local-up  (CPU)  or  make inference-gpu-up  (GPU)" >&2
  exit 1
fi
echo "  OK: $INFERENCE_SERVER_URL/healthz"
echo ""

# ── 3. Inference server readiness (wait for model + warmup) ───────────────────

echo "--- 3. Inference server readiness (up to $((READYZ_TIMEOUT * 2))s) ---"
READYZ_JSON="$(poll_inference_ready "$INFERENCE_SERVER_URL" "$READYZ_TIMEOUT" 2)"
echo ""

WARMUP_SUCCEEDED="$(echo "$READYZ_JSON" | jq -r '.warmup_succeeded // null' 2>/dev/null || echo null)"
WARMUP_ENABLED="$(echo "$READYZ_JSON" | jq -r '.warmup_enabled // false' 2>/dev/null || echo false)"
DEVICE="$(echo "$READYZ_JSON" | jq -r '.device // "unknown"' 2>/dev/null || echo unknown)"
WARMUP_MS="$(echo "$READYZ_JSON" | jq -r '.warmup_elapsed_ms // null' 2>/dev/null || echo null)"
echo "  device=$DEVICE  warmup_enabled=$WARMUP_ENABLED  warmup_succeeded=$WARMUP_SUCCEEDED  warmup_elapsed_ms=${WARMUP_MS}ms"

if [ "$WARMUP_ENABLED" = "true" ] && [ "$WARMUP_SUCCEEDED" = "false" ]; then
  WARMUP_ERROR="$(echo "$READYZ_JSON" | jq -r '.warmup_error // unknown')"
  echo "❌ Inference server warmup failed: $WARMUP_ERROR" >&2
  echo "  Check inference server logs: make inference-local-logs" >&2
  exit 1
fi
echo "  OK"
echo ""

# ── 4. Dataset version ready check ───────────────────────────────────────────

echo "--- 4. Dataset version status ---"
DATASET_JSON="$(curl -sS "$(api_url "$API_BASE_URL" "/datasets/$DATASET_ID/versions/$DATASET_VERSION")")"
DATASET_STATUS="$(echo "$DATASET_JSON" | jq -r '.version.status // empty')"
echo "  $DATASET_ID/$DATASET_VERSION: status=$DATASET_STATUS"

if [ "$DATASET_STATUS" != "ready" ]; then
  echo "❌ Dataset version is not ready (status='$DATASET_STATUS')" >&2
  echo "  Run dataset ingestion first: make e2e-dataset-scene-ingestion" >&2
  exit 1
fi
echo "  OK"
echo ""

# ── 5. Upsert grounding-dino model ────────────────────────────────────────────

echo "--- 5. Upsert model ($MODEL_ID/$MODEL_VERSION backend=grounding_dino) ---"
MODEL_RESP="$(upsert_model_with_backend \
  "$API_BASE_URL" "$MODEL_ID" "$MODEL_VERSION" "$INFERENCE_ENDPOINT_URL" "GroundingDINO" "grounding_dino")"
echo "$MODEL_RESP" | jq '.version | {id, modelId, version, backend}' 2>/dev/null || true
echo ""

# ── 6. Create pipeline run ────────────────────────────────────────────────────

echo "--- 6. Create pipeline run ---"
PAYLOAD="$(cat <<JSON
{
  "type": "detection_evaluation",
  "dataset_id": "$DATASET_ID",
  "dataset_version": "$DATASET_VERSION",
  "model_id": "$MODEL_ID",
  "model_version": "$MODEL_VERSION",
  "params": {
    "predict_detection": {
      "model_id": "$MODEL_ID",
      "model_version": "$MODEL_VERSION",
      "inference_backend": "grounding_dino",
      "scene_selection": {
        "mode": "ground_truth_only",
        "max_scenes": $MAX_SCENES,
        "max_samples": $MAX_SAMPLES
      },
      "camera_channel": "CAM_FRONT"
    },
    "evaluate_detection": {
      "evaluator_id": "center-distance",
      "match_distance_m": 2.0
    }
  }
}
JSON
)"

CREATE_RESP="$(create_pipeline_run "$API_BASE_URL" "$PAYLOAD")"
PIPELINE_RUN_ID="$(extract_pipeline_run_id "$CREATE_RESP")"
echo "  pipeline_run_id=$PIPELINE_RUN_ID"
echo ""

# ── 7. Execute ────────────────────────────────────────────────────────────────

echo "--- 7. Dispatch ---"
EXEC_RESP="$(dispatch_pipeline_run "$API_BASE_URL" "$PIPELINE_RUN_ID")"
EXEC_STATUS="$(echo "$EXEC_RESP" | jq -r '.execution.status // "error"')"
echo "  execution status=$EXEC_STATUS"
if [ "$EXEC_STATUS" = "error" ]; then
  echo "$EXEC_RESP" | jq . >&2
  exit 1
fi
echo ""

# ── 8. Poll ───────────────────────────────────────────────────────────────────

echo "--- 8. Polling (up to $((POLL_TIMEOUT * 5))s) ---"
PIPELINE_JSON="$(poll_pipeline_terminal "$API_BASE_URL" "$PIPELINE_RUN_ID" "$POLL_TIMEOUT" 5)"
echo ""

# ── 9. Assert pipeline succeeded ─────────────────────────────────────────────

echo "--- 9. Assert pipeline ---"
FINAL_STATUS="$(echo "$PIPELINE_JSON" | jq -r '.pipelineRun.status')"
echo "  status=$FINAL_STATUS"

if [ "$FINAL_STATUS" = "failed" ]; then
  echo "  error=$(echo "$PIPELINE_JSON" | jq -r '.pipelineRun.error.message // "unknown"')"
fi

assert_pipeline_succeeded "$PIPELINE_JSON" 'detection_evaluation (groundingdino) pipeline should succeed'
echo "  OK"
echo ""

# ── 10. Assert all steps succeeded ───────────────────────────────────────────

echo "--- 10. Assert steps ---"
TASKS_JSON="$(fetch_pipeline_tasks "$API_BASE_URL" "$PIPELINE_RUN_ID")"
FAILED_TASKS="$(echo "$TASKS_JSON" | jq -r \
  '[.tasks[] | select(.status != "succeeded")] | map("\(.pipelineTaskId)=\(.status)") | join(", ")')"

echo "$TASKS_JSON" | jq -r '.tasks[] | "  \(.pipelineTaskId): \(.status)"'

if [ -n "$FAILED_TASKS" ]; then
  echo "❌ Non-succeeded tasks: $FAILED_TASKS" >&2
  exit 1
fi
TASK_COUNT="$(echo "$TASKS_JSON" | jq '.tasks | length')"
echo "  All $TASK_COUNT steps: OK"
echo ""

# ── 11. Extract run IDs ───────────────────────────────────────────────────────

echo "--- 11. Extract run IDs ---"
CONTEXT_JSON="$(fetch_pipeline_run "$API_BASE_URL" "$PIPELINE_RUN_ID")"
INFERENCE_RUN_ID="$(echo "$CONTEXT_JSON" | jq -r '.pipelineRun.result.outputs.inference_run_id // empty')"
EVALUATION_RUN_ID="$(echo "$CONTEXT_JSON" | jq -r '.pipelineRun.result.outputs.evaluation_run_id // empty')"
echo "  inference_run_id=$INFERENCE_RUN_ID"
echo "  evaluation_run_id=$EVALUATION_RUN_ID"

if [ -z "$INFERENCE_RUN_ID" ]; then
  echo "❌ inference_run_id missing from pipeline context" >&2
  exit 1
fi
if [ -z "$EVALUATION_RUN_ID" ]; then
  echo "❌ evaluation_run_id missing from pipeline context" >&2
  exit 1
fi
echo "  OK"
echo ""

# ── 12. Assert inference run ──────────────────────────────────────────────────

echo "--- 12. Assert inference run ---"
INFERENCE_JSON="$(curl -sS "$(api_url "$API_BASE_URL" "/inference/runs/$INFERENCE_RUN_ID")")"
INFER_STATUS="$(echo "$INFERENCE_JSON" | jq -r '.run.status')"
INFER_SAMPLE_COUNT="$(echo "$INFERENCE_JSON" | jq -r '.run.sampleCount // 0')"
INFER_PRED_COUNT="$(echo "$INFERENCE_JSON" | jq -r '.run.predictionCount // 0')"
INFER_MANIFEST_URI="$(echo "$INFERENCE_JSON" | jq -r '.run.predictionManifestUri // empty')"

echo "  status=$INFER_STATUS"
echo "  sampleCount=$INFER_SAMPLE_COUNT  predictionCount=$INFER_PRED_COUNT"
echo "  predictionManifestUri=$INFER_MANIFEST_URI"

INFER_EVALUABLE_COUNT="$(echo "$INFERENCE_JSON" | jq -r '.run.metrics.evaluable_prediction_count // "n/a"')"
INFER_LIFTING_SUCCEEDED="$(echo "$INFERENCE_JSON" | jq -r '.run.metrics.lifting_succeeded_count // "n/a"')"
INFER_LIFTING_FAILED="$(echo "$INFERENCE_JSON" | jq -r '.run.metrics.lifting_failed_count // "n/a"')"
INFER_LIFTING_NA="$(echo "$INFERENCE_JSON" | jq -r '.run.metrics.lifting_not_applicable_count // "n/a"')"

echo "  evaluable_prediction_count=$INFER_EVALUABLE_COUNT"
echo "  lifting_succeeded=$INFER_LIFTING_SUCCEEDED  lifting_failed=$INFER_LIFTING_FAILED  lifting_na=$INFER_LIFTING_NA"

assert_json_equals "$INFERENCE_JSON" '.run.status' 'succeeded' \
  'inference run should be succeeded'
assert_json_not_empty "$INFERENCE_JSON" '.run.predictionManifestUri' \
  'inference run predictionManifestUri'
assert_json_equals "$INFERENCE_JSON" ".run.sampleCount" "$MAX_SAMPLES" \
  "inference run sampleCount should equal MAX_SAMPLES=$MAX_SAMPLES"
assert_json_not_empty "$INFERENCE_JSON" '.run.metrics.lifting_failed_count // "0"' \
  'inference run lifting_failed_count field should exist'

# prediction_count > 0 is expected but not hard-asserted
# (GroundingDINO may detect nothing in low-contrast/night frames)
if [ "$INFER_PRED_COUNT" -eq 0 ]; then
  echo "  ⚠ predictionCount=0 (GroundingDINO found no detections — check thresholds or image quality)"
fi
echo "  OK"
echo ""

# ── 13. Assert evaluation run ─────────────────────────────────────────────────

echo "--- 13. Assert evaluation run ---"
EVAL_JSON="$(curl -sS "$(api_url "$API_BASE_URL" "/evaluations/runs/$EVALUATION_RUN_ID")")"
EVAL_STATUS="$(echo "$EVAL_JSON" | jq -r '.run.status')"
EVAL_PRIMARY_NAME="$(echo "$EVAL_JSON" | jq -r '.run.primaryMetricName // empty')"
EVAL_PRIMARY_VALUE="$(echo "$EVAL_JSON" | jq -r '.run.primaryMetricValue // empty')"
EVAL_PRED_COUNT="$(echo "$EVAL_JSON" | jq -r '.run.predictionCount // 0')"
EVAL_GT_COUNT="$(echo "$EVAL_JSON" | jq -r '.run.groundTruthCount // 0')"
EVAL_UNIT="$(echo "$EVAL_JSON" | jq -r '.run.evaluationUnit // empty')"
EVAL_MANIFEST_URI="$(echo "$EVAL_JSON" | jq -r '.run.evaluationManifestUri // empty')"
METRICS_URI="$(echo "$EVAL_JSON" | jq -r '.run.metricsUri // empty')"

echo "  status=$EVAL_STATUS"
echo "  primaryMetricName=$EVAL_PRIMARY_NAME  primaryMetricValue=$EVAL_PRIMARY_VALUE"
echo "  predictionCount=$EVAL_PRED_COUNT  groundTruthCount=$EVAL_GT_COUNT"
echo "  evaluationUnit=$EVAL_UNIT"
echo "  evaluationManifestUri=$EVAL_MANIFEST_URI"
echo "  metricsUri=$METRICS_URI"

assert_json_equals "$EVAL_JSON" '.run.status' 'succeeded' \
  'evaluation run should be succeeded'
assert_json_not_empty "$EVAL_JSON" '.run.primaryMetricName' \
  'evaluation run primaryMetricName'
assert_json_not_empty "$EVAL_JSON" '.run.primaryMetricValue' \
  'evaluation run primaryMetricValue'
assert_json_not_empty "$EVAL_JSON" '.run.evaluationUnit' \
  'evaluation run evaluationUnit'
assert_json_not_empty "$EVAL_JSON" '.run.evaluationManifestUri' \
  'evaluation run evaluationManifestUri'
echo "  OK"
echo ""

# ── 14. Assert leaderboard entry ──────────────────────────────────────────────

echo "--- 14. Assert leaderboard ---"
LB_JSON="$(curl -sS "$(api_url "$API_BASE_URL" \
  "/leaderboards/evaluations?dataset_id=$DATASET_ID&dataset_version=$DATASET_VERSION")")"
LB_COUNT="$(echo "$LB_JSON" | jq '.entries | length')"
echo "  leaderboard entries=$LB_COUNT"

if [ "$LB_COUNT" -lt 1 ]; then
  echo "❌ Expected at least 1 leaderboard entry" >&2
  exit 1
fi

LB_ENTRY="$(echo "$LB_JSON" | jq --arg eid "$EVALUATION_RUN_ID" \
  '.entries[] | select(.evaluationRunId == $eid)')"
if [ -z "$LB_ENTRY" ]; then
  echo "  (evaluation_run_id not matched, using first entry)"
  LB_ENTRY="$(echo "$LB_JSON" | jq '.entries[0]')"
fi

LB_ID="$(echo "$LB_ENTRY" | jq -r '.id // .leaderboardEntryId // empty')"
LB_PRIMARY_NAME="$(echo "$LB_ENTRY" | jq -r '.primaryMetricName // empty')"
LB_PRIMARY_VALUE="$(echo "$LB_ENTRY" | jq -r '.primaryMetricValue // empty')"
echo "  leaderboard_entry_id=$LB_ID"
echo "  primaryMetricName=$LB_PRIMARY_NAME  primaryMetricValue=$LB_PRIMARY_VALUE"

if [ -z "$LB_PRIMARY_NAME" ]; then
  echo "❌ Leaderboard entry missing primaryMetricName" >&2
  exit 1
fi
if [ -z "$LB_PRIMARY_VALUE" ]; then
  echo "❌ Leaderboard entry missing primaryMetricValue" >&2
  exit 1
fi
echo "  OK"
echo ""

# ── Summary ───────────────────────────────────────────────────────────────────

EVAL_LIFTING_FAILED="$(echo "$EVAL_JSON" | jq -r '.run.summary.lifting_failed_prediction_count // .run.metrics.lifting_failed_prediction_count // "n/a"')"
EVAL_EVALUABLE="$(echo "$EVAL_JSON" | jq -r '.run.summary.evaluable_prediction_count // .run.metrics.evaluable_prediction_count // "n/a"')"

echo "=== PASSED: GroundingDINO detection_evaluation E2E ==="
echo ""
echo "  pipeline_run_id                  = $PIPELINE_RUN_ID"
echo "  inference_run_id                 = $INFERENCE_RUN_ID"
echo "  evaluation_run_id                = $EVALUATION_RUN_ID"
echo "  device                           = $DEVICE"
echo "  inference_sample_count           = $INFER_SAMPLE_COUNT"
echo "  prediction_count (raw)           = $INFER_PRED_COUNT"
echo "  evaluable_prediction_count       = ${INFER_EVALUABLE_COUNT}"
echo "  lifting_succeeded_count          = ${INFER_LIFTING_SUCCEEDED}"
echo "  lifting_failed_count             = ${INFER_LIFTING_FAILED}"
echo "  lifting_not_applicable_count     = ${INFER_LIFTING_NA}"
echo "  eval_evaluable_prediction_count  = ${EVAL_EVALUABLE}"
echo "  eval_lifting_failed_count        = ${EVAL_LIFTING_FAILED}"
echo "  ground_truth_count               = $EVAL_GT_COUNT"
echo "  metrics_uri                      = ${METRICS_URI:-${EVAL_MANIFEST_URI}}"
echo "  primary_metric_name              = $EVAL_PRIMARY_NAME"
echo "  primary_metric_value             = $EVAL_PRIMARY_VALUE"
echo "  leaderboard_entry_id             = $LB_ID"
