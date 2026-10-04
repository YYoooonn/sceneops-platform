#!/usr/bin/env bash
# verify_airflow_backend.sh
#
# This is an alternate-orchestrator COMPATIBILITY CHECK, not a new domain
# workflow -- it dispatches the recording_scene_building pipeline
# e2e-recording-scene covers, just through Airflow's per-task DAG PoC
# instead of Celery, to prove that substitution still works. The Airflow
# backend is a PoC hardcoded to recording_scene_building only
# (airflow/dags/sceneops_pipeline_run.py), not a general pipeline-backend
# substitution.
#
# Verifies the Airflow pipeline execution backend PoC:
#   dispatch recording_scene_building via Airflow (per-task DAG,
#   sceneops_pipeline_run) instead of Celery, and confirm it reaches
#   `succeeded` with all 4 task runs succeeded, recorded under the airflow
#   execution backend. Its input is a registered camera RobotRun
#   (ensure_camera_robot_run in scripts/e2e/lib.sh; acquired once, then
#   reused).
#
# Precondition (cannot be automated by this script — it's a process-startup
# setting, not a per-request one):
#   1. `make airflow-up` has been run (Airflow webserver/scheduler/DB up).
#   2. .env.local has SCENEOPS_API_EXECUTION__PIPELINE_BACKEND=airflow, and
#      the `api` service has been (re)started with that value, e.g.:
#        docker compose up -d --build api
#
# Usage:
#   bash scripts/e2e/verify_airflow_backend.sh
#
# Env overrides (defaults come from the "core" E2E fixture, see
# scripts/e2e/lib.sh's resolve_e2e_fixture):
#   API_BASE_URL    (default: http://localhost:8000)
#   DATASET_ID      (default: test-e2e-core)
#   DATASET_VERSION (default: test-v1)
#   ROBOT_RUN_ID    (default: run-verify-camera-scene-0061)
#   POLL_TIMEOUT    max poll attempts, 10s each (default: 60 = 10 min —
#                    Airflow scheduling adds latency vs. direct Celery dispatch)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/../.."
source "$SCRIPT_DIR/lib.sh"

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
resolve_e2e_fixture core
ROBOT_RUN_ID="${ROBOT_RUN_ID:-run-verify-camera-scene-0061}"
POLL_TIMEOUT="${POLL_TIMEOUT:-60}"

echo "=== Airflow pipeline execution backend E2E ==="
echo "  API_BASE_URL=$API_BASE_URL"
echo "  DATASET_ID=$DATASET_ID  DATASET_VERSION=$DATASET_VERSION"
echo ""
echo "  Precondition: api service must be running with"
echo "  SCENEOPS_API_EXECUTION__PIPELINE_BACKEND=airflow, and 'make airflow-up'"
echo "  must have been run. This script does not verify or set that for you."
echo ""

# ── 1. Ensure dataset exists ──────────────────────────────────────────────────

echo "--- 1. Upsert dataset version + registered RobotRun fixture ---"
upsert_dataset "$API_BASE_URL" "$DATASET_ID" "E2E core" | jq -c '.dataset | {datasetId}'
upsert_dataset_version "$API_BASE_URL" "$DATASET_ID" "$DATASET_VERSION" | jq -c '.version | {datasetId, version}'
ensure_camera_robot_run "$ROBOT_RUN_ID"
echo ""

# ── 2. Create pipeline run ────────────────────────────────────────────────────

echo "--- 2. Create pipeline run ---"
PAYLOAD="$(jq -cn \
  --arg ds "$DATASET_ID" --arg v "$DATASET_VERSION" --arg run "$ROBOT_RUN_ID" \
  --argjson config "$(camera_scene_build_config)" '{
    type: "recording_scene_building", dataset_id: $ds, dataset_version: $v, force: true,
    params: {
      build_recording_scenes: {robot_run_id: $run, build_config: $config},
      register_scenes: {replace: true},
      profile_scene: {triggered: true}
    }}')"

CREATE_RESP="$(create_pipeline_run "$API_BASE_URL" "$PAYLOAD")"
PIPELINE_RUN_ID="$(extract_pipeline_run_id "$CREATE_RESP")"
echo "  pipeline_run_id=$PIPELINE_RUN_ID"
echo ""

