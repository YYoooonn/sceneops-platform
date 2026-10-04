#!/usr/bin/env bash
# e2e_scene_analytics_export.sh -- the Scene-domain analytical Parquet
# export (scenes / observations / keyframes / annotations tables); unrelated
# to the episode learning-data export exercised by e2e_robot_learning.sh.
#
#   1. A registered camera RobotRun (ensure_camera_robot_run, acquired once
#      and reused) -> recording_scene_building -> registered Scenes in
#      DATASET_ID/DATASET_VERSION.
#   2. A standalone export_analytics_snapshot job.
#   3. Every table is written and registered as an analytics_table
#      artifact; scenes and observations have rows. keyframes / annotations
#      are empty: recording-derived Scenes carry neither until the label
#      ingress exists (ADR-007 Q1).
#
# Usage:
#   bash scripts/e2e/e2e_scene_analytics_export.sh
#
# Env overrides (defaults come from the "core" E2E fixture, see
# scripts/e2e/lib.sh's resolve_e2e_fixture):
#   API_BASE_URL    (default: http://localhost:8000)
#   DATASET_ID      (default: test-e2e-core)
#   DATASET_VERSION (default: test-v1)
#   ROBOT_RUN_ID    (default: run-verify-camera-scene-0061)
#   SKIP_BUILD      set to "1" to reuse Scenes already registered for
#                   DATASET_ID/DATASET_VERSION
#   POLL_TIMEOUT    max poll attempts, 5s each (default: 60 = 5 min)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/../.."
source "$SCRIPT_DIR/lib.sh"

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
resolve_e2e_fixture core
ROBOT_RUN_ID="${ROBOT_RUN_ID:-run-verify-camera-scene-0061}"
SKIP_BUILD="${SKIP_BUILD:-0}"
POLL_TIMEOUT="${POLL_TIMEOUT:-60}"

echo "=== export_analytics_snapshot E2E ==="
echo "  API_BASE_URL=$API_BASE_URL"
echo "  DATASET_ID=$DATASET_ID  DATASET_VERSION=$DATASET_VERSION"
echo ""

# ── 1. Ensure registered Scenes exist ────────────────────────────────────────

if [ "$SKIP_BUILD" = "1" ]; then
  echo "--- 1. Skipping Scene build (SKIP_BUILD=1) ---"
else
  echo "--- 1. recording_scene_building from RobotRun $ROBOT_RUN_ID ---"
  upsert_dataset "$API_BASE_URL" "$DATASET_ID" "E2E core" >/dev/null
  upsert_dataset_version "$API_BASE_URL" "$DATASET_ID" "$DATASET_VERSION" >/dev/null
  ensure_camera_robot_run "$ROBOT_RUN_ID"
  BUILD_PAYLOAD="$(jq -cn \
    --arg ds "$DATASET_ID" --arg v "$DATASET_VERSION" --arg run "$ROBOT_RUN_ID" \
    --argjson config "$(camera_scene_build_config)" '{
      type: "recording_scene_building", dataset_id: $ds, dataset_version: $v, force: true,
      params: {
        build_recording_scenes: {robot_run_id: $run, build_config: $config},
        register_scenes: {replace: true}
      }}')"
  BUILD_RUN_ID="$(extract_pipeline_run_id "$(create_pipeline_run "$API_BASE_URL" "$BUILD_PAYLOAD")")"
  dispatch_pipeline_run "$API_BASE_URL" "$BUILD_RUN_ID" >/dev/null
  assert_pipeline_succeeded "$(poll_pipeline_terminal "$API_BASE_URL" "$BUILD_RUN_ID" "$POLL_TIMEOUT" 5)" \
    "recording_scene_building should succeed" "$API_BASE_URL" "$BUILD_RUN_ID"
  echo "  pipeline_run_id=$BUILD_RUN_ID"
fi
echo ""

# ── 2. Create export_analytics_snapshot job ──────────────────────────────────

