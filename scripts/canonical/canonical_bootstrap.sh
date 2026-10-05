#!/usr/bin/env bash
# canonical_bootstrap.sh — developer/test orchestration (not a Pipeline) that
# builds the reproducible L1/L2 baseline on a fresh or existing stack:
#
#   dataset fixture (nuScenes mini, read-only)
#     -> dataset-acquisition container      finalized sensor-bearing MCAP
#     -> recording-publisher container      L1 conformance + publication
#     -> POST /robot-runs:register          RobotRun
#     -> recording_scene_building           canonical Scenes, validated, profiled
#     -> recording_episode_building         canonical Episodes, validated, profiled
#     -> baseline_verify                    read-only check through the API
#
# It builds nothing derived: no labels, sample views, ScenarioSets,
# predictions, evaluations or learning exports. Those are L3 workflows
# (make e2e-scene-ml / e2e-episode-learning) that run on top of a baseline.
#
# create-or-verify: a RobotRun that is already registered is reused (not
# re-acquired); a Scene / Episode build over an unchanged scope converges on
# the registered revisions; a changed producer or configuration fails loudly at
# registration instead of replacing canonical membership. Recovery from a
# mismatched baseline is an explicit `make local-reset` + rebuild.
#
# Identity (all overridable): BASELINE_ID (default `canonical`) names the
# robot, the RobotRuns (run-<BASELINE_ID>-<unit>) and the DatasetVersion
# (sceneops-<BASELINE_ID>/baseline). The journeys that mutate their scope use a
# unique BASELINE_ID per run; the default baseline is the persistent one.
#
# stdout: one JSON summary (baseline_verify). Progress goes to stderr, so
#   BASELINE="$(scripts/canonical/canonical_bootstrap.sh)"
# captures the summary.
#
# Prerequisites: `make local-up`; `make acquisition-image`; data/raw/nuscenes
# with v1.0-mini and can_bus (ACQUISITION_NUSCENES_ROOT overrides).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"
source "$REPO_ROOT/scripts/e2e/lib.sh"
source "$SCRIPT_DIR/baseline_lib.sh"

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"

log() { echo "$@" >&2; }

# build_scope <pipeline-type> <build-task> <register-task> <profile-task> <config>
# One pipeline run over one RobotRun's recording scope.
build_scope() {
  local type="$1" build_task="$2" register_task="$3" profile_task="$4" run_id="$5" config="$6"
  local params pipeline
  params="$(jq -cn --arg b "$build_task" --arg r "$register_task" --arg p "$profile_task" \
    --arg run "$run_id" --argjson config "$config" '{
      ($b): {robot_run_id: $run, build_config: $config},
      ($r): {replace: false},
      ($p): {triggered: true}}')"
  pipeline="$(run_pipeline "$API_BASE_URL" "$type" "$DATASET_ID" "$DATASET_VERSION" "$params")"
  assert_pipeline_succeeded "$(fetch_pipeline_run "$API_BASE_URL" "$pipeline")" \
    "$type for $run_id should succeed" "$API_BASE_URL" "$pipeline" >&2
  log "  ✅  $type ($run_id): $pipeline"
}

log "=== canonical baseline '$BASELINE_ID': $DATASET_ID/$DATASET_VERSION from $SOURCE_UNITS ==="
require_api "$API_BASE_URL"

for unit in $SOURCE_UNITS; do
  run_id="$(baseline_run_id "$unit")"
  if api_get "$API_BASE_URL" "/robot-runs/$run_id" >/dev/null 2>&1; then
    log "--- RobotRun $run_id is registered: reused"
    continue
  fi
  log "--- $unit -> acquisition container -> MCAP -> L1 conformance -> publish -> register"
  trap 'remove_recording "$run_id"' EXIT
  summary="$(acquire_recording "$SOURCE_VERSION" "$unit" "$run_id")"
  log "  $(echo "$summary" | jq -c '{sha256, size_bytes, message_count}')"
  check_recording "$run_id" || fail "recording of $unit is not L1-conformant"
  publication="$(publish_recording "$run_id" "$ROBOT_ID" file)"
  registration="$(register_robot_run "$API_BASE_URL" "$(echo "$publication" | jq -r '.manifest_uri')")"
  assert_job_succeeded "$registration" "REGISTER_ROBOT_RUN for $run_id should succeed" >&2
  remove_recording "$run_id"
  trap - EXIT
  log "  ✅  RobotRun $run_id registered"
done

upsert_dataset "$API_BASE_URL" "$DATASET_ID" "Canonical baseline $BASELINE_ID" >/dev/null
upsert_dataset_version "$API_BASE_URL" "$DATASET_ID" "$DATASET_VERSION" >/dev/null

for unit in $SOURCE_UNITS; do
  run_id="$(baseline_run_id "$unit")"
  log "--- canonical Scenes and Episodes of $run_id"
  build_scope recording_scene_building build_recording_scenes register_scenes profile_scene \
    "$run_id" "$(scene_build_config)"
  build_scope recording_episode_building build_recording_episodes register_episodes profile_episode \
    "$run_id" "$(episode_build_config)"
done

log "--- verify"
baseline_verify "$API_BASE_URL"
log "=== baseline '$BASELINE_ID' ready ==="
