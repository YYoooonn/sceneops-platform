#!/usr/bin/env bash
# e2e_raw_log_scene_building_time_window_celery.sh
#
# E2E test for the raw_log_scene_building pipeline using fixed_window segmentation
# and time_bucket sampling. Proves that RawSceneBuilder can reconstruct scenes
# from raw timestamps independently of nuScenes source scene/sample structure.
#
# A single nuScenes source scene (~20s) is split into multiple 2-second SceneOps
# scene segments, demonstrating that fixed_window does not copy nuScenes structure.
#
# Usage:
#   bash scripts/e2e/e2e_raw_log_scene_building_time_window_celery.sh
#
# Env overrides:
#   API_BASE_URL      (default: http://localhost:8000)
#   DATASET_ID        (default: nuscenes)
#   DATASET_VERSION   (default: v1.0-mini)
#   SOURCE_ROOT_URI   (default: /data/raw/nuscenes)
#   MAX_SEQUENCES     (default: 5)
#   POLL_TIMEOUT      max poll attempts, 5s each (default: 60 = 5 min)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
DATASET_ID="${DATASET_ID:-nuscenes}"
DATASET_VERSION="${DATASET_VERSION:-v1.0-mini}"
SOURCE_ROOT_URI="${SOURCE_ROOT_URI:-/data/raw/nuscenes}"
MAX_SEQUENCES="${MAX_SEQUENCES:-1}"
POLL_TIMEOUT="${POLL_TIMEOUT:-60}"

echo "=== raw_log_scene_building (fixed_window + time_bucket) E2E ==="
echo "  API_BASE_URL=$API_BASE_URL"
echo "  DATASET_ID=$DATASET_ID  DATASET_VERSION=$DATASET_VERSION"
echo "  SOURCE_ROOT_URI=$SOURCE_ROOT_URI  MAX_SEQUENCES=$MAX_SEQUENCES"
echo ""

# ── 1. Ensure dataset exists ──────────────────────────────────────────────────

echo "--- 1. Upsert dataset ---"
upsert_dataset "$API_BASE_URL" "$DATASET_ID" "nuScenes" | jq '.dataset | {datasetId, status}' 2>/dev/null || true
echo ""

# ── 2. Create pipeline run ────────────────────────────────────────────────────

echo "--- 2. Create pipeline run ---"
PAYLOAD="$(cat <<JSON
{
  "type": "raw_log_scene_building",
  "dataset_id": "$DATASET_ID",
  "dataset_version": "$DATASET_VERSION",
  "params": {
    "build_scenes": {
      "source_type": "nuscenes_raw_log_mock",
      "source_format": "nuscenes",
      "raw_root_uri": "$SOURCE_ROOT_URI",
      "max_source_sequences": $MAX_SEQUENCES,
      "required_channels": ["CAM_FRONT", "LIDAR_TOP"],
      "segmentation": {
        "strategy": "fixed_window",
        "fixed_window_duration_ms": 10000
      },
      "sampling": {
        "strategy": "time_bucket",
        "sample_time_window_ms": 500,
        "sync_policy": "best_effort",
        "missing_channel_policy": "keep_with_warning",
        "required_channels": ["CAM_FRONT", "LIDAR_TOP"]
      }
    },
    "register_scene": {
      "replace_existing": true
    },
    "validate_scene": {
      "require_target_channels": ["CAM_FRONT", "LIDAR_TOP"],
      "sample_validation": {
        "validate_samples": true,
        "block_on_sample_missing_channels": false
      }
    },
    "profile_scene": {
      "include_annotation_stats": true,
      "include_sensor_coverage": true
    },
    "build_scene_index": {},
    "build_dataset_manifest": {}
  }
}
JSON
)"

CREATE_RESP="$(create_pipeline_run "$API_BASE_URL" "$PAYLOAD")"
PIPELINE_RUN_ID="$(extract_pipeline_run_id "$CREATE_RESP")"
echo "  pipeline_run_id=$PIPELINE_RUN_ID"
echo ""

# ── 3. Execute ────────────────────────────────────────────────────────────────

