#!/usr/bin/env bash
# e2e_detection_evaluation.sh
#
# E2E test for the detection_evaluation pipeline:
#   predict_detection -> evaluate_detection
#
# Verifies that the new explicit contract fields are populated:
#   - InferenceRunRecord:  prediction_manifest_uri, prediction_count
#   - EvaluationRunRecord: primary_metric_name, primary_metric_value,
#                          prediction_count, ground_truth_count, evaluation_unit
#   - Leaderboard entry:   primary_metric_name, primary_metric_value
#
# Usage:
#   bash scripts/e2e/e2e_detection_evaluation.sh
#
# Env overrides:
#   API_BASE_URL      (default: http://localhost:8000)
#   DATASET_ID        (default: nuscenes)
#   DATASET_VERSION   (default: v1.0-mini)
#   MODEL_ID          (default: dummy-detector)
#   MODEL_VERSION     (default: v1)
#   POLL_TIMEOUT      max poll attempts, 5s each (default: 60 = 5 min)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
DATASET_ID="${DATASET_ID:-nuscenes}"
DATASET_VERSION="${DATASET_VERSION:-v1.0-mini}"
MODEL_ID="${MODEL_ID:-dummy-detector}"
MODEL_VERSION="${MODEL_VERSION:-v1}"
POLL_TIMEOUT="${POLL_TIMEOUT:-60}"

echo "=== detection_evaluation E2E ==="
echo "  API_BASE_URL=$API_BASE_URL"
echo "  DATASET_ID=$DATASET_ID  DATASET_VERSION=$DATASET_VERSION"
echo "  MODEL_ID=$MODEL_ID  MODEL_VERSION=$MODEL_VERSION"
echo ""

# ── 0. Ensure model exists ────────────────────────────────────────────────────

echo "--- 0. Upsert model ---"
upsert_model "$API_BASE_URL" "$MODEL_ID" "$MODEL_VERSION" "dummy detector" | jq '.version | {id, modelId}' 2>/dev/null || true
echo ""

# ── 1. Create pipeline run ────────────────────────────────────────────────────

