#!/usr/bin/env bash
# canonical_bootstrap.sh — developer/test orchestration (not a Pipeline) that
# builds the reproducible L1/L2 baseline of a reference-corpus selection on a
# fresh or existing stack:
#
#   reference corpus (corpus.json + corpus.lock.json), prepared recordings
#     -> reference resolve                  fixtures + their verified, locked
#                                           recordings (nothing is converted)
#     -> recording-publisher container      publish, read in place from the
#                                           read-only reference mount
#     -> POST /robot-runs:register          one RobotRun per fixture, each pinning
#                                           exactly the locked recording sha256
#     -> recording_scene_building           canonical Scenes, validated, profiled
#     -> recording_episode_building         canonical Episodes, validated, profiled
#     -> baseline_verify                    read-only check through the API
#
# It builds nothing derived: no labels, sample views, ScenarioSets,
# predictions, evaluations or learning exports. Those are L3 workflows
# (make e2e-scene-ml / e2e-episode-learning) that run on top of a baseline.
#
# Selection: REFERENCE_SCOPE (default smoke-1; nuscenes-mini-full-10) or FIXTURE
# (one fixture). Scene and Episode builds run independently per RobotRun with
# the build configurations of config/baselines/; output counts are whatever each
# fixture yields.
#
# create-or-verify: a RobotRun that is already registered is reused (and must pin
# the locked recording); a Scene / Episode build over an unchanged scope
# converges on the registered revisions; a changed producer or configuration
# fails loudly at registration instead of replacing canonical membership.
# Recovery from a mismatched baseline is an explicit `make local-reset` + rebuild.
#
# Identity (all overridable): BASELINE_ID (default `ref-<scope>`, or
# `ref-<fixture>` for FIXTURE) names the robot, the RobotRuns
# (run-<BASELINE_ID>-<fixture>) and the DatasetVersion (sceneops-<BASELINE_ID>/
# baseline). Smoke and full are different baselines. The journeys that mutate
# their scope use a unique BASELINE_ID per run.
#
# stdout: one JSON summary (baseline_verify). Progress goes to stderr, so
#   BASELINE="$(scripts/canonical/canonical_bootstrap.sh)"
# captures the summary.
#
# Prerequisites: `make local-up`; the reference recordings prepared by
# `make reference-data-bootstrap REFERENCE_SCOPE=<scope>`; the dataset-acquisition
# image (the resolver) and the worker image (the publisher).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"
source "$REPO_ROOT/scripts/e2e/lib.sh"
source "$SCRIPT_DIR/baseline_lib.sh"

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"

log "=== canonical baseline '$BASELINE_ID': $DATASET_ID/$DATASET_VERSION from $REFERENCE_CORPUS (${FIXTURE:-$REFERENCE_SCOPE}) ==="
require_api "$API_BASE_URL"

log "--- reference corpus: fixtures and cached recordings, verified against the lock"
baseline_resolve full
log "  $(echo "$BASELINE_FIXTURES" | jq -c '[.[].fixture_id]')"

# Collected into an array first: a docker container inside a `while read` loop
# would consume the remaining fixtures from stdin. (No mapfile: the host bash may
# be 3.2.)
fixtures=()
while IFS= read -r fixture; do fixtures+=("$fixture"); done < <(echo "$BASELINE_FIXTURES" | jq -c '.[]')
for fixture in "${fixtures[@]}"; do
  baseline_register_fixture "$fixture"
done

upsert_dataset "$API_BASE_URL" "$DATASET_ID" "Canonical baseline $BASELINE_ID" >/dev/null
upsert_dataset_version "$API_BASE_URL" "$DATASET_ID" "$DATASET_VERSION" >/dev/null

for id in $(echo "$BASELINE_FIXTURES" | jq -r '.[].fixture_id'); do
  run_id="$(baseline_run_id "$id")"
  log "--- canonical Scenes and Episodes of $run_id"
  baseline_build_scope recording_scene_building build_recording_scenes register_scenes profile_scene \
    "$run_id" "$(scene_build_config)"
  baseline_build_scope recording_episode_building build_recording_episodes register_episodes profile_episode \
    "$run_id" "$(episode_build_config)"
done

log "--- verify"
baseline_verify "$API_BASE_URL"
log "=== baseline '$BASELINE_ID' ready ==="
