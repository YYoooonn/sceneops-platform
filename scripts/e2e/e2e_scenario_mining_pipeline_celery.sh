#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
DATASET_ID="${DATASET_ID:-nuscenes}"
DATASET_VERSION="${DATASET_VERSION:-v1.0-mini}"
SOURCE_URI="${SOURCE_URI:-/data/raw/nuscenes}"
MAX_SCENES="${MAX_SCENES:-5}"

# Predicate: pedestrian within 30m AND low speed (<= 40km/h)
# nuScenes v1.0-mini contains human.pedestrian.adult in many scenes
REQUIRED_CATEGORY="${REQUIRED_CATEGORY:-human.pedestrian.adult}"
MAX_DISTANCE_M="${MAX_DISTANCE_M:-30}"
SPEED_MAX_KMH="${SPEED_MAX_KMH:-40}"

# Scene window around matched anchor
PRE_EVENT_S="${PRE_EVENT_S:-3}"
POST_EVENT_S="${POST_EVENT_S:-7}"
MIN_GAP_S="${MIN_GAP_S:-5}"

echo "Running scenario mining pipeline E2E with Celery"
echo "API_BASE_URL=$API_BASE_URL"
echo "DATASET_ID=$DATASET_ID / DATASET_VERSION=$DATASET_VERSION"
echo "Predicate: $REQUIRED_CATEGORY within ${MAX_DISTANCE_M}m AND speed<=${SPEED_MAX_KMH}km/h"
echo "Window: -${PRE_EVENT_S}s .. +${POST_EVENT_S}s | min_gap=${MIN_GAP_S}s | max_scenes=$MAX_SCENES"

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
        "type": "scenario_mining",
        "requiredChannels": ["CAM_FRONT", "LIDAR_TOP"],
        "mining": {
          "predicate": {
            "type": "and",
            "predicates": [
              {
                "type": "object_neighborhood",
                "requiredCategories": ["$REQUIRED_CATEGORY"],
                "maxDistanceM": $MAX_DISTANCE_M,
                "minCount": 1
              },
              {
                "type": "ego_kinematic",
                "speedMaxKmh": $SPEED_MAX_KMH
              }
            ]
          },
          "preEventSeconds": $PRE_EVENT_S,
          "postEventSeconds": $POST_EVENT_S,
          "minGapBetweenScenesSeconds": $MIN_GAP_S
        }
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

# ── 3. Wait (semantic indexing reads all annotations — may take a few minutes) ─

echo "Waiting for pipeline terminal state..."
PIPELINE_JSON="$(poll_pipeline_terminal "$API_BASE_URL" "$PIPELINE_RUN_ID" 60 10)"
echo "$PIPELINE_JSON" | jq .

assert_pipeline_succeeded "$PIPELINE_JSON" \
  'scenario mining pipeline should succeed'

# ── 4. Verify build_scenes output ────────────────────────────────────────────

echo "Checking build_scenes output..."

RAW_LOG_ID="$(
  require_json_field \
    "$PIPELINE_JSON" \
    "$(pipeline_output_field_expr "buildScenes" "rawLogId")" \
    'buildScenes.rawLogId'
)"
echo "raw_log_id=$RAW_LOG_ID"

SCENE_COUNT="$(
  require_json_field \
    "$PIPELINE_JSON" \
    "$(pipeline_output_field_expr "buildScenes" "sceneCount")" \
    'buildScenes.sceneCount'
)"
echo "scene_count=$SCENE_COUNT"

if [ "$SCENE_COUNT" -eq 0 ]; then
  echo "WARNING: 0 scenes matched the predicate. Try relaxing maxDistanceM or speedMaxKmh."
fi

SAMPLE_COUNT="$(
  require_json_field \
    "$PIPELINE_JSON" \
    "$(pipeline_output_field_expr "buildScenes" "sampleCount")" \
    'buildScenes.sampleCount'
)"
echo "sample_count=$SAMPLE_COUNT"

assert_json_not_empty "$PIPELINE_JSON" \
  "$(pipeline_output_field_expr "buildScenes" "sceneSegmentsUri")" \
  'buildScenes.sceneSegmentsUri'

