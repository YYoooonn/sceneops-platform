#!/usr/bin/env bash
# e2e_pipeline_contracts.sh
#
# Validates stabilized pipeline contracts:
#   1. Pipeline definitions API returns only supported+implemented pipelines
#   2. Unsupported pipeline creation is rejected with HTTP 400
#   3. dataset_scene_ingestion pipeline runs end-to-end
#   4. validate_scene result includes validation_run_id and validation_report_uri
#   5. profile_scene result includes profile_run_id and profile_report_uri
#   6. Dataset version quality cache is populated after validation and profiling
#
# Usage:
#   bash scripts/e2e/e2e_pipeline_contracts.sh
#
# Env overrides:
#   API_BASE_URL     (default: http://localhost:8000)
#   DATASET_ID       (default: nuscenes)
#   DATASET_VERSION  (default: v1.0-mini)
#   SOURCE_ROOT_URI  (default: /data/raw/nuscenes)
#   MAX_SOURCE_SCENES (default: 2)
#   POLL_TIMEOUT     max poll attempts, 5s each (default: 60 = 5 min)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
DATASET_ID="${DATASET_ID:-nuscenes}"
DATASET_VERSION="${DATASET_VERSION:-v1.0-mini}"
SOURCE_ROOT_URI="${SOURCE_ROOT_URI:-/data/raw/nuscenes}"
MAX_SOURCE_SCENES="${MAX_SOURCE_SCENES:-2}"
POLL_TIMEOUT="${POLL_TIMEOUT:-60}"

SUPPORTED_PIPELINE_TYPES=(
  "dataset_scene_ingestion"
  "raw_log_scene_building"
  "detection_evaluation"
)

UNSUPPORTED_PIPELINE_TYPES=(
  "scene_reconstruction"
  "scene_registration"
  "scenario_curation"
  "generated_dataset_preparation"
)

echo "=== pipeline contracts E2E ==="
echo "  API_BASE_URL=$API_BASE_URL"
echo "  DATASET_ID=$DATASET_ID  DATASET_VERSION=$DATASET_VERSION"
echo "  SOURCE_ROOT_URI=$SOURCE_ROOT_URI  MAX_SOURCE_SCENES=$MAX_SOURCE_SCENES"
echo ""

# ── 1. Pipeline definitions — only supported+implemented returned ─────────────

echo "--- 1. Verify pipeline definitions ---"
DEFS_JSON="$(curl -sS "$(api_url "$API_BASE_URL" "/pipelines/definitions")")"
DEF_COUNT="$(echo "$DEFS_JSON" | jq '.count')"
echo "  definitions count=$DEF_COUNT (expected ${#SUPPORTED_PIPELINE_TYPES[@]})"

if [ "$DEF_COUNT" -ne "${#SUPPORTED_PIPELINE_TYPES[@]}" ]; then
  echo "❌ Expected exactly ${#SUPPORTED_PIPELINE_TYPES[@]} supported pipeline definitions, got $DEF_COUNT" >&2
  echo "$DEFS_JSON" | jq '.definitions[] | {type, supported, implemented}' >&2
  exit 1
fi

# Verify each supported type is present.
for pipeline_type in "${SUPPORTED_PIPELINE_TYPES[@]}"; do
  MATCH="$(echo "$DEFS_JSON" | jq -r --arg t "$pipeline_type" '.definitions[] | select(.type == $t) | .type // empty')"
  if [ -z "$MATCH" ]; then
    echo "❌ Expected supported pipeline '$pipeline_type' in definitions listing" >&2
    exit 1
  fi
  SUPPORTED="$(echo "$DEFS_JSON" | jq -r --arg t "$pipeline_type" '.definitions[] | select(.type == $t) | .supported')"
  IMPLEMENTED="$(echo "$DEFS_JSON" | jq -r --arg t "$pipeline_type" '.definitions[] | select(.type == $t) | .implemented')"
  echo "  $pipeline_type: supported=$SUPPORTED implemented=$IMPLEMENTED"
done

