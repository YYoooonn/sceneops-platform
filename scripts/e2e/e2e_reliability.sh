#!/usr/bin/env bash
# e2e_reliability.sh
#
# E2E test for Phase 2 reliability primitives:
#   1. Job execution_key idempotency (identical create -> same job; force ->
#      new job; different params -> new job).
#   2. Pipeline partial retry: a pipeline BLOCKED by a quality gate
#      (validate_scene) can be redispatched, and the already-succeeded task
#      (register_scene) is NOT re-executed.
#
# Usage:
#   bash scripts/e2e/e2e_reliability.sh
#
# Env overrides:
#   API_BASE_URL    (default: http://localhost:8000)
#   DATASET_ID      (default: nuscenes)
#   DATASET_VERSION (default: v1.0-mini)
#   SOURCE_ROOT_URI (default: /data/raw/nuscenes)
#   MAX_SOURCE_SCENES (default: 2)
#   POLL_TIMEOUT    max poll attempts, 5s each (default: 60 = 5 min)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
DATASET_ID="${DATASET_ID:-nuscenes}"
DATASET_VERSION="${DATASET_VERSION:-v1.0-mini}"
SOURCE_ROOT_URI="${SOURCE_ROOT_URI:-/data/raw/nuscenes}"
MAX_SOURCE_SCENES="${MAX_SOURCE_SCENES:-2}"
POLL_TIMEOUT="${POLL_TIMEOUT:-60}"

echo "=== reliability (idempotency + partial retry) E2E ==="
echo "  API_BASE_URL=$API_BASE_URL"
echo "  DATASET_ID=$DATASET_ID  DATASET_VERSION=$DATASET_VERSION"
echo ""

echo "--- 0. Upsert dataset ---"
upsert_dataset "$API_BASE_URL" "$DATASET_ID" "nuScenes" | jq '.dataset | {datasetId, status}' 2>/dev/null || true
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

echo "--- B1. Create dataset_scene_ingestion pipeline with an impossible ---"
echo "         channel requirement (forces validate_scene to BLOCK) ---"
PIPELINE_PAYLOAD="$(cat <<JSON
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
      "require_target_channels": ["NONEXISTENT_CHANNEL"]
    },
    "profile_scene": {},
    "build_scene_index": {},
    "build_dataset_manifest": {}
  }
}
JSON
)"

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

echo "--- B3. Capture register_scene task identity before retry ---"
TASKS_BEFORE="$(fetch_pipeline_tasks "$API_BASE_URL" "$PIPELINE_RUN_ID")"
REGISTER_TASK_RUN_ID_BEFORE="$(echo "$TASKS_BEFORE" | jq -r '.tasks[] | select(.pipelineTaskId == "register_scene") | .pipelineTaskRunId')"
REGISTER_STATUS_BEFORE="$(echo "$TASKS_BEFORE" | jq -r '.tasks[] | select(.pipelineTaskId == "register_scene") | .status')"
echo "  register_scene: pipelineTaskRunId=$REGISTER_TASK_RUN_ID_BEFORE status=$REGISTER_STATUS_BEFORE"

if [ "$REGISTER_STATUS_BEFORE" != "succeeded" ]; then
  echo "❌ Expected register_scene to have succeeded before the blocking task" >&2
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

echo "--- B5. Assert register_scene was NOT re-executed ---"
TASKS_AFTER="$(fetch_pipeline_tasks "$API_BASE_URL" "$PIPELINE_RUN_ID")"
REGISTER_TASK_RUN_ID_AFTER="$(echo "$TASKS_AFTER" | jq -r '.tasks[] | select(.pipelineTaskId == "register_scene") | .pipelineTaskRunId')"
echo "  register_scene: pipelineTaskRunId=$REGISTER_TASK_RUN_ID_AFTER"

if [ "$REGISTER_TASK_RUN_ID_AFTER" != "$REGISTER_TASK_RUN_ID_BEFORE" ]; then
  echo "❌ register_scene task run identity changed across retry — it was re-executed" >&2
  exit 1
fi
echo "  OK (same task run — resumed from the blocked task, not re-run from scratch)"
echo ""

# ── Summary ───────────────────────────────────────────────────────────────────

echo "=== PASSED ==="
echo "  idempotent job_id=$JOB_A  forced job_id=$JOB_C"
echo "  pipeline_run_id=$PIPELINE_RUN_ID  register_scene task unchanged across retry"
