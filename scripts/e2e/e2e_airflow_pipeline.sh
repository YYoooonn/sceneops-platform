#!/usr/bin/env bash
# e2e_airflow_pipeline.sh
#
# E2E test for the Airflow pipeline execution backend PoC:
#   dispatch dataset_scene_ingestion via Airflow (per-task DAG,
#   sceneops_pipeline_run) instead of Celery, and confirm it reaches
#   `succeeded` with all 6 task runs succeeded — same outcome as
#   e2e_dataset_scene_ingestion.sh, different execution backend.
#
# Precondition (cannot be automated by this script — it's a process-startup
# setting, not a per-request one):
#   1. `make airflow-up` has been run (Airflow webserver/scheduler/DB up).
#   2. .env.local has SCENEOPS_API_EXECUTION__PIPELINE_BACKEND=airflow, and
#      the `api` service has been (re)started with that value, e.g.:
#        docker compose -f docker-compose.local.yml up -d --build api
#
# Usage:
#   bash scripts/e2e/e2e_airflow_pipeline.sh
#
# Env overrides:
#   API_BASE_URL    (default: http://localhost:8000)
#   DATASET_ID      (default: nuscenes)
#   DATASET_VERSION (default: v1.0-mini)
#   SOURCE_ROOT_URI (default: /data/raw/nuscenes)
#   MAX_SOURCE_SCENES (default: 2)
#   POLL_TIMEOUT    max poll attempts, 10s each (default: 60 = 10 min —
#                    Airflow scheduling adds latency vs. direct Celery dispatch)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
DATASET_ID="${DATASET_ID:-nuscenes}"
DATASET_VERSION="${DATASET_VERSION:-v1.0-mini}"
SOURCE_ROOT_URI="${SOURCE_ROOT_URI:-/data/raw/nuscenes}"
MAX_SOURCE_SCENES="${MAX_SOURCE_SCENES:-2}"
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
  "force": true,
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

assert_pipeline_succeeded "$PIPELINE_JSON" 'Airflow-dispatched dataset_scene_ingestion pipeline should succeed'
echo "  OK"
echo ""

# ── 6. Assert all 6 task runs succeeded ──────────────────────────────────────

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
echo "  DAG run named '$PIPELINE_RUN_ID' to see the 8-task graph."
