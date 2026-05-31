#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
DATASET_ID="${DATASET_ID:-nuscenes}"
DATASET_VERSION="${DATASET_VERSION:-v1.0-mini}"
MODEL_ID="${MODEL_ID:-mock-detector}"
MODEL_BACKEND="${MODEL_BACKEND:-mock}"
MODEL_NAME="${MODEL_NAME:-Mock Detector}"
MODEL_TASK_TYPE="${MODEL_TASK_TYPE:-detection}"
MODEL_VERSION="${MODEL_VERSION:-v0}"
MAX_SAMPLES="${MAX_SAMPLES:-2}"

echo "🚀 Running mock detection validation pipeline E2E with Celery"
echo "API_BASE_URL=$API_BASE_URL"
echo "API_PREFIX=${API_PREFIX:-/api/v1}"
echo "DATASET_ID=$DATASET_ID"
echo "DATASET_VERSION=$DATASET_VERSION"
echo "MODEL_ID=$MODEL_ID"
echo "MODEL_VERSION=$MODEL_VERSION"
echo "MAX_SAMPLES=$MAX_SAMPLES"

echo ""
echo "📦 Registering mock model..."
MODEL_RESPONSE="$(
  curl -sS -X POST "$(api_url "$API_BASE_URL" "/models")" \
    -H "Content-Type: application/json" \
    -d "{
      \"modelId\": \"$MODEL_ID\",
      \"taskType\": \"$MODEL_TASK_TYPE\",
      \"name\": \"$MODEL_NAME\",
      \"description\": \"Mock detector model for SceneOps E2E tests\",
      \"metadata\": {
        \"e2e\": true,
        \"runtime\": \"mock\"
      }
    }"
)"
echo "$MODEL_RESPONSE" | jq .

echo ""
echo "📦 Registering ONNX model version..."
MODEL_VERSION_RESPONSE="$(
  curl -sS -X POST "$(api_url "$API_BASE_URL" "/models/$MODEL_ID/versions")" \
    -H "Content-Type: application/json" \
    -d "{
      \"version\": \"${MODEL_VERSION}\",
      \"backend\": \"${MODEL_BACKEND}\",
      \"status\": \"ready\",
      \"metadata\": {
        \"e2e\": true,
        \"runtime\": \"mock\"
      }
    }"
)"
echo "$MODEL_VERSION_RESPONSE" | jq .

PAYLOAD="$(
  cat <<JSON
{
  "type": "detection_validation",
  "datasetId": "$DATASET_ID",
  "datasetVersion": "$DATASET_VERSION",
  "modelId": "$MODEL_ID",
  "modelVersion": "$MODEL_VERSION",
  "params": {
    "validate": {
      "validateSamples": true,
      "requireTargetChannels": ["CAM_FRONT", "LIDAR_TOP"]
    },
    "predict": {
      "inferenceBackend": "mock",
      "maxSamples": $MAX_SAMPLES
    },
    "evaluate": {
      "maxSamples": $MAX_SAMPLES,
      "matchDistanceM": 2.0
    }
  }
}
JSON
)"

echo "📦 Creating detection validation pipeline run..."
CREATE_RESPONSE="$(create_pipeline_run "$API_BASE_URL" "$PAYLOAD")"
echo "$CREATE_RESPONSE" | jq .

PIPELINE_RUN_ID="$(extract_pipeline_run_id "$CREATE_RESPONSE")"
echo "✅ pipeline_run_id=$PIPELINE_RUN_ID"

echo "🚀 Dispatching pipeline run..."
DISPATCH_RESPONSE="$(dispatch_pipeline_run "$API_BASE_URL" "$PIPELINE_RUN_ID")"
echo "$DISPATCH_RESPONSE" | jq .

echo "⏳ Waiting for pipeline terminal state..."
PIPELINE_JSON="$(poll_pipeline_terminal "$API_BASE_URL" "$PIPELINE_RUN_ID")"
echo "$PIPELINE_JSON" | jq .

assert_pipeline_succeeded "$PIPELINE_JSON" \
  'mock detection validation pipeline should succeed'

# echo "🔎 Checking validation lineage..."
# assert_validation_ready_from_pipeline "$PIPELINE_JSON"

# VALIDATION_RUN_ID="$(
#   require_json_field \
#     "$PIPELINE_JSON" \
#     "$(pipeline_validation_field_expr "validation_run_id")" \
#     'validation_run_id'
# )"

INFERENCE_RUN_ID="$(
  require_json_field \
    "$PIPELINE_JSON" \
    "$(pipeline_inference_field_expr "inference_run_id")" \
    'inference_run_id'
)"

EVALUATION_RUN_ID="$(
  require_json_field \
    "$PIPELINE_JSON" \
    "$(pipeline_evaluation_field_expr "evaluation_run_id")" \
    'evaluation_run_id'
)"

# echo "✅ validation_run_id=$VALIDATION_RUN_ID"
echo "✅ inference_run_id=$INFERENCE_RUN_ID"
echo "✅ evaluation_run_id=$EVALUATION_RUN_ID"

# echo "🔎 Fetching validation run..."
# VALIDATION_RUN_JSON="$(fetch_validation_run "$API_BASE_URL" "$VALIDATION_RUN_ID")"
# echo "$VALIDATION_RUN_JSON" | jq .

# assert_validation_run_ready "$VALIDATION_RUN_JSON"

echo "✅ Mock detection validation E2E passed"