echo "--- 3. Dispatch ---"
EXEC_RESP="$(dispatch_pipeline_run "$API_BASE_URL" "$PIPELINE_RUN_ID")"
EXEC_STATUS="$(echo "$EXEC_RESP" | jq -r '.execution.status // "error"')"
echo "  execution status=$EXEC_STATUS"
if [ "$EXEC_STATUS" = "error" ]; then
  echo "$EXEC_RESP" | jq . >&2
  exit 1
fi
echo ""

# ── 4. Poll ───────────────────────────────────────────────────────────────────

echo "--- 4. Polling (up to $((POLL_TIMEOUT * 5))s) ---"
PIPELINE_JSON="$(poll_pipeline_terminal "$API_BASE_URL" "$PIPELINE_RUN_ID" "$POLL_TIMEOUT" 5)"
echo ""

# ── 5. Assert pipeline succeeded ─────────────────────────────────────────────

echo "--- 5. Assert pipeline ---"
FINAL_STATUS="$(echo "$PIPELINE_JSON" | jq -r '.pipelineRun.status')"
echo "  status=$FINAL_STATUS"

if [ "$FINAL_STATUS" = "failed" ]; then
  echo "  error=$(echo "$PIPELINE_JSON" | jq -r '.pipelineRun.error.message // "unknown"')"
fi

assert_pipeline_succeeded "$PIPELINE_JSON" 'raw_log_scene_building (fixed_window+time_bucket) pipeline should succeed'
echo "  OK"
echo ""

# ── 6. Assert all steps succeeded ────────────────────────────────────────────

echo "--- 6. Assert steps ---"
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

# ── 7. Assert build_scenes result ─────────────────────────────────────────────

echo "--- 7. Assert build_scenes ---"

BUILD_TASK="$(echo "$TASKS_JSON" | jq '.tasks[] | select(.pipelineTaskId == "build_scenes")')"

SEGMENT_INDEX_URI="$(echo "$BUILD_TASK" | jq -r '.result.refs.scene_segment_index_uri // empty')"
SCENE_MANIFEST_URIS_COUNT="$(echo "$BUILD_TASK" | jq -r '.result.refs.scene_manifest_uris | length // 0')"
SCENE_COUNT="$(echo "$BUILD_TASK" | jq -r '.result.summary.scene_count // 0')"
SAMPLE_COUNT="$(echo "$BUILD_TASK" | jq -r '.result.summary.sample_count // 0')"
FRAME_COUNT="$(echo "$BUILD_TASK" | jq -r '.result.summary.frame_count // 0')"
SEG_STRATEGY="$(echo "$BUILD_TASK" | jq -r '.result.summary.segmentation_strategy // empty')"
SAMP_STRATEGY="$(echo "$BUILD_TASK" | jq -r '.result.summary.sampling_strategy // empty')"

echo "  scene_segment_index_uri=$SEGMENT_INDEX_URI"
echo "  scene_manifest_uris count=$SCENE_MANIFEST_URIS_COUNT"
echo "  scene_count=$SCENE_COUNT"
echo "  sample_count=$SAMPLE_COUNT"
echo "  frame_count=$FRAME_COUNT"
echo "  segmentation_strategy=$SEG_STRATEGY"
echo "  sampling_strategy=$SAMP_STRATEGY"

[ -n "$SEGMENT_INDEX_URI" ] || { echo "❌ build_scenes: missing scene_segment_index_uri" >&2; exit 1; }
[ "${SCENE_MANIFEST_URIS_COUNT:-0}" -ge 1 ] || { echo "❌ build_scenes: expected scene_manifest_uris" >&2; exit 1; }
[ "${SCENE_COUNT:-0}" -gt 1 ] || { echo "❌ build_scenes: expected scene_count > 1 (fixed_window should produce multiple segments), got $SCENE_COUNT" >&2; exit 1; }
[ "${SAMPLE_COUNT:-0}" -gt 0 ] || { echo "❌ build_scenes: expected sample_count > 0" >&2; exit 1; }
[ "${FRAME_COUNT:-0}" -gt 0 ]  || { echo "❌ build_scenes: expected frame_count > 0" >&2; exit 1; }

if [ -n "$SEG_STRATEGY" ]; then
  [ "$SEG_STRATEGY" = "fixed_window" ] || { echo "❌ build_scenes: expected segmentation_strategy=fixed_window, got $SEG_STRATEGY" >&2; exit 1; }