# Verify no unsupported types appear.
for pipeline_type in "${UNSUPPORTED_PIPELINE_TYPES[@]}"; do
  MATCH="$(echo "$DEFS_JSON" | jq -r --arg t "$pipeline_type" '.definitions[] | select(.type == $t) | .type // empty')"
  if [ -n "$MATCH" ]; then
    echo "❌ Unsupported pipeline '$pipeline_type' must NOT appear in default definitions listing" >&2
    exit 1
  fi
done

echo "  All definitions checks: OK"
echo ""

# ── 2. Unsupported pipeline creation rejected with HTTP 400 ───────────────────

echo "--- 2. Verify unsupported pipeline creation rejected ---"
upsert_dataset "$API_BASE_URL" "$DATASET_ID" "nuScenes" >/dev/null 2>&1 || true

for pipeline_type in "${UNSUPPORTED_PIPELINE_TYPES[@]}"; do
  HTTP_CODE="$(curl -sS -o /dev/null -w "%{http_code}" \
    -X POST "$(api_url "$API_BASE_URL" "/pipelines/runs")" \
    -H "Content-Type: application/json" \
    -d "{\"type\": \"$pipeline_type\", \"dataset_id\": \"$DATASET_ID\", \"dataset_version\": \"$DATASET_VERSION\"}")"

  echo "  POST /pipelines/runs type=$pipeline_type → HTTP $HTTP_CODE"

  if [ "$HTTP_CODE" != "400" ]; then
    echo "❌ Expected HTTP 400 for unsupported pipeline '$pipeline_type', got $HTTP_CODE" >&2
    exit 1
  fi
done

echo "  All unsupported pipeline rejections: OK"
echo ""

# ── 3. Ensure dataset and version exist ───────────────────────────────────────

echo "--- 3. Upsert dataset ---"
upsert_dataset "$API_BASE_URL" "$DATASET_ID" "nuScenes" | jq '.dataset | {datasetId, status}' 2>/dev/null || true
echo ""

# ── 4. Create pipeline run with explicit validate_scene + profile_scene ───────

echo "--- 4. Create pipeline run ---"
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

# ── 5. Dispatch ───────────────────────────────────────────────────────────────

echo "--- 5. Dispatch ---"
EXEC_RESP="$(dispatch_pipeline_run "$API_BASE_URL" "$PIPELINE_RUN_ID")"
EXEC_STATUS="$(echo "$EXEC_RESP" | jq -r '.execution.status // "error"')"
echo "  execution status=$EXEC_STATUS"
if [ "$EXEC_STATUS" = "error" ]; then
  echo "$EXEC_RESP" | jq . >&2
  exit 1
fi
echo ""

# ── 6. Poll to completion ─────────────────────────────────────────────────────

echo "--- 6. Polling (up to $((POLL_TIMEOUT * 5))s) ---"
PIPELINE_JSON="$(poll_pipeline_terminal "$API_BASE_URL" "$PIPELINE_RUN_ID" "$POLL_TIMEOUT" 5)"
echo ""

# ── 7. Assert pipeline succeeded ─────────────────────────────────────────────

echo "--- 7. Assert pipeline succeeded ---"
FINAL_STATUS="$(echo "$PIPELINE_JSON" | jq -r '.pipelineRun.status')"
echo "  status=$FINAL_STATUS"

if [ "$FINAL_STATUS" = "failed" ]; then
  echo "  error=$(echo "$PIPELINE_JSON" | jq -r '.pipelineRun.error.message // "unknown"')"
fi

assert_pipeline_succeeded "$PIPELINE_JSON" 'dataset_scene_ingestion pipeline should succeed'
echo "  OK"
echo ""

# ── 8. Assert task statuses ───────────────────────────────────────────────────

echo "--- 8. Assert task statuses ---"
TASKS_JSON="$(fetch_pipeline_tasks "$API_BASE_URL" "$PIPELINE_RUN_ID")"
echo "$TASKS_JSON" | jq -r '.tasks[] | "  \(.pipelineTaskId): \(.status)"'

