#!/usr/bin/env bash
# e2e_raw_log_scene_building.sh
#
# E2E test for the raw_log_scene_building pipeline:
#   build_scenes -> register_scene -> validate_scene -> profile_scene
#   -> build_scene_index -> build_dataset_manifest
#
# Uses NuScenesRawLogMocker to flatten nuScenes into mock raw sensor frames.
#
# Usage:
#   bash scripts/e2e/e2e_raw_log_scene_building.sh
#
# Env overrides:
#   API_BASE_URL      (default: http://localhost:8000)
#   DATASET_ID        (default: nuscenes)
#   DATASET_VERSION   (default: v1.0-mini)
#   SOURCE_ROOT_URI   (default: /data/raw/nuscenes)
#   MAX_SEQUENCES     (default: 10)
#   POLL_TIMEOUT      max poll attempts, 5s each (default: 60 = 5 min)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
DATASET_ID="${DATASET_ID:-nuscenes}"
DATASET_VERSION="${DATASET_VERSION:-v1.0-mini}"
SOURCE_ROOT_URI="${SOURCE_ROOT_URI:-/data/raw/nuscenes}"
MAX_SEQUENCES="${MAX_SEQUENCES:-10}"
POLL_TIMEOUT="${POLL_TIMEOUT:-60}"

echo "=== raw_log_scene_building E2E ==="
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
      "segmentation": {
        "strategy": "sequence"
      },
      "sampling": {
        "strategy": "frame_id",
        "sync_policy": "best_effort",
        "missing_channel_policy": "keep_with_warning",
        "required_channels": ["CAM_FRONT", "LIDAR_TOP"]
      }
    },
    "register_scene": {
      "replace_existing": true
    },
    "validate_scene": {
      "require_target_channels": ["CAM_FRONT", "LIDAR_TOP"]
    },
    "profile_scene": {
      "profile_samples": true,
      "profile_assets": true
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

assert_pipeline_succeeded "$PIPELINE_JSON" 'raw_log_scene_building pipeline should succeed'
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

# ── 7. Assert build_scenes refs ──────────────────────────────────────────────

echo "--- 7. Assert build_scenes refs ---"

BUILD_TASK="$(echo "$TASKS_JSON" | jq '.tasks[] | select(.pipelineTaskId == "build_scenes")')"

RAW_LOG_MANIFEST_URI="$(echo "$BUILD_TASK" | jq -r '.result.artifacts.raw_log_manifest_uri // empty')"
RAW_FRAME_INDEX_URI="$(echo "$BUILD_TASK" | jq -r '.result.artifacts.raw_log_frame_index_uri // empty')"
SEGMENT_INDEX_URI="$(echo "$BUILD_TASK" | jq -r '.result.artifacts.scene_segment_index_uri // empty')"
SCENE_MANIFEST_URIS_COUNT="$(echo "$BUILD_TASK" | jq -r '.result.refs.scene_manifest_uris | length // 0')"
OBS_COUNT="$(echo "$BUILD_TASK" | jq -r '.result.summary.observation_count // 0')"

echo "  raw_log_manifest_uri=$RAW_LOG_MANIFEST_URI"
echo "  raw_log_frame_index_uri=$RAW_FRAME_INDEX_URI"
echo "  scene_segment_index_uri=$SEGMENT_INDEX_URI"
echo "  scene_manifest_uris count=$SCENE_MANIFEST_URIS_COUNT"
echo "  observation_count=$OBS_COUNT"

[ -n "$RAW_LOG_MANIFEST_URI" ] || { echo "❌ build_scenes: missing raw_log_manifest_uri" >&2; exit 1; }
[ -n "$RAW_FRAME_INDEX_URI" ]  || { echo "❌ build_scenes: missing raw_log_frame_index_uri" >&2; exit 1; }
[ -n "$SEGMENT_INDEX_URI" ]    || { echo "❌ build_scenes: missing scene_segment_index_uri" >&2; exit 1; }
[ "${SCENE_MANIFEST_URIS_COUNT:-0}" -ge 1 ] || { echo "❌ build_scenes: expected scene_manifest_uris" >&2; exit 1; }
[ "${OBS_COUNT:-0}" -gt 0 ]    || { echo "❌ build_scenes: expected observation_count > 0" >&2; exit 1; }
echo "  OK"
echo ""

# ── 8. Assert register_scene refs ────────────────────────────────────────────

echo "--- 8. Assert register_scene refs ---"
REG_COUNT="$(echo "$TASKS_JSON" | jq -r '.tasks[] | select(.pipelineTaskId == "register_scene") | .result.summary.registered_scene_count // 0')"
echo "  registered_scene_count=$REG_COUNT"
[ "${REG_COUNT:-0}" -ge 1 ] || { echo "❌ register_scene: expected registered_scene_count > 0" >&2; exit 1; }
echo "  OK"
echo ""

# ── 9. Assert build_scene_index refs ─────────────────────────────────────────

echo "--- 9. Assert build_scene_index refs ---"
SCENE_INDEX_URI="$(echo "$TASKS_JSON" | jq -r '.tasks[] | select(.pipelineTaskId == "build_scene_index") | .result.artifacts.scene_index_uri // empty')"
echo "  scene_index_uri=$SCENE_INDEX_URI"
[ -n "$SCENE_INDEX_URI" ] || { echo "❌ build_scene_index: missing scene_index_uri" >&2; exit 1; }
echo "  OK"
echo ""

# ── 10. Assert build_dataset_manifest refs ───────────────────────────────────

echo "--- 10. Assert build_dataset_manifest refs ---"
MANIFEST_URI_TASK="$(echo "$TASKS_JSON" | jq -r '.tasks[] | select(.pipelineTaskId == "build_dataset_manifest") | .result.refs.dataset_manifest_uri // empty')"
echo "  dataset_manifest_uri=$MANIFEST_URI_TASK"
[ -n "$MANIFEST_URI_TASK" ] || { echo "❌ build_dataset_manifest: missing dataset_manifest_uri" >&2; exit 1; }
echo "  OK"
echo ""

# ── 11. Assert scenes registered in DB ───────────────────────────────────────

echo "--- 11. Assert scenes ---"
SCENES_JSON="$(curl -sS "$(api_url "$API_BASE_URL" "/scenes?dataset_id=$DATASET_ID&dataset_version=$DATASET_VERSION")")"
SCENE_COUNT="$(echo "$SCENES_JSON" | jq '.scenes | length')"
echo "  scene_count=$SCENE_COUNT"
echo "$SCENES_JSON" | jq -r '.scenes[] | "  \(.sceneId)  status=\(.status)"' 2>/dev/null || true

[ "$SCENE_COUNT" -ge 1 ] || { echo "❌ Expected at least 1 scene in DB, got 0" >&2; exit 1; }
echo "  OK"
echo ""

# ── Summary ───────────────────────────────────────────────────────────────────

echo "=== PASSED ==="
echo "  pipeline_run_id=$PIPELINE_RUN_ID"
echo "  scenes=$SCENE_COUNT"
echo "  scene_index_uri=$SCENE_INDEX_URI"
echo "  dataset_manifest_uri=$MANIFEST_URI_TASK"
