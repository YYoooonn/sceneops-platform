#!/usr/bin/env bash
# e2e_dataset_scene_ingestion.sh
#
# E2E test for the dataset_scene_ingestion pipeline:
#   ingest_scenes -> register_scene -> validate_scene -> profile_scene
#   -> build_scene_index -> build_dataset_manifest
#
# Usage:
#   bash scripts/e2e/e2e_dataset_scene_ingestion.sh
#
# Env overrides:
#   API_BASE_URL   (default: http://localhost:8000)
#   DATASET_ID     (default: nuscenes)
#   DATASET_VERSION (default: v1.0-mini)
#   SOURCE_ROOT_URI (default: /data/raw/nuscenes)
#   MAX_SOURCE_SCENES     (default: 2)
#   POLL_TIMEOUT   max poll attempts, 5s each (default: 60 = 5 min)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
DATASET_ID="${DATASET_ID:-nuscenes}"
DATASET_VERSION="${DATASET_VERSION:-v1.0-mini}"
SOURCE_ROOT_URI="${SOURCE_ROOT_URI:-/data/raw/nuscenes}"
MAX_SOURCE_SCENES="${MAX_SOURCE_SCENES:-10}"
POLL_TIMEOUT="${POLL_TIMEOUT:-60}"

echo "=== dataset_scene_ingestion E2E ==="
echo "  API_BASE_URL=$API_BASE_URL"
echo "  DATASET_ID=$DATASET_ID  DATASET_VERSION=$DATASET_VERSION"
echo "  SOURCE_ROOT_URI=$SOURCE_ROOT_URI  MAX_SOURCE_SCENES=$MAX_SOURCE_SCENES"
echo ""

# ── 1. Ensure dataset exists ──────────────────────────────────────────────────

echo "--- 1. Upsert dataset ---"
upsert_dataset "$API_BASE_URL" "$DATASET_ID" "nuScenes" | jq '.dataset | {datasetId, status}' 2>/dev/null || true
echo ""

# ── 2. Create pipeline run ────────────────────────────────────────────────────