# Required tasks must succeed.
for task_id in ingest_scenes register_scene build_scene_index build_dataset_manifest; do
  TASK_STATUS="$(echo "$TASKS_JSON" | jq -r --arg t "$task_id" '.tasks[] | select(.pipelineTaskId == $t) | .status // empty')"
  if [ "$TASK_STATUS" != "succeeded" ]; then
    echo "❌ Required task '$task_id' expected succeeded, got '$TASK_STATUS'" >&2
    exit 1
  fi
done

# validate_scene and profile_scene must succeed (explicit params were provided).
for task_id in validate_scene profile_scene; do
  TASK_STATUS="$(echo "$TASKS_JSON" | jq -r --arg t "$task_id" '.tasks[] | select(.pipelineTaskId == $t) | .status // empty')"
  if [ "$TASK_STATUS" != "succeeded" ]; then
    echo "❌ Task '$task_id' expected succeeded (explicit params given), got '$TASK_STATUS'" >&2
    exit 1
  fi
done

echo "  All task statuses: OK"
echo ""

# ── 9. Assert validate_scene result refs ──────────────────────────────────────

echo "--- 9. Assert validate_scene result refs ---"
VALIDATE_TASK="$(echo "$TASKS_JSON" | jq '.tasks[] | select(.pipelineTaskId == "validate_scene")')"

VALIDATION_RUN_ID="$(echo "$VALIDATE_TASK" | jq -r '.result.refs.validation_run_id // empty')"
# validation_report_uri is in artifacts (not a downstream-consumed ref)
VALIDATION_REPORT_URI="$(echo "$VALIDATE_TASK" | jq -r '.result.artifacts.validation_report_uri // empty')"
VALIDATION_STATUS="$(echo "$VALIDATE_TASK" | jq -r '.result.summary.validation_status // empty')"
SHOULD_BLOCK="$(echo "$VALIDATE_TASK" | jq -r '.result.summary.should_block_pipeline // empty')"
CHECKED_COUNT="$(echo "$VALIDATE_TASK" | jq -r '.result.summary.checked_scene_count // 0')"

echo "  validation_run_id=$VALIDATION_RUN_ID"
echo "  validation_report_uri=$VALIDATION_REPORT_URI"
echo "  validation_status=$VALIDATION_STATUS"
echo "  should_block_pipeline=$SHOULD_BLOCK"
echo "  checked_scene_count=$CHECKED_COUNT"

if [ -z "$VALIDATION_RUN_ID" ]; then
  echo "❌ validate_scene: missing validation_run_id in result refs" >&2
  echo "$VALIDATE_TASK" | jq . >&2
  exit 1
fi

if [ -z "$VALIDATION_REPORT_URI" ]; then
  echo "❌ validate_scene: missing validation_report_uri in result refs" >&2
  exit 1
fi

if [ -z "$VALIDATION_STATUS" ]; then
  echo "❌ validate_scene: missing validation_status in result summary" >&2
  exit 1
fi

if [ "${CHECKED_COUNT:-0}" -lt 1 ]; then
  echo "❌ validate_scene: checked_scene_count should be >= 1, got $CHECKED_COUNT" >&2
  exit 1
fi

echo "  OK"
echo ""

# ── 10. Assert profile_scene result refs ──────────────────────────────────────

echo "--- 10. Assert profile_scene result refs ---"
PROFILE_TASK="$(echo "$TASKS_JSON" | jq '.tasks[] | select(.pipelineTaskId == "profile_scene")')"

PROFILE_RUN_ID="$(echo "$PROFILE_TASK" | jq -r '.result.refs.profile_run_id // empty')"
# profile_report_uri is in artifacts (not a downstream-consumed ref)
PROFILE_REPORT_URI="$(echo "$PROFILE_TASK" | jq -r '.result.artifacts.profile_report_uri // empty')"
PROFILE_SCENE_COUNT="$(echo "$PROFILE_TASK" | jq -r '.result.summary.scene_count // 0')"

echo "  profile_run_id=$PROFILE_RUN_ID"
echo "  profile_report_uri=$PROFILE_REPORT_URI"
echo "  scene_count=$PROFILE_SCENE_COUNT"

