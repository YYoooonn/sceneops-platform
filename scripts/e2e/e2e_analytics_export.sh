#!/usr/bin/env bash
# e2e_analytics_export.sh
#
# E2E test for the export_analytics_snapshot job (Parquet analytical layer):
#   1. Run dataset_scene_ingestion to ensure registered scenes exist.
#   2. Create + dispatch a standalone export_analytics_snapshot job.
#   3. Assert scenes/samples/sensor_frames/annotations parquet tables were
#      written and registered as analytics_table artifacts.
#
# Usage:
#   bash scripts/e2e/e2e_analytics_export.sh
#
# Env overrides:
#   API_BASE_URL    (default: http://localhost:8000)
#   DATASET_ID      (default: nuscenes)
#   DATASET_VERSION (default: v1.0-mini)
#   SOURCE_ROOT_URI (default: /data/raw/nuscenes)
#   MAX_SOURCE_SCENES (default: 2)
#   SKIP_INGESTION  set to "1" to reuse scenes already registered for
#                   DATASET_ID/DATASET_VERSION instead of re-running ingestion
#   POLL_TIMEOUT    max poll attempts, 5s each (default: 60 = 5 min)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
DATASET_ID="${DATASET_ID:-nuscenes}"
DATASET_VERSION="${DATASET_VERSION:-v1.0-mini}"
SOURCE_ROOT_URI="${SOURCE_ROOT_URI:-/data/raw/nuscenes}"
MAX_SOURCE_SCENES="${MAX_SOURCE_SCENES:-2}"
SKIP_INGESTION="${SKIP_INGESTION:-0}"
POLL_TIMEOUT="${POLL_TIMEOUT:-60}"

echo "=== export_analytics_snapshot E2E ==="
echo "  API_BASE_URL=$API_BASE_URL"
echo "  DATASET_ID=$DATASET_ID  DATASET_VERSION=$DATASET_VERSION"
echo ""

# ── 1. Ensure registered scenes exist ────────────────────────────────────────

if [ "$SKIP_INGESTION" = "1" ]; then
  echo "--- 1. Skipping ingestion (SKIP_INGESTION=1) ---"
else
  echo "--- 1. Run dataset_scene_ingestion to get registered scenes ---"
  API_BASE_URL="$API_BASE_URL" \
  DATASET_ID="$DATASET_ID" \
  DATASET_VERSION="$DATASET_VERSION" \
  SOURCE_ROOT_URI="$SOURCE_ROOT_URI" \
  MAX_SOURCE_SCENES="$MAX_SOURCE_SCENES" \
  POLL_TIMEOUT="$POLL_TIMEOUT" \
    bash "$SCRIPT_DIR/e2e_dataset_scene_ingestion.sh"
fi
echo ""

# ── 2. Create export_analytics_snapshot job ──────────────────────────────────

echo "--- 2. Create job ---"
PAYLOAD="$(cat <<JSON
{
  "type": "export_analytics_snapshot",
  "dataset_id": "$DATASET_ID",
  "dataset_version": "$DATASET_VERSION",
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

# ── 6. Assert all 4 tables were written with rows ────────────────────────────

echo "--- 6. Assert tables ---"
for TABLE in scenes samples sensor_frames annotations; do
  URI="$(echo "$JOB_JSON" | jq -r --arg t "$TABLE" '.job.result.table_uris[$t] // empty')"
  ROWS="$(echo "$JOB_JSON" | jq -r --arg t "$TABLE" '.job.result.row_counts[$t] // 0')"
  echo "  $TABLE: uri=$URI rows=$ROWS"

  if [ -z "$URI" ]; then
    echo "❌ Missing table_uris.$TABLE in job result" >&2
    exit 1
  fi
  if [ "${ROWS:-0}" -lt 1 ]; then
    echo "❌ Expected $TABLE row_count > 0, got $ROWS" >&2
    exit 1
  fi
done
echo "  OK"
echo ""

# ── 7. Assert analytics_table artifacts registered ───────────────────────────

echo "--- 7. Assert artifacts ---"
ARTIFACTS_JSON="$(curl -sS "$(api_url "$API_BASE_URL" "/artifacts?owner_type=dataset_version&owner_id=$DATASET_ID:$DATASET_VERSION")")"
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