echo "--- 2. Create pipeline run ---"
PAYLOAD="$(cat <<JSON
{
  "type": "dataset_scene_ingestion",
  "dataset_id": "$DATASET_ID",
  "dataset_version": "$DATASET_VERSION",
  "params": {
    "ingest_scenes": {
      "source_format": "nuscenes",
      "source_root_uri": "$SOURCE_ROOT_URI",
      "max_source_scenes": $MAX_SOURCE_SCENES,
      "mode": "upsert"
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

assert_pipeline_succeeded "$PIPELINE_JSON" 'dataset_scene_ingestion pipeline should succeed'
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

# ── 6b. Assert key task refs ──────────────────────────────────────────────────

echo "--- 6b. Assert task refs ---"

# ingest_scenes: scene_manifest_uris populated
INGEST_URIS="$(echo "$TASKS_JSON" | jq -r '.tasks[] | select(.pipelineTaskId == "ingest_scenes") | .result.refs.scene_manifest_uris | length // 0')"
echo "  ingest_scenes scene_manifest_uris count=$INGEST_URIS"
if [ "${INGEST_URIS:-0}" -lt 1 ]; then
  echo "❌ ingest_scenes: expected scene_manifest_uris" >&2; exit 1
fi

# register_scene: registered_scene_count > 0
REG_COUNT="$(echo "$TASKS_JSON" | jq -r '.tasks[] | select(.pipelineTaskId == "register_scene") | .result.summary.registered_scene_count // 0')"
echo "  register_scene registered_scene_count=$REG_COUNT"
if [ "${REG_COUNT:-0}" -lt 1 ]; then
  echo "❌ register_scene: expected registered_scene_count > 0" >&2; exit 1
fi

# build_scene_index: scene_index_uri exists
SCENE_INDEX_URI="$(echo "$TASKS_JSON" | jq -r '.tasks[] | select(.pipelineTaskId == "build_scene_index") | .result.artifacts.scene_index_uri // empty')"
echo "  build_scene_index scene_index_uri=$SCENE_INDEX_URI"
if [ -z "$SCENE_INDEX_URI" ]; then
  echo "❌ build_scene_index: expected scene_index_uri" >&2; exit 1
fi

# build_dataset_manifest: dataset_manifest_uri exists
MANIFEST_URI_TASK="$(echo "$TASKS_JSON" | jq -r '.tasks[] | select(.pipelineTaskId == "build_dataset_manifest") | .result.refs.dataset_manifest_uri // empty')"
echo "  build_dataset_manifest dataset_manifest_uri=$MANIFEST_URI_TASK"
if [ -z "$MANIFEST_URI_TASK" ]; then
  echo "❌ build_dataset_manifest: expected dataset_manifest_uri" >&2; exit 1
fi
echo "  OK"
echo ""

# ── 7. Assert scenes created ─────────────────────────────────────────────────

echo "--- 7. Assert scenes ---"
SCENES_JSON="$(curl -sS "$(api_url "$API_BASE_URL" "/scenes?dataset_id=$DATASET_ID&dataset_version=$DATASET_VERSION")")"
SCENE_COUNT="$(echo "$SCENES_JSON" | jq '.scenes | length')"
echo "  scene_count=$SCENE_COUNT"
echo "$SCENES_JSON" | jq -r '.scenes[] | "  \(.sceneId)  status=\(.status)"'

if [ "$SCENE_COUNT" -lt 1 ]; then
  echo "❌ Expected at least 1 scene, got 0" >&2
  exit 1
fi
echo "  OK"
echo ""

# ── 8. Assert dataset version ready ──────────────────────────────────────────

echo "--- 8. Assert dataset version ---"
VERSION_JSON="$(curl -sS "$(api_url "$API_BASE_URL" "/datasets/$DATASET_ID/versions/$DATASET_VERSION")")"
VERSION_STATUS="$(echo "$VERSION_JSON" | jq -r '.version.status')"
MANIFEST_URI="$(echo "$VERSION_JSON" | jq -r '.version.manifestUri // empty')"
VERSION_SCENE_COUNT="$(echo "$VERSION_JSON" | jq -r '.version.sceneCount // 0')"
SAMPLE_COUNT="$(echo "$VERSION_JSON" | jq -r '.version.sampleCount // 0')"

echo "  status=$VERSION_STATUS"
echo "  sceneCount=$VERSION_SCENE_COUNT  sampleCount=$SAMPLE_COUNT"
echo "  manifestUri=$MANIFEST_URI"

assert_json_equals "$VERSION_JSON" '.version.status' 'ready' 'dataset version should be ready'
assert_json_not_empty "$VERSION_JSON" '.version.manifestUri' 'dataset version manifestUri'
echo "  OK"
echo ""

# ── 9. Assert dataset manifest artifact ──────────────────────────────────────

echo "--- 9. Assert artifacts ---"
ARTIFACTS_JSON="$(curl -sS "$(api_url "$API_BASE_URL" "/artifacts?owner_type=dataset_version&owner_id=$DATASET_ID:$DATASET_VERSION")")"
MANIFEST_ARTIFACT_COUNT="$(echo "$ARTIFACTS_JSON" | jq '[.artifacts[] | select(.kind == "dataset_manifest")] | length')"
echo "  dataset_manifest artifacts=$MANIFEST_ARTIFACT_COUNT"

if [ "$MANIFEST_ARTIFACT_COUNT" -lt 1 ]; then
  echo "❌ Expected at least 1 dataset_manifest artifact" >&2
  exit 1
fi
echo "  OK"
echo ""

# ── Summary ───────────────────────────────────────────────────────────────────

echo "=== PASSED ==="
echo "  pipeline_run_id=$PIPELINE_RUN_ID"
echo "  scenes=$SCENE_COUNT  samples=$SAMPLE_COUNT"
echo "  manifest_uri=$MANIFEST_URI"