if [ -z "$PROFILE_RUN_ID" ]; then
  echo "❌ profile_scene: missing profile_run_id in result refs" >&2
  echo "$PROFILE_TASK" | jq . >&2
  exit 1
fi

if [ -z "$PROFILE_REPORT_URI" ]; then
  echo "❌ profile_scene: missing profile_report_uri in result refs" >&2
  exit 1
fi

echo "  OK"
echo ""

# ── 11. Assert dataset version quality cache ──────────────────────────────────

echo "--- 11. Assert dataset version quality cache ---"
QUALITY_JSON="$(curl -sS "$(api_url "$API_BASE_URL" "/datasets/$DATASET_ID/versions/$DATASET_VERSION/quality")")"

echo "$QUALITY_JSON" | jq '{
  latestValidationRunId,
  validationStatus,
  shouldBlockPipeline,
  validationReportUri,
  latestProfileRunId,
  profileReportUri
}' 2>/dev/null || echo "$QUALITY_JSON"

QUAL_VALIDATION_RUN_ID="$(echo "$QUALITY_JSON" | jq -r '.latestValidationRunId // empty')"
QUAL_VALIDATION_STATUS="$(echo "$QUALITY_JSON" | jq -r '.validationStatus // empty')"
QUAL_SHOULD_BLOCK="$(echo "$QUALITY_JSON" | jq -r '.shouldBlockPipeline // empty')"
QUAL_VALIDATION_REPORT_URI="$(echo "$QUALITY_JSON" | jq -r '.validationReportUri // empty')"
QUAL_PROFILE_RUN_ID="$(echo "$QUALITY_JSON" | jq -r '.latestProfileRunId // empty')"
QUAL_PROFILE_REPORT_URI="$(echo "$QUALITY_JSON" | jq -r '.profileReportUri // empty')"

if [ -z "$QUAL_VALIDATION_RUN_ID" ]; then
  echo "❌ quality cache: latestValidationRunId is empty" >&2
  echo "$QUALITY_JSON" | jq . >&2
  exit 1
fi
echo "  latestValidationRunId=$QUAL_VALIDATION_RUN_ID  OK"

if [ -z "$QUAL_VALIDATION_STATUS" ]; then
  echo "❌ quality cache: validationStatus is empty" >&2
  exit 1
fi
echo "  validationStatus=$QUAL_VALIDATION_STATUS  OK"

if [ "$QUAL_SHOULD_BLOCK" = "null" ] || [ -z "$QUAL_SHOULD_BLOCK" ]; then
  echo "❌ quality cache: shouldBlockPipeline is missing" >&2
  exit 1
fi
echo "  shouldBlockPipeline=$QUAL_SHOULD_BLOCK  OK"

if [ -z "$QUAL_VALIDATION_REPORT_URI" ]; then
  echo "❌ quality cache: validationReportUri is empty" >&2
  exit 1
fi
echo "  validationReportUri=$QUAL_VALIDATION_REPORT_URI  OK"

if [ -z "$QUAL_PROFILE_RUN_ID" ]; then
  echo "❌ quality cache: latestProfileRunId is empty" >&2
  exit 1
fi
echo "  latestProfileRunId=$QUAL_PROFILE_RUN_ID  OK"

if [ -z "$QUAL_PROFILE_REPORT_URI" ]; then
  echo "❌ quality cache: profileReportUri is empty" >&2
  exit 1
fi
echo "  profileReportUri=$QUAL_PROFILE_REPORT_URI  OK"

# Cross-check: run IDs in quality cache match the task result refs.
if [ "$QUAL_VALIDATION_RUN_ID" != "$VALIDATION_RUN_ID" ]; then
  echo "❌ quality cache latestValidationRunId ($QUAL_VALIDATION_RUN_ID) does not match task result ($VALIDATION_RUN_ID)" >&2
  exit 1
fi
echo "  latestValidationRunId cross-check: OK"

