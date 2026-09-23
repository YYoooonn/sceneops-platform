#!/usr/bin/env bash
# e2e_lerobot_roundtrip.sh (SceneOps V2 Request 3.4)
#
# Real end-to-end round-trip:
#
#   persistent "interop" SceneOps fixture (real Postgres + MinIO)
#           -> SceneOpsDataset
#           -> LeRobotDatasetAdapter (tools/lerobot-integration, isolated)
#           -> real LeRobot v3 dataset on disk
#           -> official LeRobot reader
#           -> golden semantic comparison (sceneops_analytics.testing.interop_dataset)
#
# Two Python processes, two venvs, on purpose (SceneOps V2 Request 3.3A's
# dependency isolation is frozen, not renegotiated here):
#
#   Step 1  scripts/e2e/e2e_lerobot_resolve.py, run from the MAIN workspace
#           venv (needs sceneops-db for the real Postgres ArtifactRecord
#           lookup). Ensures/verifies the interop fixture
#           (e2e_fixture_bootstrap.ensure_e2e_fixture, never re-derived
#           here) and prints its manifest's real uri+checksum as JSON.
#
#   Step 2  scripts/e2e/e2e_lerobot_export.py, run from
#           tools/lerobot-integration's isolated venv (has lerobot +
#           sceneops-analytics/core/storage, never sceneops-db). Given only
#           that uri+checksum, independently reopens the real persistent
#           SceneOpsDataset, exports it, reopens the LeRobot output with
#           LeRobot's own official reader, and verifies everything.
#
# Usage:
#   bash scripts/e2e/e2e_lerobot_roundtrip.sh
#
# Prerequisites (this script does not do either of these for you):
#   make local-up                    # Postgres + MinIO
#   make lerobot-sync                # tools/lerobot-integration/.venv, one-time
#
# Env overrides (defaults match scripts/e2e/bootstrap_e2e_fixtures.py /
# makefiles/e2e.mk's E2E_BOOTSTRAP_ENV):
#   SCENEOPS_DATABASE_URL   real Postgres DSN (host form, not the
#                           container-internal "postgres" hostname)
#   MINIO_ENDPOINT_URL      (default: http://localhost:9000)
#   MINIO_ROOT_USER         (default: minioadmin)
#   MINIO_ROOT_PASSWORD     (default: minioadmin)
#   MINIO_BUCKET            (default: sceneops)
#   LEROBOT_REPO_ID         (default: test-e2e-lerobot-interop)
#   LEROBOT_OUTPUT_DIR      (default: <repo_root>/data/runs/e2e-lerobot)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
source "$SCRIPT_DIR/lib.sh"

resolve_e2e_fixture interop

MINIO_ENDPOINT_URL="${MINIO_ENDPOINT_URL:-http://localhost:9000}"
MINIO_ROOT_USER="${MINIO_ROOT_USER:-minioadmin}"
MINIO_ROOT_PASSWORD="${MINIO_ROOT_PASSWORD:-minioadmin}"
MINIO_BUCKET="${MINIO_BUCKET:-sceneops}"
LEROBOT_REPO_ID="${LEROBOT_REPO_ID:-test-e2e-lerobot-interop}"

echo "=== LeRobot round-trip E2E ==="
echo "  DATASET_ID=$DATASET_ID  DATASET_VERSION=$DATASET_VERSION"
echo "  LEROBOT_REPO_ID=$LEROBOT_REPO_ID"
echo ""

echo "--- 1. Resolve real persistent interop fixture (main workspace venv) ---"
cd "$REPO_ROOT"
RESOLVE_JSON="$(
  SCENEOPS_DATABASE_URL="${SCENEOPS_DATABASE_URL:-}" \
  MINIO_ENDPOINT_URL="$MINIO_ENDPOINT_URL" \
  MINIO_ROOT_USER="$MINIO_ROOT_USER" \
  MINIO_ROOT_PASSWORD="$MINIO_ROOT_PASSWORD" \
  MINIO_BUCKET="$MINIO_BUCKET" \
  uv run python scripts/e2e/e2e_lerobot_resolve.py
)"

MANIFEST_URI="$(echo "$RESOLVE_JSON" | jq -r '.manifest_uri')"
MANIFEST_CHECKSUM="$(echo "$RESOLVE_JSON" | jq -r '.manifest_checksum')"

if [ -z "$MANIFEST_URI" ] || [ "$MANIFEST_URI" = "null" ]; then
  echo "❌ Step 1 did not return a manifest_uri" >&2
  echo "$RESOLVE_JSON" >&2
  exit 1
fi
echo "  manifest_uri=$MANIFEST_URI"
echo "  manifest_checksum=$MANIFEST_CHECKSUM"
echo ""

echo "--- 2. Export + official read-back + golden comparison (tools/lerobot-integration, isolated venv) ---"
cd "$REPO_ROOT/tools/lerobot-integration"
EXPORT_JSON="$(
  MINIO_ENDPOINT_URL="$MINIO_ENDPOINT_URL" \
  MINIO_ROOT_USER="$MINIO_ROOT_USER" \
  MINIO_ROOT_PASSWORD="$MINIO_ROOT_PASSWORD" \
  MINIO_BUCKET="$MINIO_BUCKET" \
  uv run python ../../scripts/e2e/e2e_lerobot_export.py \
    --manifest-uri "$MANIFEST_URI" \
    --manifest-checksum "$MANIFEST_CHECKSUM" \
    --repo-id "$LEROBOT_REPO_ID" \
    ${LEROBOT_OUTPUT_DIR:+--output-dir "$LEROBOT_OUTPUT_DIR"}
)"
cd "$REPO_ROOT"
echo ""

echo "=== PASSED ==="
echo "$EXPORT_JSON" | jq -r '"  export_root=" + .export_root, "  exported_episode_count=" + (.report.exported_episode_count | tostring), "  exported_step_count=" + (.report.exported_step_count | tostring), "  total_frames_readback=" + (.readback.total_frames | tostring)'
