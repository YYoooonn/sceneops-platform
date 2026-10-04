#!/usr/bin/env bash
# e2e_cleanroom.sh
#
# The ONLY full-platform acceptance workflow. Proves SceneOps can
# reconstruct its operational/canonical state from empty application state
# + real external raw data -- not from large synthetic fixtures.
#
#   make local-reset                 (destructive: fresh Postgres/Redis/
#                                      MinIO; PRESERVES data/raw/nuscenes,
#                                      the CAN bus expansion, and any other
#                                      external raw source data)
#     -> make local-up (run by local-reset itself)
#     -> e2e-recording-scene         (real nuScenes -> acquisition -> RobotRun
#                                      -> canonical Scenes -> validation/profile)
#     -> e2e-robot-learning           (real nuScenes CAN bus -> ROS2 ->
#                                      MCAP -> RosbagAdapter -> Episode ->
#                                      alignment -> EXPORT_LEARNING_DATA ->
#                                      curation)
#     -> final persisted-state validation (queries the real API for what
#        actually got written -- not just "each script exited 0")
#
# Not in the chain yet: e2e-perception (scenario curation -> detection ->
# evaluation) needs ground truth and a synchronized-sample view, which
# recording-derived Scenes do not have until ADR-007 implementation step 10;
# the chain is consolidated in step 11.
#
# Deliberately does NOT require GPU, Airflow, or the isolated LeRobot
# container -- those remain OPTIONAL verification paths, run separately:
#   make verify-airflow-backend                  (needs airflow-up)
#   make e2e-interop                             (needs lerobot-sync)
#
# This is DESTRUCTIVE to local Postgres/Redis/MinIO state. Requires
# interactive confirmation (same as `make local-reset`) unless FORCE=1.
#
# Usage:
#   make e2e-cleanroom
#   FORCE=1 make e2e-cleanroom

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=================================================================="
echo " e2e-cleanroom: full-platform acceptance from empty SceneOps state"
echo "=================================================================="
echo ""
echo "This will:"
echo "  1. make local-reset   [DESTRUCTIVE] wipe Postgres/Redis/MinIO,"
echo "                        preserve data/raw/nuscenes + CAN bus expansion"
echo "  2. e2e-recording-scene  nuScenes -> RobotRun -> canonical Scenes -> quality"
echo "  3. e2e-robot-learning real CAN bus -> ROS2 -> MCAP -> Episode -> learning export"
echo "  4. final persisted-state validation"
echo ""

echo "--- 1. make local-reset ---"
FORCE="${FORCE:-0}" make -C "$REPO_ROOT" local-reset
echo ""

echo "--- 2. Verify stack healthy ---"
make -C "$REPO_ROOT" status
echo ""

echo "--- 3. e2e-recording-scene ---"
make -C "$REPO_ROOT" e2e-recording-scene
echo ""

echo "--- 4. e2e-robot-learning ---"
bash "$SCRIPT_DIR/e2e_robot_learning.sh"
echo ""

# ── 5. Final persisted-state validation ──────────────────────────────────────
# Queries the real, persisted API state directly -- proof this is a
# reconstruction from empty state + real data, not just "every script
# happened to exit 0".

echo "--- 5. Final persisted-state validation ---"
API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
source "$SCRIPT_DIR/lib.sh"
resolve_e2e_fixture core

# e2e-recording-scene registers into its own dataset, one fresh version per run.
SCENE_DATASET_ID="test-e2e-recording-scene"
SCENE_VERSION="$(curl -sS "$(api_url "$API_BASE_URL" "/datasets/$SCENE_DATASET_ID/versions?limit=500")" \
  | jq -r '.versions | sort_by(.createdAt) | last | .version // empty')"
QUALITY_JSON="$(curl -sS "$(api_url "$API_BASE_URL" "/datasets/$SCENE_DATASET_ID/versions/$SCENE_VERSION/quality")")"
EPISODES_JSON="$(curl -sS "$(api_url "$API_BASE_URL" "/episodes?dataset_id=$DATASET_ID&dataset_version=$DATASET_VERSION&limit=200")")"

SCENE_READINESS="$(echo "$QUALITY_JSON" | jq -r '.readiness')"
SCENE_COUNT="$(echo "$QUALITY_JSON" | jq -r '.counts.sceneCount // 0')"
EPISODE_COUNT="$(echo "$EPISODES_JSON" | jq -r '.count // 0')"

echo "  $SCENE_DATASET_ID/$SCENE_VERSION quality.readiness = $SCENE_READINESS"
echo "  scene_count                       = $SCENE_COUNT"
echo "  episode_count                     = $EPISODE_COUNT"

[ "$SCENE_READINESS" = "ready" ] || { echo "❌ Expected recording-scene dataset version readiness=ready, got $SCENE_READINESS" >&2; exit 1; }
[ "${SCENE_COUNT:-0}" -ge 1 ] || { echo "❌ Expected scene_count >= 1" >&2; exit 1; }
[ "${EPISODE_COUNT:-0}" -ge 1 ] || { echo "❌ Expected episode_count >= 1" >&2; exit 1; }

echo "  OK — SceneOps reconstructed its canonical Scene and Episode state from"
echo "       empty application state + real external nuScenes/CAN-bus data."
echo ""

echo "=================================================================="
echo " PASSED: e2e-cleanroom"
echo "=================================================================="
echo ""
echo "Optional follow-up verification (not required for this to pass):"
echo "  make verify-airflow-backend                  (needs airflow-up)"
echo "  make e2e-interop                              (needs lerobot-sync)"
