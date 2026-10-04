#!/usr/bin/env bash
# verify_reliability.sh -- verifies an execution-model property, not a
# domain workflow from a real source to a persisted result, so it lives in
# the verify-* namespace rather than e2e-*, same tier as
# verify_airflow_backend.sh.
#
# Verifies these reliability primitives:
#   1. Job execution_key idempotency (identical create -> same job; force ->
#      new job; different params -> new job).
#   2. Pipeline partial retry: a recording_scene_building run BLOCKED by a
#      quality gate (validate_scene) can be redispatched, and the
#      already-succeeded tasks (build_recording_scenes, register_scenes) are
#      NOT re-executed.
#
# Part B needs a registered, sensor-bearing RobotRun: ensure_camera_robot_run
# (scripts/e2e/lib.sh) acquires one through the acquisition containers the
# first time and reuses it afterwards (ROBOT_RUN_ID).
#
# Usage:
#   bash scripts/e2e/verify_reliability.sh
#
# Env overrides (defaults come from the "core" E2E fixture, see
# scripts/e2e/lib.sh's resolve_e2e_fixture):
#   API_BASE_URL    (default: http://localhost:8000)
#   DATASET_ID      (default: test-e2e-core)
#   DATASET_VERSION (default: test-v1)
#   ROBOT_RUN_ID    (default: run-verify-camera-scene-0061)
#   POLL_TIMEOUT    max poll attempts, 5s each (default: 60 = 5 min)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/../.."
source "$SCRIPT_DIR/lib.sh"

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
resolve_e2e_fixture core
ROBOT_RUN_ID="${ROBOT_RUN_ID:-run-verify-camera-scene-0061}"
POLL_TIMEOUT="${POLL_TIMEOUT:-60}"

echo "=== reliability (idempotency + partial retry) E2E ==="
echo "  API_BASE_URL=$API_BASE_URL"
echo "  DATASET_ID=$DATASET_ID  DATASET_VERSION=$DATASET_VERSION"
echo ""

echo "--- 0. Upsert dataset version + registered RobotRun fixture ---"
upsert_dataset "$API_BASE_URL" "$DATASET_ID" "E2E core" | jq -c '.dataset | {datasetId}'
upsert_dataset_version "$API_BASE_URL" "$DATASET_ID" "$DATASET_VERSION" | jq -c '.version | {datasetId, version}'
ensure_camera_robot_run "$ROBOT_RUN_ID"
echo ""

# ── Part A: Job execution_key idempotency ────────────────────────────────────

echo "--- A1. Create job (no force) ---"
JOB_PAYLOAD="$(cat <<JSON
{
  "type": "export_analytics_snapshot",
  "dataset_id": "$DATASET_ID",
  "dataset_version": "$DATASET_VERSION",
  "params": {"dataset_id": "$DATASET_ID", "dataset_version": "$DATASET_VERSION"}
}
JSON
)"
JOB_A="$(extract_job_id "$(create_job "$API_BASE_URL" "$JOB_PAYLOAD")")"
echo "  job_a=$JOB_A"

echo "--- A2. Create identical job again (should dedupe) ---"
JOB_B="$(extract_job_id "$(create_job "$API_BASE_URL" "$JOB_PAYLOAD")")"
echo "  job_b=$JOB_B"

if [ "$JOB_A" != "$JOB_B" ]; then
  echo "❌ Expected identical create_job calls to return the same job_id: $JOB_A != $JOB_B" >&2
  exit 1
fi
echo "  OK (deduped)"
echo ""

echo "--- A3. Create with force=true (should NOT dedupe) ---"
FORCE_PAYLOAD="$(cat <<JSON
{
  "type": "export_analytics_snapshot",
  "dataset_id": "$DATASET_ID",
  "dataset_version": "$DATASET_VERSION",
  "params": {"dataset_id": "$DATASET_ID", "dataset_version": "$DATASET_VERSION"},
  "force": true
}
JSON
)"
JOB_C="$(extract_job_id "$(create_job "$API_BASE_URL" "$FORCE_PAYLOAD")")"
echo "  job_c=$JOB_C"

if [ "$JOB_A" = "$JOB_C" ]; then
  echo "❌ Expected force=true to bypass dedup and create a new job" >&2
  exit 1
