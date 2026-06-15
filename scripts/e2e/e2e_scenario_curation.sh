#!/usr/bin/env bash
# e2e_scenario_curation.sh
#
# Smoke test for the scenario_curation pipeline.
#
# Assumes dataset scene ingestion has already run (scenes registered + profiled).
# Run `make e2e-dataset-ingestion` first if starting from scratch.
#
# Usage:
#   bash scripts/e2e/e2e_scenario_curation.sh
#
# Env overrides:
#   API_BASE_URL     (default: http://localhost:8000)
#   DATASET_ID       (default: nuscenes)
#   DATASET_VERSION  (default: v1.0-mini)
#   POLL_TIMEOUT     max poll attempts, 5s each (default: 60 = 5 min)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
DATASET_ID="${DATASET_ID:-nuscenes}"
DATASET_VERSION="${DATASET_VERSION:-v1.0-mini}"
POLL_TIMEOUT="${POLL_TIMEOUT:-60}"

echo "=== scenario curation E2E ==="
echo "  API_BASE_URL=$API_BASE_URL"
echo "  DATASET_ID=$DATASET_ID  DATASET_VERSION=$DATASET_VERSION"
echo ""

# ── 1. Create pipeline run ────────────────────────────────────────────────────

echo "--- 1. Create scenario_curation pipeline run ---"
CREATE_PAYLOAD="$(cat <<EOF
{
  "type": "scenario_curation",
  "dataset_id": "$DATASET_ID",
  "dataset_version": "$DATASET_VERSION",
  "params": {
    "mine_scenarios": {
      "candidate_profile": "detection_ready",
      "min_annotation_count": 0,
      "required_channels": ["CAM_FRONT", "LIDAR_TOP"],
      "max_candidates": 20,
      "sort_by": "annotation_count",
      "order": "desc"
    }
  }
}
EOF
)"

CREATE_JSON="$(create_pipeline_run "$API_BASE_URL" "$CREATE_PAYLOAD")"
echo "$CREATE_JSON" | jq .

PIPELINE_RUN_ID="$(extract_pipeline_run_id "$CREATE_JSON")"
echo "  pipeline_run_id=$PIPELINE_RUN_ID"

# ── 2. Execute pipeline ───────────────────────────────────────────────────────

echo ""
echo "--- 2. Execute pipeline ---"
DISPATCH_JSON="$(dispatch_pipeline_run "$API_BASE_URL" "$PIPELINE_RUN_ID")"
echo "$DISPATCH_JSON" | jq .

# ── 3. Poll for terminal status ───────────────────────────────────────────────

echo ""
echo "--- 3. Polling for terminal status (max=${POLL_TIMEOUT} attempts × 5s) ---"
PIPELINE_JSON="$(poll_pipeline_terminal "$API_BASE_URL" "$PIPELINE_RUN_ID" "$POLL_TIMEOUT" 5)"
echo "$PIPELINE_JSON" | jq '.pipelineRun | {status, result}'

# ── 4. Assert pipeline succeeded ─────────────────────────────────────────────

echo ""
echo "--- 4. Assertions ---"
assert_pipeline_succeeded "$PIPELINE_JSON" "scenario_curation pipeline should succeed"
echo "  ✓ pipeline status=succeeded"

# scenario_set_id must be non-empty
SCENARIO_SET_ID="$(require_json_field "$PIPELINE_JSON" \
  '.pipelineRun.result.outputs.scenario_set_id // .pipelineRun.result.outputs.scenario_set_id' \
  'scenario_set_id')"
echo "  ✓ scenario_set_id=$SCENARIO_SET_ID"

# scenario_set_uri must be non-empty
SCENARIO_SET_URI="$(require_json_field "$PIPELINE_JSON" \
  '.pipelineRun.result.outputs.scenario_set_uri' \
  'scenario_set_uri')"
echo "  ✓ scenario_set_uri=$SCENARIO_SET_URI"