fi
if [ -n "$SAMP_STRATEGY" ]; then
  [ "$SAMP_STRATEGY" = "time_bucket" ] || { echo "❌ build_scenes: expected sampling_strategy=time_bucket, got $SAMP_STRATEGY" >&2; exit 1; }
fi
echo "  OK"
echo ""

# ── 8. Assert register_scene refs ────────────────────────────────────────────

echo "--- 8. Assert register_scene ---"
REG_COUNT="$(echo "$TASKS_JSON" | jq -r '.tasks[] | select(.pipelineTaskId == "register_scene") | .result.summary.registered_scene_count // 0')"
echo "  registered_scene_count=$REG_COUNT"
[ "${REG_COUNT:-0}" -gt 0 ] || { echo "❌ register_scene: expected registered_scene_count > 0" >&2; exit 1; }

# registered_scene_count should match scene_count from build_scenes
if [ "${SCENE_COUNT:-0}" -gt 0 ] && [ "${REG_COUNT:-0}" -gt 0 ]; then
  [ "$REG_COUNT" -eq "$SCENE_COUNT" ] || { echo "❌ register_scene: registered_scene_count ($REG_COUNT) != scene_count ($SCENE_COUNT)" >&2; exit 1; }
fi
echo "  OK"
echo ""

# ── 9. Assert build_scene_index refs ─────────────────────────────────────────

echo "--- 9. Assert build_scene_index ---"
SCENE_INDEX_URI="$(echo "$TASKS_JSON" | jq -r '.tasks[] | select(.pipelineTaskId == "build_scene_index") | .result.refs.scene_index_uri // empty')"
echo "  scene_index_uri=$SCENE_INDEX_URI"
[ -n "$SCENE_INDEX_URI" ] || { echo "❌ build_scene_index: missing scene_index_uri" >&2; exit 1; }
echo "  OK"
echo ""

# ── 10. Assert build_dataset_manifest refs ───────────────────────────────────

echo "--- 10. Assert build_dataset_manifest ---"
MANIFEST_URI_TASK="$(echo "$TASKS_JSON" | jq -r '.tasks[] | select(.pipelineTaskId == "build_dataset_manifest") | .result.refs.dataset_manifest_uri // empty')"
echo "  dataset_manifest_uri=$MANIFEST_URI_TASK"
[ -n "$MANIFEST_URI_TASK" ] || { echo "❌ build_dataset_manifest: missing dataset_manifest_uri" >&2; exit 1; }
echo "  OK"
echo ""

# ── 11. Assert validate_scene did not block ───────────────────────────────────

echo "--- 11. Assert validate_scene ---"
SHOULD_BLOCK="$(echo "$TASKS_JSON" | jq -r '.tasks[] | select(.pipelineTaskId == "validate_scene") | .result.summary.should_block_pipeline // "false"')"
echo "  should_block_pipeline=$SHOULD_BLOCK"
[ "$SHOULD_BLOCK" = "false" ] || { echo "❌ validate_scene: should_block_pipeline is not false" >&2; exit 1; }
echo "  OK"
echo ""

# ── 12. Assert scenes registered in DB ───────────────────────────────────────

echo "--- 12. Assert scenes in DB ---"
SCENES_JSON="$(curl -sS "$(api_url "$API_BASE_URL" "/scenes?dataset_id=$DATASET_ID&dataset_version=$DATASET_VERSION")")"
DB_SCENE_COUNT="$(echo "$SCENES_JSON" | jq '.scenes | length')"
echo "  scene_count=$DB_SCENE_COUNT"
echo "$SCENES_JSON" | jq -r '.scenes[] | "  \(.sceneId)  status=\(.status)"' 2>/dev/null || true

[ "$DB_SCENE_COUNT" -ge 1 ] || { echo "❌ Expected at least 1 scene in DB, got 0" >&2; exit 1; }
echo "  OK"
echo ""

# ── Summary ───────────────────────────────────────────────────────────────────

echo "=== PASSED ==="
echo "  pipeline_run_id=$PIPELINE_RUN_ID"
echo "  segmentation=fixed_window  sampling=time_bucket"
echo "  scene_count=$SCENE_COUNT  sample_count=$SAMPLE_COUNT  frame_count=$FRAME_COUNT"
echo "  scene_index_uri=$SCENE_INDEX_URI"
echo "  dataset_manifest_uri=$MANIFEST_URI_TASK"