if [ "$QUAL_PROFILE_RUN_ID" != "$PROFILE_RUN_ID" ]; then
  echo "❌ quality cache latestProfileRunId ($QUAL_PROFILE_RUN_ID) does not match task result ($PROFILE_RUN_ID)" >&2
  exit 1
fi
echo "  latestProfileRunId cross-check: OK"

echo ""

# ── 12. Optional: verify optional task skip behavior ─────────────────────────
# Run a second pipeline with no validate_scene / profile_scene params to
# confirm optional tasks are marked SKIPPED and do not block the pipeline.

echo "--- 12. Verify optional task skip (no validate/profile params) ---"
SKIP_PAYLOAD="$(cat <<JSON
{
  "type": "dataset_scene_ingestion",
  "dataset_id": "$DATASET_ID",
  "dataset_version": "${DATASET_VERSION}-skip-test",
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
    "build_scene_index": {},
    "build_dataset_manifest": {}
  }
}
JSON
)"

SKIP_CREATE_RESP="$(create_pipeline_run "$API_BASE_URL" "$SKIP_PAYLOAD")"
SKIP_RUN_ID="$(extract_pipeline_run_id "$SKIP_CREATE_RESP")"
echo "  skip pipeline_run_id=$SKIP_RUN_ID"

SKIP_EXEC_RESP="$(dispatch_pipeline_run "$API_BASE_URL" "$SKIP_RUN_ID")"
SKIP_EXEC_STATUS="$(echo "$SKIP_EXEC_RESP" | jq -r '.execution.status // "error"')"
echo "  execution status=$SKIP_EXEC_STATUS"
if [ "$SKIP_EXEC_STATUS" = "error" ]; then
  echo "$SKIP_EXEC_RESP" | jq . >&2
  exit 1
fi

echo "  Polling skip run..."
SKIP_PIPELINE_JSON="$(poll_pipeline_terminal "$API_BASE_URL" "$SKIP_RUN_ID" "$POLL_TIMEOUT" 5)"
assert_pipeline_succeeded "$SKIP_PIPELINE_JSON" 'skip-test pipeline should succeed'

SKIP_TASKS_JSON="$(fetch_pipeline_tasks "$API_BASE_URL" "$SKIP_RUN_ID")"
echo "$SKIP_TASKS_JSON" | jq -r '.tasks[] | "  \(.pipelineTaskId): \(.status)"'

# validate_scene and profile_scene must be SKIPPED (no params provided).
for task_id in validate_scene profile_scene; do
  SKIP_TASK_STATUS="$(echo "$SKIP_TASKS_JSON" | jq -r --arg t "$task_id" '.tasks[] | select(.pipelineTaskId == $t) | .status // empty')"
  if [ "$SKIP_TASK_STATUS" != "skipped" ]; then
    echo "❌ Task '$task_id' expected skipped (no params), got '$SKIP_TASK_STATUS'" >&2
    exit 1
  fi
  echo "  $task_id: skipped OK"
done

# Required tasks must succeed despite optional tasks being skipped.
for task_id in ingest_scenes register_scene build_scene_index build_dataset_manifest; do
  SKIP_TASK_STATUS="$(echo "$SKIP_TASKS_JSON" | jq -r --arg t "$task_id" '.tasks[] | select(.pipelineTaskId == $t) | .status // empty')"
  if [ "$SKIP_TASK_STATUS" != "succeeded" ]; then
    echo "❌ Required task '$task_id' expected succeeded after optional skip, got '$SKIP_TASK_STATUS'" >&2
    exit 1
  fi
done

echo "  Optional task skip: OK"
echo ""

# ── Summary ───────────────────────────────────────────────────────────────────

echo "=== PASSED ==="
echo "  pipeline_run_id=$PIPELINE_RUN_ID"
echo "  validation_run_id=$VALIDATION_RUN_ID"
echo "  profile_run_id=$PROFILE_RUN_ID"
echo "  quality_cache: validationStatus=$QUAL_VALIDATION_STATUS shouldBlockPipeline=$QUAL_SHOULD_BLOCK"
echo "  skip pipeline_run_id=$SKIP_RUN_ID"