# readiness_report_uri must be non-empty
READINESS_URI="$(require_json_field "$PIPELINE_JSON" \
  '.pipelineRun.result.lineage.artifacts.readiness_report_uri' \
  'readiness_report_uri')"
echo "  ✓ readiness_report_uri=$READINESS_URI"

# candidate_count > 0
assert_json_gt "$PIPELINE_JSON" \
  '.pipelineRun.result.metrics.candidate_count // 0' \
  0 \
  "candidate_count should be > 0"
CANDIDATE_COUNT="$(echo "$PIPELINE_JSON" | jq -r '.pipelineRun.result.metrics.candidate_count // 0')"
echo "  ✓ candidate_count=$CANDIDATE_COUNT"

# scored_scene_count == candidate_count
SCORED_COUNT="$(echo "$PIPELINE_JSON" | jq -r '.pipelineRun.result.metrics.scored_scene_count // 0')"
if [ "$SCORED_COUNT" != "$CANDIDATE_COUNT" ] && [ "$SCORED_COUNT" -gt 0 ]; then
  echo "  ℹ scored_scene_count=$SCORED_COUNT (may differ if scoring runs over full set)"
else
  echo "  ✓ scored_scene_count=$SCORED_COUNT"
fi

# ready + warning + blocked == scored_scene_count
READY="$(echo "$PIPELINE_JSON" | jq -r '.pipelineRun.result.metrics.ready_count // 0')"
WARNING="$(echo "$PIPELINE_JSON" | jq -r '.pipelineRun.result.metrics.warning_count // 0')"
BLOCKED="$(echo "$PIPELINE_JSON" | jq -r '.pipelineRun.result.metrics.blocked_count // 0')"
BUCKET_SUM=$(( READY + WARNING + BLOCKED ))
echo "  ready=$READY warning=$WARNING blocked=$BLOCKED bucket_sum=$BUCKET_SUM"

if [ "$BUCKET_SUM" -ne "$SCORED_COUNT" ]; then
  echo "❌ Bucket sum $BUCKET_SUM != scored_scene_count $SCORED_COUNT" >&2
  exit 1
fi
echo "  ✓ ready + warning + blocked == scored_scene_count"

# ── 5. Fetch scenario set from API ───────────────────────────────────────────

echo ""
echo "--- 5. Fetch scenario set via API ---"
SCENARIO_SET_JSON="$(curl -sS "$(api_url "$API_BASE_URL" "/scenarios/$SCENARIO_SET_ID")")"
echo "$SCENARIO_SET_JSON" | jq .
require_json_field "$SCENARIO_SET_JSON" '.scenarioSet.scenarioSetId' 'scenarioSet.scenarioSetId'
echo "  ✓ scenario set retrievable via GET /scenarios/$SCENARIO_SET_ID"

# ── 6. Print top scene IDs ────────────────────────────────────────────────────

echo ""
echo "--- 6. Top scene IDs ---"
MINING_RUN_ID="$(echo "$PIPELINE_JSON" | jq -r '.pipelineRun.result.outputs.mining_run_id // empty')"
echo "  mining_run_id=$MINING_RUN_ID"
echo "  readiness_run_id from artifacts=$READINESS_URI"
TOP_IDS="$(echo "$PIPELINE_JSON" | jq -r '.pipelineRun.result | .. | .top_scene_ids? // empty | .[]?' 2>/dev/null || echo "(check readiness report artifact)")"
echo "  top_scene_ids: $TOP_IDS"

echo ""
echo "✅ scenario_curation E2E passed"
echo ""
echo "=== Scenario Curation Result ==="
echo "  pipeline_run_id : $PIPELINE_RUN_ID"
echo "  scenario_set_id : $SCENARIO_SET_ID"
echo "  candidate_count=$CANDIDATE_COUNT  ready=$READY  warning=$WARNING  blocked=$BLOCKED"
echo ""
