#!/usr/bin/env bash
# e2e_cleanroom.sh — the full-platform acceptance: from fresh platform state,
# SceneOps reconstructs its canonical baseline and runs its representative
# workflows through supported production paths only.
#
#   images                  api / worker / dataset-acquisition / LeRobot images
#                           built from the current tree
#   make local-reset        DESTRUCTIVE: fresh containers, fresh PostgreSQL /
#                           Redis / MinIO state; PRESERVES the external dataset
#                           fixture (data/raw)
#   canonical-bootstrap     RobotRun -> canonical Scenes and Episodes (L1/L2)
#   canonical-verify        the baseline, read back through the API
#   e2e-scene-ml            Scene ML journey on that baseline (L3)
#   e2e-episode-learning    Episode learning journey on that baseline (L3)
#   final verification      every pipeline run of the baseline succeeded, every
#                           job succeeded, the canonical records are exactly
#                           those the bootstrap registered, and the derived
#                           artifacts the journeys produced exist
#
# Control-plane operations go through FastAPI; bulk data moves through
# containers and the ArtifactStore. There is no direct SQL, no direct MinIO
# inspection, no host worker CLI and no host Python.
#
# Does not need a GPU, a real inference server, Airflow or Kafka: the
# model-backend, orchestrator and streaming acceptances are separate
# (make acceptance-grounding-dino, make test-infrastructure-airflow,
# make e2e-streaming-equivalence).
#
# Requires interactive confirmation (the reset is destructive) unless FORCE=1.
#
# Usage:
#   make e2e-cleanroom
#   FORCE=1 make e2e-cleanroom

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"
source "$SCRIPT_DIR/lib.sh"

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
export BASELINE_ID="${BASELINE_ID:-canonical}"
export SOURCE_UNITS="${SOURCE_UNITS:-${SOURCE_UNIT:-scene-0061}}"
source "$REPO_ROOT/scripts/canonical/baseline_lib.sh"

echo "=================================================================="
echo " e2e-cleanroom: full-platform acceptance from fresh platform state"
echo "=================================================================="
echo ""
echo "This will:"
echo "  1. build images          from the current tree (api, worker, acquisition, LeRobot)"
echo "  2. make local-reset      [DESTRUCTIVE] fresh Postgres/Redis/MinIO, preserve data/raw"
echo "  3. canonical-bootstrap   RobotRun -> canonical Scenes and Episodes ($BASELINE_ID)"
echo "  4. canonical-verify      the baseline through the API"
echo "  5. e2e-scene-ml          labels -> views -> ScenarioSet -> prediction -> evaluation"
echo "  6. e2e-episode-learning  alignment -> learning export -> LeRobot round trip"
echo "  7. final verification    through the API"
echo ""

echo "--- 1. images from the current tree ---"
make -C "$REPO_ROOT" compose-build acquisition-image lerobot-image
echo ""

echo "--- 2. make local-reset (fresh containers and platform state) ---"
FORCE="${FORCE:-0}" make -C "$REPO_ROOT" local-reset
make -C "$REPO_ROOT" status
echo ""

echo "--- 3. canonical-bootstrap ---"
BASELINE="$("$REPO_ROOT/scripts/canonical/canonical_bootstrap.sh")"
echo "$BASELINE" | jq -c .
echo ""

echo "--- 4. canonical-verify ---"
VERIFIED="$("$REPO_ROOT/scripts/canonical/canonical_verify.sh")"
check "the verified baseline is the bootstrapped one" [ "$VERIFIED" = "$BASELINE" ]
echo ""

echo "--- 5. e2e-scene-ml on the baseline ---"
make -C "$REPO_ROOT" e2e-scene-ml BASELINE_ID="$BASELINE_ID"
echo ""

echo "--- 6. e2e-episode-learning on the baseline ---"
make -C "$REPO_ROOT" e2e-episode-learning BASELINE_ID="$BASELINE_ID"
echo ""

echo "--- 7. final verification (through the API) ---"
BASELINE_SUMMARY="$BASELINE" "$SCRIPT_DIR/cleanroom_verify.sh"
echo ""

echo "=================================================================="
echo " PASSED: e2e-cleanroom"
echo "=================================================================="
echo "SceneOps reconstructed its canonical baseline ($DATASET_ID/$DATASET_VERSION) and ran"
echo "its Scene ML and Episode learning workflows from fresh platform state."