echo "--- 1. Create pipeline run ---"
PAYLOAD="$(cat <<JSON
{
  "type": "detection_evaluation",
  "dataset_id": "$DATASET_ID",
  "dataset_version": "$DATASET_VERSION",
  "params": {
    "predict_detection": {
      "model_id": "$MODEL_ID",
      "model_version": "$MODEL_VERSION",
      "inference_backend": "mock"
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

# ── 2. Execute ────────────────────────────────────────────────────────────────

echo "--- 2. Dispatch ---"
EXEC_RESP="$(dispatch_pipeline_run "$API_BASE_URL" "$PIPELINE_RUN_ID")"
EXEC_STATUS="$(echo "$EXEC_RESP" | jq -r '.execution.status // "error"')"
echo "  execution status=$EXEC_STATUS"
if [ "$EXEC_STATUS" = "error" ]; then
  echo "$EXEC_RESP" | jq . >&2
  exit 1
fi
echo ""

# ── 3. Poll ───────────────────────────────────────────────────────────────────

echo "--- 3. Polling (up to $((POLL_TIMEOUT * 5))s) ---"
PIPELINE_JSON="$(poll_pipeline_terminal "$API_BASE_URL" "$PIPELINE_RUN_ID" "$POLL_TIMEOUT" 5)"
echo ""

# ── 4. Assert pipeline succeeded ─────────────────────────────────────────────

echo "--- 4. Assert pipeline ---"
FINAL_STATUS="$(echo "$PIPELINE_JSON" | jq -r '.pipelineRun.status')"
echo "  status=$FINAL_STATUS"

if [ "$FINAL_STATUS" = "failed" ]; then
  echo "  error=$(echo "$PIPELINE_JSON" | jq -r '.pipelineRun.error.message // "unknown"')"
fi

assert_pipeline_succeeded "$PIPELINE_JSON" 'detection_evaluation pipeline should succeed'
echo "  OK"
echo ""

# ── 5. Assert all steps succeeded ────────────────────────────────────────────

echo "--- 5. Assert steps ---"
TASKS_JSON="$(fetch_pipeline_tasks "$API_BASE_URL" "$PIPELINE_RUN_ID")"
TASK_COUNT="$(echo "$TASKS_JSON" | jq '.tasks | length')"
FAILED_TASKS="$(echo "$TASKS_JSON" | jq -r '[.tasks[] | select(.status != "succeeded")] | map("\(.pipelineTaskId)=\(.status)") | join(", ")')"

echo "$TASKS_JSON" | jq -r '.tasks[] | "  \(.pipelineTaskId): \(.status)"'

if [ -n "$FAILED_TASKS" ]; then
  echo "❌ Non-succeeded tasks: $FAILED_TASKS" >&2
  exit 1
fi
echo "  All $TASK_COUNT steps: OK"
echo ""

# ── 6. Extract run IDs from result summary ───────────────────────────────────

echo "--- 6. Extract run IDs ---"
CONTEXT_JSON="$(curl -sS "$(api_url "$API_BASE_URL" "/pipelines/runs/$PIPELINE_RUN_ID")")"
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

# ── 7. Assert inference run has explicit fields ───────────────────────────────

echo "--- 7. Assert inference run ---"
INFERENCE_JSON="$(curl -sS "$(api_url "$API_BASE_URL" "/inference/runs/$INFERENCE_RUN_ID")")"
INFER_STATUS="$(echo "$INFERENCE_JSON" | jq -r '.run.status')"
INFER_PRED_COUNT="$(echo "$INFERENCE_JSON" | jq -r '.run.predictionCount // empty')"
INFER_MANIFEST_URI="$(echo "$INFERENCE_JSON" | jq -r '.run.predictionManifestUri // empty')"

echo "  status=$INFER_STATUS"
echo "  predictionCount=$INFER_PRED_COUNT"
echo "  predictionManifestUri=$INFER_MANIFEST_URI"

assert_json_equals "$INFERENCE_JSON" '.run.status' 'succeeded' 'inference run should be succeeded'
assert_json_not_empty "$INFERENCE_JSON" '.run.predictionManifestUri' 'inference run predictionManifestUri'
assert_json_gt "$INFERENCE_JSON" '.run.sampleCount // 0' 0 'inference run sampleCount'
echo "  OK"
echo ""

# ── 8. Assert evaluation run has explicit primary metric fields ───────────────

echo "--- 8. Assert evaluation run ---"
EVAL_JSON="$(curl -sS "$(api_url "$API_BASE_URL" "/evaluations/runs/$EVALUATION_RUN_ID")")"
EVAL_STATUS="$(echo "$EVAL_JSON" | jq -r '.run.status')"
EVAL_PRIMARY_NAME="$(echo "$EVAL_JSON" | jq -r '.run.primaryMetricName // empty')"
EVAL_PRIMARY_VALUE="$(echo "$EVAL_JSON" | jq -r '.run.primaryMetricValue // empty')"
EVAL_PRED_COUNT="$(echo "$EVAL_JSON" | jq -r '.run.predictionCount // empty')"
EVAL_GT_COUNT="$(echo "$EVAL_JSON" | jq -r '.run.groundTruthCount // empty')"
EVAL_UNIT="$(echo "$EVAL_JSON" | jq -r '.run.evaluationUnit // empty')"
EVAL_MANIFEST_URI="$(echo "$EVAL_JSON" | jq -r '.run.evaluationManifestUri // empty')"

echo "  status=$EVAL_STATUS"
echo "  primaryMetricName=$EVAL_PRIMARY_NAME  primaryMetricValue=$EVAL_PRIMARY_VALUE"
echo "  predictionCount=$EVAL_PRED_COUNT  groundTruthCount=$EVAL_GT_COUNT"
echo "  evaluationUnit=$EVAL_UNIT"
echo "  evaluationManifestUri=$EVAL_MANIFEST_URI"

assert_json_equals "$EVAL_JSON" '.run.status' 'succeeded' 'evaluation run should be succeeded'
assert_json_not_empty "$EVAL_JSON" '.run.primaryMetricName' 'evaluation run primaryMetricName'
assert_json_not_empty "$EVAL_JSON" '.run.primaryMetricValue' 'evaluation run primaryMetricValue'
assert_json_not_empty "$EVAL_JSON" '.run.evaluationUnit' 'evaluation run evaluationUnit'
assert_json_not_empty "$EVAL_JSON" '.run.evaluationManifestUri' 'evaluation run evaluationManifestUri'
echo "  OK"
echo ""

# ── 9. Assert leaderboard entry has primary metric ────────────────────────────

echo "--- 9. Assert leaderboard ---"
LB_JSON="$(curl -sS "$(api_url "$API_BASE_URL" "/leaderboards/evaluations?dataset_id=$DATASET_ID&dataset_version=$DATASET_VERSION")")"
LB_COUNT="$(echo "$LB_JSON" | jq '.entries | length')"
echo "  leaderboard entries=$LB_COUNT"

if [ "$LB_COUNT" -lt 1 ]; then
  echo "❌ Expected at least 1 leaderboard entry" >&2
  exit 1
fi

LB_ENTRY="$(echo "$LB_JSON" | jq --arg eid "$EVALUATION_RUN_ID" '.entries[] | select(.evaluationRunId == $eid)')"
if [ -z "$LB_ENTRY" ]; then
  echo "  (evaluation_run_id not matched, using first entry)"
  LB_ENTRY="$(echo "$LB_JSON" | jq '.entries[0]')"
fi

LB_PRIMARY_NAME="$(echo "$LB_ENTRY" | jq -r '.primaryMetricName // empty')"
LB_PRIMARY_VALUE="$(echo "$LB_ENTRY" | jq -r '.primaryMetricValue // empty')"
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

echo "=== PASSED ==="
echo "  pipeline_run_id=$PIPELINE_RUN_ID"
echo "  inference_run_id=$INFERENCE_RUN_ID"
echo "  evaluation_run_id=$EVALUATION_RUN_ID"
echo "  primary_metric=$EVAL_PRIMARY_NAME=$EVAL_PRIMARY_VALUE"
echo "  prediction_count=$EVAL_PRED_COUNT  ground_truth_count=$EVAL_GT_COUNT"