# ── 3. Dispatch (routes to Airflow, not Celery, per api's own settings) ──────

echo "--- 3. Dispatch ---"
EXEC_RESP="$(dispatch_pipeline_run "$API_BASE_URL" "$PIPELINE_RUN_ID")"
echo "$EXEC_RESP"
EXEC_STATUS="$(echo "$EXEC_RESP" | jq -r '.execution.status // "error"')"
EXEC_BACKEND="$(echo "$EXEC_RESP" | jq -r '.execution.executionBackend // "unknown"')"
echo "  execution status=$EXEC_STATUS backend=$EXEC_BACKEND"

if [ "$EXEC_STATUS" = "error" ]; then
  echo "$EXEC_RESP" | jq . >&2
  exit 1
fi

if [ "$EXEC_BACKEND" != "airflow" ]; then
  echo "❌ Expected execution backend 'airflow', got '$EXEC_BACKEND'." >&2
  echo "   Is SCENEOPS_API_EXECUTION__PIPELINE_BACKEND=airflow set on the api service?" >&2
  exit 1
fi
echo ""

# ── 4. Poll ───────────────────────────────────────────────────────────────────

echo "--- 4. Polling (up to $((POLL_TIMEOUT * 10))s) ---"
PIPELINE_JSON="$(poll_pipeline_terminal "$API_BASE_URL" "$PIPELINE_RUN_ID" "$POLL_TIMEOUT" 10)"
echo ""

# ── 5. Assert pipeline succeeded ─────────────────────────────────────────────

echo "--- 5. Assert pipeline ---"
FINAL_STATUS="$(echo "$PIPELINE_JSON" | jq -r '.pipelineRun.status')"
echo "  status=$FINAL_STATUS"

if [ "$FINAL_STATUS" != "succeeded" ]; then
  echo "  error=$(echo "$PIPELINE_JSON" | jq -r '.pipelineRun.error.message // "unknown"')"
fi

assert_pipeline_succeeded "$PIPELINE_JSON" 'Airflow-dispatched recording_scene_building pipeline should succeed' "$API_BASE_URL" "$PIPELINE_RUN_ID"
echo "  OK"
echo ""

# ── 6. Assert all 4 task runs succeeded ──────────────────────────────────────

echo "--- 6. Assert tasks ---"
TASKS_JSON="$(fetch_pipeline_tasks "$API_BASE_URL" "$PIPELINE_RUN_ID")"
TASK_COUNT="$(echo "$TASKS_JSON" | jq '.tasks | length')"
FAILED_TASKS="$(echo "$TASKS_JSON" | jq -r '[.tasks[] | select(.status != "succeeded")] | map("\(.pipelineTaskId)=\(.status)") | join(", ")')"

echo "$TASKS_JSON" | jq -r '.tasks[] | "  \(.pipelineTaskId): \(.status)"'

if [ -n "$FAILED_TASKS" ]; then
  echo "❌ Non-succeeded tasks: $FAILED_TASKS" >&2
  exit 1
fi
echo "  All $TASK_COUNT tasks: OK"
echo ""

# ── 7. Assert the execution record shows the airflow backend ────────────────

echo "--- 7. Assert execution record ---"
EXECUTIONS_JSON="$(curl -sS "$(api_url "$API_BASE_URL" "/executions?resource_id=$PIPELINE_RUN_ID")")"
RECORDED_BACKEND="$(echo "$EXECUTIONS_JSON" | jq -r '.executions[0].executionBackend // "unknown"')"
echo "  recorded execution_backend=$RECORDED_BACKEND"

if [ "$RECORDED_BACKEND" != "airflow" ]; then
  echo "❌ Expected ExecutionRecord.execution_backend='airflow', got '$RECORDED_BACKEND'" >&2
  exit 1
fi
echo "  OK"
echo ""

# ── Summary ───────────────────────────────────────────────────────────────────

echo "=== PASSED ==="
echo "  pipeline_run_id=$PIPELINE_RUN_ID"
echo "  execution_backend=$RECORDED_BACKEND"
echo "  Check the Airflow UI (http://localhost:8080) for the sceneops_pipeline_run"
echo "  DAG run named '$PIPELINE_RUN_ID' to see the start -> 4 tasks -> finalize graph."