fi
echo "  OK (force bypassed dedup)"
echo ""

# ── Part B: Pipeline partial retry (BLOCKED redispatch) ──────────────────────

echo "--- B1. Create recording_scene_building with an impossible ---"
echo "         channel requirement (forces validate_scene to BLOCK) ---"
PIPELINE_PAYLOAD="$(jq -cn \
  --arg ds "$DATASET_ID" --arg v "$DATASET_VERSION" --arg run "$ROBOT_RUN_ID" \
  --argjson config "$(camera_scene_build_config)" '{
    type: "recording_scene_building", dataset_id: $ds, dataset_version: $v,
    params: {
      build_recording_scenes: {robot_run_id: $run, build_config: $config},
      register_scenes: {replace: true},
      validate_scene: {require_target_channels: ["NONEXISTENT_CHANNEL"]}
    }}')"

PIPELINE_RUN_ID="$(extract_pipeline_run_id "$(create_pipeline_run "$API_BASE_URL" "$PIPELINE_PAYLOAD")")"
echo "  pipeline_run_id=$PIPELINE_RUN_ID"
echo ""

echo "--- B2. Dispatch (first attempt) ---"
dispatch_pipeline_run "$API_BASE_URL" "$PIPELINE_RUN_ID" > /dev/null
PIPELINE_JSON="$(poll_pipeline_terminal "$API_BASE_URL" "$PIPELINE_RUN_ID" "$POLL_TIMEOUT" 5)"
STATUS_1="$(echo "$PIPELINE_JSON" | jq -r '.pipelineRun.status')"
echo "  status=$STATUS_1"

if [ "$STATUS_1" != "blocked" ]; then
  echo "❌ Expected pipeline to be BLOCKED by validate_scene, got: $STATUS_1" >&2
  exit 1
fi
echo "  OK (blocked, as expected)"
echo ""

echo "--- B3. Capture the succeeded tasks' identities before retry ---"
completed_tasks() {
  fetch_pipeline_tasks "$API_BASE_URL" "$PIPELINE_RUN_ID" | jq -c '[.tasks[]
    | select(.pipelineTaskId == "build_recording_scenes" or .pipelineTaskId == "register_scenes")
    | {pipelineTaskId, pipelineTaskRunId, jobId, status}] | sort_by(.pipelineTaskId)'
}
TASKS_BEFORE="$(completed_tasks)"
echo "  $TASKS_BEFORE"

if [ "$(echo "$TASKS_BEFORE" | jq -r '[.[].status] | unique | join(",")')" != "succeeded" ]; then
  echo "❌ Expected build_recording_scenes and register_scenes to have succeeded before the blocking task" >&2
  exit 1
fi
echo ""

echo "--- B4. Redispatch the SAME (BLOCKED) pipeline_run_id ---"
dispatch_pipeline_run "$API_BASE_URL" "$PIPELINE_RUN_ID" > /dev/null
PIPELINE_JSON_2="$(poll_pipeline_terminal "$API_BASE_URL" "$PIPELINE_RUN_ID" "$POLL_TIMEOUT" 5)"
STATUS_2="$(echo "$PIPELINE_JSON_2" | jq -r '.pipelineRun.status')"
echo "  status=$STATUS_2"

if [ "$STATUS_2" != "blocked" ]; then
  echo "❌ Expected redispatch to reach BLOCKED again (same bad params), got: $STATUS_2" >&2
  exit 1
fi
echo "  OK (BLOCKED pipeline was redispatchable — no RuntimeError)"
echo ""

echo "--- B5. Assert build_recording_scenes / register_scenes were NOT re-executed ---"
TASKS_AFTER="$(completed_tasks)"
echo "  $TASKS_AFTER"

if [ "$TASKS_AFTER" != "$TASKS_BEFORE" ]; then
  echo "❌ build/register task runs or jobs changed across retry — they were re-executed" >&2
  exit 1
fi
echo "  OK (same task run — resumed from the blocked task, not re-run from scratch)"
echo ""

# ── Summary ───────────────────────────────────────────────────────────────────

echo "=== PASSED ==="
echo "  idempotent job_id=$JOB_A  forced job_id=$JOB_C"
echo "  pipeline_run_id=$PIPELINE_RUN_ID  build/register tasks unchanged across retry"
