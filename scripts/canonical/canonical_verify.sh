#!/usr/bin/env bash
# canonical_verify.sh
#
# Read-only verification of the frozen v0.0 canonical baseline family
# (sceneops-scenes/v0.0, sceneops-episodes/v0.0, sceneops-canonical/v0.0)
# against config/baselines/canonical-v0.0.yaml. Never dispatches a pipeline
# or job, never writes to Postgres/MinIO -- every check is a GET plus a
# MinIO/ArtifactStore byte read. Fails (non-zero exit) if any of the three
# baselines is absent, partially materialized, or disagrees with the
# contract; does not attempt to create or repair anything (see
# canonical_bootstrap.sh for that).
#
# Usage:
#   make canonical-verify
#   bash scripts/canonical/canonical_verify.sh
#
# Env overrides:
#   API_BASE_URL   (default: http://localhost:8000)
#   API_PREFIX     (default: /api/v1)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
source "$REPO_ROOT/scripts/e2e/lib.sh"
source "$SCRIPT_DIR/canonical_contract.sh"

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
API_PREFIX="${API_PREFIX:-/api/v1}"

echo "=== canonical-verify: v0.0 baseline family (read-only) ==="
load_baseline_spec
echo "  baseline_version=$BASELINE_VERSION"
echo "  $SCENES_DATASET_ID/$SCENES_DATASET_VERSION  $EPISODES_DATASET_ID/$EPISODES_DATASET_VERSION  $CANONICAL_DATASET_ID/$CANONICAL_DATASET_VERSION"
echo ""

echo "--- Contract presence (create-or-verify status) ---"
STATUS_SCENES="$(contract_status "$SCENES_DATASET_ID" "$SCENES_DATASET_VERSION" "$SCENES_EXPECTED_SCENE_COUNT" "$SCENES_EXPECTED_EPISODE_COUNT")"
STATUS_EPISODES="$(contract_status "$EPISODES_DATASET_ID" "$EPISODES_DATASET_VERSION" "$EPISODES_EXPECTED_SCENE_COUNT" "$EPISODES_EXPECTED_EPISODE_COUNT")"
STATUS_CANONICAL="$(contract_status "$CANONICAL_DATASET_ID" "$CANONICAL_DATASET_VERSION" "$CANONICAL_EXPECTED_SCENE_COUNT" "$CANONICAL_EXPECTED_EPISODE_COUNT")"
echo "  $SCENES_DATASET_ID/$SCENES_DATASET_VERSION: $STATUS_SCENES"
echo "  $EPISODES_DATASET_ID/$EPISODES_DATASET_VERSION: $STATUS_EPISODES"
echo "  $CANONICAL_DATASET_ID/$CANONICAL_DATASET_VERSION: $STATUS_CANONICAL"
echo ""

for status in "$STATUS_SCENES" "$STATUS_EPISODES" "$STATUS_CANONICAL"; do
  if [ "$status" != "matches" ]; then
    echo "❌ canonical-verify requires the full v0.0 baseline family to already be materialized and matching." >&2
    echo "   Run 'make canonical-bootstrap' first (it will create what's missing, or fail loudly on partial/mismatched state)." >&2
    exit 1
  fi
done

echo "--- Deep verification (ownership, quality, artifacts, learning export, SceneOpsDataset reopen) ---"
if ! verify_full_contract; then
  echo "" >&2
  echo "❌ canonical-verify FAILED" >&2
  exit 1
fi

echo ""
echo "=== canonical-verify PASSED (read-only, no mutation) ==="