echo "--- 2. Create job ---"
PAYLOAD="$(cat <<JSON
{
  "type": "export_analytics_snapshot",
  "dataset_id": "$DATASET_ID",
  "dataset_version": "$DATASET_VERSION",
  "force": true,
  "params": {
    "dataset_id": "$DATASET_ID",
    "dataset_version": "$DATASET_VERSION"
  }
}
JSON
)"

CREATE_RESP="$(create_job "$API_BASE_URL" "$PAYLOAD")"
JOB_ID="$(extract_job_id "$CREATE_RESP")"
echo "  job_id=$JOB_ID"
echo ""

# ── 3. Dispatch ───────────────────────────────────────────────────────────────

echo "--- 3. Dispatch ---"
EXEC_RESP="$(execute_job "$API_BASE_URL" "$JOB_ID")"
EXEC_STATUS="$(echo "$EXEC_RESP" | jq -r '.execution.status // "error"')"
echo "  execution status=$EXEC_STATUS"
if [ "$EXEC_STATUS" = "error" ]; then
  echo "$EXEC_RESP" | jq . >&2
  exit 1
fi
echo ""

# ── 4. Poll ───────────────────────────────────────────────────────────────────

echo "--- 4. Polling (up to $((POLL_TIMEOUT * 5))s) ---"
JOB_JSON="$(poll_job_terminal "$API_BASE_URL" "$JOB_ID" "$POLL_TIMEOUT" 5)"
echo ""

# ── 5. Assert job succeeded ──────────────────────────────────────────────────

echo "--- 5. Assert job ---"
FINAL_STATUS="$(echo "$JOB_JSON" | jq -r '.job.status')"
echo "  status=$FINAL_STATUS"

if [ "$FINAL_STATUS" = "failed" ]; then
  echo "  error=$(echo "$JOB_JSON" | jq -r '.job.error.message // "unknown"')"
fi

assert_job_succeeded "$JOB_JSON" 'export_analytics_snapshot job should succeed'
echo "  OK"
echo ""

# ── 6. Assert every table was written ────────────────────────────────────────

echo "--- 6. Assert tables ---"
for TABLE in scenes observations keyframes annotations; do
  URI="$(echo "$JOB_JSON" | jq -r --arg t "$TABLE" '.job.result.table_uris[$t] // empty')"
  ROWS="$(echo "$JOB_JSON" | jq -r --arg t "$TABLE" '.job.result.row_counts[$t] // 0')"
  echo "  $TABLE: uri=$URI rows=$ROWS"
  if [ -z "$URI" ]; then
    echo "❌ Missing table_uris.$TABLE in job result" >&2
    exit 1
  fi
done
for TABLE in scenes observations; do
  if [ "$(echo "$JOB_JSON" | jq -r --arg t "$TABLE" '.job.result.row_counts[$t] // 0')" -lt 1 ]; then
    echo "❌ Expected $TABLE row_count > 0" >&2
    exit 1
  fi
done
echo "  OK"
echo ""

# ── 7. Assert analytics_table artifacts registered ───────────────────────────

echo "--- 7. Assert artifacts ---"
ARTIFACTS_JSON="$(fetch_artifacts_by_owner "$API_BASE_URL" "dataset_version" "$DATASET_ID:$DATASET_VERSION")"
assert_artifact_kind_present "$ARTIFACTS_JSON" "analytics_table" "expected analytics_table artifacts for $DATASET_ID:$DATASET_VERSION"
ANALYTICS_ARTIFACT_COUNT="$(echo "$ARTIFACTS_JSON" | jq '[.artifacts[] | select(.kind == "analytics_table")] | length')"
echo "  analytics_table artifacts=$ANALYTICS_ARTIFACT_COUNT"

if [ "$ANALYTICS_ARTIFACT_COUNT" -lt 4 ]; then
  echo "❌ Expected 4 analytics_table artifacts (scenes/samples/sensor_frames/annotations), got $ANALYTICS_ARTIFACT_COUNT" >&2
  exit 1
fi
echo "  OK"
echo ""

# ── Summary ───────────────────────────────────────────────────────────────────

echo "=== PASSED ==="
echo "  job_id=$JOB_ID"
echo "  scene_count=$(echo "$JOB_JSON" | jq -r '.job.result.scene_count // 0')"
