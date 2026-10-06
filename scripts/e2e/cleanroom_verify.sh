#!/usr/bin/env bash
# cleanroom_verify.sh — the final verification of e2e-cleanroom, through the API
# only: after canonical-bootstrap and both L3 journeys ran on the baseline, the
# canonical records are exactly those the bootstrap registered, every pipeline
# run of the baseline succeeded, no job failed, and the derived artifacts the
# journeys produce exist.
#
# BASELINE_SUMMARY is the JSON canonical-bootstrap printed; L3_DATASET_ID is the
# DatasetVersion (version `baseline`) the journeys wrote into. It is also runnable on
# its own after the journeys have run:
#   BASELINE_SUMMARY="$(scripts/canonical/canonical_verify.sh)" L3_DATASET_ID=<id> scripts/e2e/cleanroom_verify.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"
source "$SCRIPT_DIR/lib.sh"

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
# The fixture the L3 journeys ran on (their SOURCE_UNIT).
UNIT="${SOURCE_UNIT:-scene-0061}"
source "$REPO_ROOT/scripts/canonical/baseline_lib.sh"
[ -n "${BASELINE_SUMMARY:-}" ] || fail "BASELINE_SUMMARY (the canonical-bootstrap JSON) is required"
[ -n "${L3_DATASET_ID:-}" ] || fail "L3_DATASET_ID (the DatasetVersion the L3 journeys wrote into) is required"

AFTER="$("$REPO_ROOT/scripts/canonical/canonical_verify.sh")"
check "the canonical records are exactly those the bootstrap registered" [ "$AFTER" = "$BASELINE_SUMMARY" ]

RUNS="$(api_get "$API_BASE_URL" "/pipelines/runs?dataset_id=$L3_DATASET_ID&dataset_version=$DATASET_VERSION&limit=500")"
echo "$RUNS" | jq -r '.pipelineRuns | group_by(.type) | .[] | "  \(.[0].type): \(length) run(s), statuses=\([.[].status] | unique | join(","))"'
check "every pipeline type of the journeys ran" \
  [ "$(echo "$RUNS" | jq -r '[.pipelineRuns[].type] | unique | sort | join(",")')" = "episode_learning_data_building,recording_episode_building,recording_scene_building,scene_ml_evaluation" ]
check "no pipeline run of the journeys failed or is blocked" \
  [ "$(echo "$RUNS" | jq '[.pipelineRuns[] | select(.status != "succeeded")] | length')" = 0 ]
JOBS="$(api_get "$API_BASE_URL" "/jobs?dataset_id=$L3_DATASET_ID&dataset_version=$DATASET_VERSION&limit=500")"
check "no job of the journeys failed" \
  [ "$(echo "$JOBS" | jq '[.jobs[] | select(.status == "failed")] | length')" = 0 ]
for job_type in import_labels build_scene_sample_views predict_detection evaluate_detection \
  validate_aligned_episode profile_aligned_episode; do
  check "the $job_type atomic job ran" \
    [ "$(echo "$JOBS" | jq --arg t "$job_type" '[.jobs[] | select(.type == $t and .status == "succeeded")] | length')" -ge 1 ]
done

for kind in scene_manifest episode_manifest scene_sample_view_manifest \
  scenario_set_manifest prediction_manifest aligned_episode_manifest learning_data_export_manifest; do
  count="$(api_get "$API_BASE_URL" "/artifacts?kind=$kind&dataset_id=$L3_DATASET_ID&dataset_version=$DATASET_VERSION&limit=1" | jq '.artifacts | length')"
  check "$kind artifacts are registered" [ "$count" -ge 1 ]
done
# A LabelSet is independent of any DatasetVersion: it is owned by the label set.
check "the label set revision is registered under its own label set" \
  [ "$(api_get "$API_BASE_URL" "/artifacts?kind=label_set_manifest&owner_type=label_set&owner_id=labels-$L3_DATASET_ID-$UNIT&limit=1" | jq '.artifacts | length')" -ge 1 ]
check "observation payloads are registered on the reference RobotRun" \
  [ "$(count_artifacts "$API_BASE_URL" observation_payload robot_run "$(baseline_run_id "$UNIT")")" -ge 1 ]
echo ""
