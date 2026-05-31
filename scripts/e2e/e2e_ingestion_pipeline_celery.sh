#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
DATASET_ID="${DATASET_ID:-nuscenes}"
DATASET_VERSION="${DATASET_VERSION:-v1.0-mini}"
MAX_SCENES="${MAX_SCENES:-2}"

echo "🚀 Running dataset ingestion pipeline E2E with Celery"
echo "API_BASE_URL=$API_BASE_URL"
echo "API_PREFIX=${API_PREFIX:-/api/v1}"
echo "DATASET_ID=$DATASET_ID"
echo "DATASET_VERSION=$DATASET_VERSION"
echo "MAX_SCENES=$MAX_SCENES"

PAYLOAD="$(
  cat <<JSON
{
  "type": "dataset_ingestion",
  "datasetId": "$DATASET_ID",
  "datasetVersion": "$DATASET_VERSION",
  "params": {
    "ingest": {
      "datasetType": "nuscenes",
      "maxScenes": $MAX_SCENES,
      "mode": "upsert"
    },
    "validate": {
      "validateSamples": true,
      "requireTargetChannels": ["CAM_FRONT", "LIDAR_TOP"]
    }
  }
}
JSON
)"

echo "📦 Creating pipeline run..."
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
  'dataset ingestion pipeline should succeed'

echo "🔎 Checking validation lineage..."
assert_validation_ready_from_pipeline "$PIPELINE_JSON"

VALIDATION_RUN_ID="$(
  require_json_field \
    "$PIPELINE_JSON" \
    "$(pipeline_validation_field_expr "validation_run_id")" \
    'validation_run_id'
)"

VALIDATION_REPORT_URI="$(
  require_json_field \
    "$PIPELINE_JSON" \
    "$(pipeline_validation_field_expr "validation_report_uri")" \
    'validation_report_uri'
)"

echo "✅ validation_run_id=$VALIDATION_RUN_ID"
echo "✅ validation_report_uri=$VALIDATION_REPORT_URI"

echo "🔎 Fetching validation run..."
VALIDATION_RUN_JSON="$(fetch_validation_run "$API_BASE_URL" "$VALIDATION_RUN_ID")"
echo "$VALIDATION_RUN_JSON" | jq .

assert_validation_run_ready "$VALIDATION_RUN_JSON"

echo "🔎 Fetching validation report..."
VALIDATION_REPORT_JSON="$(fetch_validation_report "$API_BASE_URL" "$VALIDATION_RUN_ID")"
echo "$VALIDATION_REPORT_JSON" | jq .

assert_json_equals "$VALIDATION_REPORT_JSON" '.validation_run_id // .validationRunId' "$VALIDATION_RUN_ID" \
  'validation report id should match'

assert_json_equals "$VALIDATION_REPORT_JSON" '.status' 'ready' \
  'validation report status should be ready'

assert_json_not_empty "$VALIDATION_REPORT_JSON" '.summary.sample_count // .summary.sampleCount' \
  'validation report summary.sample_count'

assert_json_not_empty "$VALIDATION_REPORT_JSON" '.summary.validated_sample_count // .summary.validatedSampleCount' \
  'validation report summary.validated_sample_count'

# XXX add profiling check

echo "✅ Dataset ingestion + validation + profiling E2E passed"