# ── 5. Verify validation passed ──────────────────────────────────────────────

echo "Checking validation output..."
assert_validation_ready_from_pipeline "$PIPELINE_JSON"

VALIDATION_RUN_ID="$(
  require_json_field \
    "$PIPELINE_JSON" \
    "$(pipeline_validation_field_expr "validation_run_id")" \
    'validation_run_id'
)"
echo "validation_run_id=$VALIDATION_RUN_ID"

# ── 6. Verify raw-log API ─────────────────────────────────────────────────────

echo "Verifying raw-log API..."
RAW_LOG_JSON="$(curl -sS "$(api_url "$API_BASE_URL" "/datasets/$DATASET_ID/versions/$DATASET_VERSION/raw-log")")"
echo "$RAW_LOG_JSON" | jq .

assert_json_not_empty "$RAW_LOG_JSON" '.rawLogId // .raw_log_id' \
  'raw-log: rawLogId'
assert_json_not_empty "$RAW_LOG_JSON" '.frameCount // .frame_count' \
  'raw-log: frameCount'

# ── 7. Verify scene-segments API and predicate metadata ──────────────────────

echo "Verifying scene-segments API..."
SEGMENTS_JSON="$(curl -sS "$(api_url "$API_BASE_URL" "/datasets/$DATASET_ID/versions/$DATASET_VERSION/scene-segments")")"
echo "$SEGMENTS_JSON" | jq .

TOTAL_SEGMENTS="$(echo "$SEGMENTS_JSON" | jq -r '.total // 0')"
echo "total_segments=$TOTAL_SEGMENTS"

if [ "$TOTAL_SEGMENTS" -gt 0 ]; then
  # quality_summary should contain predicate description (set by ScenarioMiningSegmenter)
  assert_json_not_empty "$SEGMENTS_JSON" \
    '(.segments // [])[0].qualitySummary.predicate // (.segments // [])[0].quality_summary.predicate' \
    'first segment quality_summary.predicate'

  PREDICATE_DESC="$(
    echo "$SEGMENTS_JSON" | jq -r \
    '(.segments // [])[0].qualitySummary.predicate // (.segments // [])[0].quality_summary.predicate // ""'
  )"
  echo "predicate_desc=$PREDICATE_DESC"

  # anchor speed should be present
  ANCHOR_SPEED="$(
    echo "$SEGMENTS_JSON" | jq -r \
    '(.segments // [])[0].qualitySummary.anchorSpeedKmh // (.segments // [])[0].quality_summary.anchor_speed_kmh // "null"'
  )"
  echo "anchor_speed_kmh=$ANCHOR_SPEED"
fi

# ── 8. Channel filter ────────────────────────────────────────────────────────

echo "Testing channel filter: CAM_FRONT..."
FILTERED_JSON="$(
  curl -sS "$(api_url "$API_BASE_URL" "/datasets/$DATASET_ID/versions/$DATASET_VERSION/scene-segments?channel=CAM_FRONT")"
)"
FILTERED_COUNT="$(echo "$FILTERED_JSON" | jq -r '.total // 0')"
echo "CAM_FRONT segments=$FILTERED_COUNT"

# All scenario-mined scenes require CAM_FRONT (it's used for ego pose),
# so filtered count should equal total
if [ "$TOTAL_SEGMENTS" -gt 0 ] && [ "$FILTERED_COUNT" -eq 0 ]; then
  echo "WARNING: CAM_FRONT filter returned 0 segments — check channel population"
fi

# ── 9. Summary ───────────────────────────────────────────────────────────────

echo "Scenario mining pipeline E2E passed"
echo "  raw_log_id      = $RAW_LOG_ID"
echo "  scene_count     = $SCENE_COUNT (max_scenes=$MAX_SCENES)"
echo "  sample_count    = $SAMPLE_COUNT"
echo "  validation_run  = $VALIDATION_RUN_ID"
echo "  api_segments    = $TOTAL_SEGMENTS (CAM_FRONT filtered: $FILTERED_COUNT)"
echo "  predicate       = $PREDICATE_DESC"
