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
# (one fixture) chooses which fixtures are acted on. Scene and Episode builds run
# independently per RobotRun with the build configurations of config/baselines/;
# output counts are whatever each fixture yields.
#
# create-or-verify: a RobotRun that is already registered is reused (and must pin
# the locked recording); a RobotRun whose Scenes and Episodes are all there,
# validated, profiled and ready is reused as it is, with no build pipeline run
# over it; an incomplete one converges on the registered revisions; a changed
# producer or configuration fails loudly at registration instead of replacing
# canonical membership. The cached recordings are hashed against the lock only
# when a RobotRun has to be published. Recovery from a mismatched baseline is an
# explicit `make local-reset` + rebuild.
#
# Identity: the golden reference contract's recording_import baseline
# (`ref-nuscenes-mini-full-10`: robot, RobotRuns run-<BASELINE_ID>-<fixture>,
# DatasetVersion sceneops-<BASELINE_ID>/baseline) whatever the selection: smoke-1
# is scene-0061 of that baseline, not another one. A different BASELINE_ID names
# a baseline of non-contract RobotRuns and needs a disposable runtime
# (DISPOSABLE_RUNTIME=1). DATASET_ID may name another DatasetVersion: the
# Scenes and Episodes of the (contract) RobotRuns are then built into it and no
# RobotRun is created.
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
baseline_guard_identity

log "--- reference corpus: locked facts of the selection"
baseline_resolve lock-only
if ! baseline_all_registered; then
  log "--- a RobotRun has to be published: cached recordings verified against the lock"
  baseline_resolve full
fi
log "  $(echo "$BASELINE_FIXTURES" | jq -c '[.[].fixture_id]')"

# Collected into an array first: a docker container inside a `while read` loop
# would consume the remaining fixtures from stdin. (No mapfile: the host bash may
# be 3.2.)
fixtures=()
while IFS= read -r fixture; do fixtures+=("$fixture"); done < <(echo "$BASELINE_FIXTURES" | jq -c '.[]')
for fixture in "${fixtures[@]}"; do
  baseline_register_fixture "$fixture"
done

baseline_ensure_dataset "Canonical baseline $BASELINE_ID"

for id in $(echo "$BASELINE_FIXTURES" | jq -r '.[].fixture_id'); do
  run_id="$(baseline_run_id "$id")"
  if baseline_fixture_complete "$run_id"; then
    log "--- Scenes and Episodes of $run_id are built and ready: reused"
    continue
  fi
  log "--- canonical Scenes and Episodes of $run_id"
  baseline_build_scope recording_scene_building build_recording_scenes register_scenes profile_scene \
    "$run_id" "$(scene_build_config)"
  baseline_build_scope recording_episode_building build_recording_episodes register_episodes profile_episode \
    "$run_id" "$(episode_build_config)"
done

log "--- verify"
baseline_verify "$API_BASE_URL"
log "=== baseline '$BASELINE_ID' ready ==="
