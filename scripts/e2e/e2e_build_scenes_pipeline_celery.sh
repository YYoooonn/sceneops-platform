#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
DATASET_ID="${DATASET_ID:-nuscenes}"
DATASET_VERSION="${DATASET_VERSION:-v1.0-mini}"
SOURCE_URI="${SOURCE_URI:-/data/raw/nuscenes}"
MAX_SCENES="${MAX_SCENES:-3}"
WINDOW_SECONDS="${WINDOW_SECONDS:-20}"

echo "Running scene building pipeline E2E with Celery"
echo "API_BASE_URL=$API_BASE_URL"
echo "API_PREFIX=${API_PREFIX:-/api/v1}"
echo "DATASET_ID=$DATASET_ID"
echo "DATASET_VERSION=$DATASET_VERSION"
echo "SOURCE_URI=$SOURCE_URI"
echo "MAX_SCENES=$MAX_SCENES / WINDOW_SECONDS=$WINDOW_SECONDS"

# ── 1. Create pipeline run ────────────────────────────────────────────────────

PAYLOAD="$(
  cat <<JSON
{
  "type": "scene_building",
  "datasetId": "$DATASET_ID",
  "datasetVersion": "$DATASET_VERSION",
  "params": {
    "build_scenes": {
      "sourceUri": "$SOURCE_URI",
      "datasetType": "nuscenes",
      "sourceFormat": "nuscenes",
      "maxScenes": $MAX_SCENES,
      "writeDatasetManifest": true,
      "policy": {
        "type": "fixed_window",
        "windowSeconds": $WINDOW_SECONDS,
        "strideSeconds": $WINDOW_SECONDS,
        "requiredChannels": ["CAM_FRONT", "LIDAR_TOP"],
        "maxTimestampGapMs": 500,
        "minFrameCount": 2,
        "splitOnMissingRequiredChannel": true
      }
    },
    "validate": {
      "validateSamples": true,
      "requireTargetChannels": ["CAM_FRONT", "LIDAR_TOP"]
    }
  }
}
JSON
)"

echo "Creating pipeline run..."
CREATE_RESPONSE="$(create_pipeline_run "$API_BASE_URL" "$PAYLOAD")"
echo "$CREATE_RESPONSE" | jq .

PIPELINE_RUN_ID="$(extract_pipeline_run_id "$CREATE_RESPONSE")"
echo "pipeline_run_id=$PIPELINE_RUN_ID"

# ── 2. Dispatch ───────────────────────────────────────────────────────────────

echo "Dispatching pipeline run..."
DISPATCH_RESPONSE="$(dispatch_pipeline_run "$API_BASE_URL" "$PIPELINE_RUN_ID")"
echo "$DISPATCH_RESPONSE" | jq .

# ── 3. Wait for completion (indexing + segmenting can take a while) ───────────

echo "Waiting for pipeline terminal state (build_scenes may take ~5 min)..."
PIPELINE_JSON="$(poll_pipeline_terminal "$API_BASE_URL" "$PIPELINE_RUN_ID" 60 10)"
echo "$PIPELINE_JSON" | jq .

assert_pipeline_succeeded "$PIPELINE_JSON" \
  'scene building pipeline should succeed'

# ── 4. Verify build_scenes output ────────────────────────────────────────────

echo "Checking build_scenes output..."

RAW_LOG_ID="$(
  require_json_field \
    "$PIPELINE_JSON" \
    "$(pipeline_output_field_expr "buildScenes" "rawLogId")" \
    'buildScenes.raw_log_id'
)"
echo "raw_log_id=$RAW_LOG_ID"

assert_json_not_empty "$PIPELINE_JSON" \
  "$(pipeline_output_field_expr "buildScenes" "rawLogManifestUri")" \
  'buildScenes.raw_log_manifest_uri'

assert_json_not_empty "$PIPELINE_JSON" \
  "$(pipeline_output_field_expr "buildScenes" "sceneSegmentsUri")" \
  'buildScenes.sceneSegmentsUri'

BUILT_SCENE_COUNT="$(
  require_json_field \
    "$PIPELINE_JSON" \
    "$(pipeline_output_field_expr "buildScenes" "sceneCount")" \
    'buildScenes.sceneCount'
)"
echo "scene_count=$BUILT_SCENE_COUNT"

BUILT_SAMPLE_COUNT="$(
  require_json_field \
    "$PIPELINE_JSON" \
    "$(pipeline_output_field_expr "buildScenes" "sampleCount")" \
    'buildScenes.sampleCount'
)"
echo "sample_count=$BUILT_SAMPLE_COUNT"

# ── 5. Verify validation output ──────────────────────────────────────────────

echo "Checking validation output..."
assert_validation_ready_from_pipeline "$PIPELINE_JSON"

VALIDATION_RUN_ID="$(
  require_json_field \
    "$PIPELINE_JSON" \
    "$(pipeline_validation_field_expr "validation_run_id")" \
    'validation_run_id'
)"
echo "validation_run_id=$VALIDATION_RUN_ID"

# ── 6. Verify raw-log API endpoint ───────────────────────────────────────────

echo "Verifying raw-log API endpoint..."
RAW_LOG_RESPONSE="$(
  curl -sS "$(api_url "$API_BASE_URL" "/datasets/$DATASET_ID/versions/$DATASET_VERSION/raw-log")"
)"
echo "$RAW_LOG_RESPONSE" | jq .

assert_json_not_empty "$RAW_LOG_RESPONSE" '.rawLogId // .raw_log_id' \
  'raw-log endpoint: rawLogId'

assert_json_not_empty "$RAW_LOG_RESPONSE" '.frameCount // .frame_count' \
  'raw-log endpoint: frameCount'

assert_json_not_empty "$RAW_LOG_RESPONSE" '.channels' \
  'raw-log endpoint: channels'

# ── 7. Verify scene-segments API endpoint ────────────────────────────────────

echo "Verifying scene-segments API endpoint..."
SEGMENTS_RESPONSE="$(
  curl -sS "$(api_url "$API_BASE_URL" "/datasets/$DATASET_ID/versions/$DATASET_VERSION/scene-segments")"
)"
echo "$SEGMENTS_RESPONSE" | jq .

assert_json_not_empty "$SEGMENTS_RESPONSE" '.total // .count' \
  'scene-segments endpoint: total'

SEGMENT_COUNT="$(echo "$SEGMENTS_RESPONSE" | jq -r '.total // .count')"
echo "segment_count=$SEGMENT_COUNT"

# Verify at least one segment has quality_summary
assert_json_not_empty "$SEGMENTS_RESPONSE" \
  '(.segments // [])[0].qualitySummary // (.segments // [])[0].quality_summary' \
  'first segment quality_summary'

# ── 8. Spot-check valid_only filter ──────────────────────────────────────────

VALID_SEGMENTS_RESPONSE="$(
  curl -sS "$(api_url "$API_BASE_URL" "/datasets/$DATASET_ID/versions/$DATASET_VERSION/scene-segments?valid_only=true")"
)"
VALID_COUNT="$(echo "$VALID_SEGMENTS_RESPONSE" | jq -r '.total // 0')"
echo "valid_only segment_count=$VALID_COUNT"

echo "Scene building pipeline E2E passed"
echo "  raw_log_id       = $RAW_LOG_ID"
echo "  scene_count      = $BUILT_SCENE_COUNT"
echo "  sample_count     = $BUILT_SAMPLE_COUNT"
echo "  validation_run   = $VALIDATION_RUN_ID"
echo "  api_segments     = $SEGMENT_COUNT (valid: $VALID_COUNT)"
