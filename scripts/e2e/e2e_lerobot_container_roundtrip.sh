#!/usr/bin/env bash
# e2e_lerobot_container_roundtrip.sh (SceneOps V2 Request 4.3)
#
# Containerized counterpart to scripts/e2e/e2e_lerobot_roundtrip.sh
# (Request 3.4) -- same real end-to-end round-trip, same frozen golden
# expectations (scripts/e2e/lerobot_roundtrip_golden.py, shared unchanged),
# but the export itself runs through the LeRobot integration CONTAINER
# (tools/lerobot-integration/Dockerfile, Request 4.2) instead of a second
# host-side isolated-venv process:
#
#   persistent "interop" SceneOps fixture (real Postgres + MinIO)
#           -> IntegrationRequest (Request 4.1/4.1A)
#           -> LeRobot integration container (Request 4.2, isolated, no DB)
#           -> IntegrationResult
#           -> real LeRobot v3 dataset on disk (host-mounted)
#           -> official LeRobot reader (host-side, isolated venv)
#           -> golden semantic comparison (same oracle as Request 3.4)
#
# Three steps, three environments, on purpose (SceneOps V2 Request 3.3A's
# dependency isolation is frozen, not renegotiated here; Request 4.2's
# container runtime ownership -- no DB, no ArtifactRecord writes, never
# deletes external_ref.uri -- is frozen too):
#
#   Step 1  scripts/e2e/lerobot_container_build_request.py, run from the
#           MAIN workspace venv (needs sceneops-db). Resolves/verifies the
#           real persistent interop fixture and prints one
#           IntegrationRequest as JSON -- unchanged from Request 4.2.
#
#   Step 2  `docker run` the built lerobot-integration image. Given only
#           that IntegrationRequest JSON and SCENEOPS_INTEGRATION_
#           ARTIFACT__* env vars, independently re-fetches+re-verifies the
#           manifest, opens the real persistent SceneOpsDataset, and
#           exports it -- never touching Postgres/sceneops-db, never
#           deleting its export target (Request 4.2 follow-up §1).
#
#   Step 3  scripts/e2e/e2e_lerobot_container_verify.py, run from
#           tools/lerobot-integration's isolated venv (has lerobot, never
#           sceneops-db) -- the SAME environment the host E2E's own Step 2
#           already uses for this exact purpose (Request 4.3 §4: no new
#           read-back service/runtime layer). Verifies the container's
#           IntegrationResult, reconstructs its ExternalExportReport, and
#           reopens the exported dataset with LeRobot's own official
#           reader -- identical assertions to e2e_lerobot_export.py's, via
#           the same shared lerobot_roundtrip_golden.py oracle.
#
#   Step 4  Re-run the container directly against the now-populated target
#           (no cleanup in between) and verify it fails clearly, with a
#           non-zero exit and the target's file listing byte-for-byte
#           unchanged (SceneOps V2 Request 4.3 §5/§7's "existing export
#           target" failure-path coverage) -- proving the container-level
#           behavior Request 4.2's own follow-up only unit-tested via
#           execute() directly, this time through the real docker/CLI
#           boundary.
#
# This script owns cleanup of its own known test-owned output directory
# (below, before Step 1) -- the container itself never deletes
# external_ref.uri; a fresh path every run is what makes reruns of THIS
# script idempotent, never something the container does on its own (see
# also scripts/e2e/lerobot_container_smoke.sh, Request 4.2's lighter-
# weight smoke test of the same container).
#
# Usage:
#   bash scripts/e2e/e2e_lerobot_container_roundtrip.sh
#
# Prerequisites (this script does not do any of these for you):
#   make local-up          # Postgres + MinIO, on the sceneops-network
#   make lerobot-image      # builds sceneops-platform/lerobot-integration:local
#   make lerobot-sync       # tools/lerobot-integration/.venv, one-time (Step 3)
#
# Env overrides (defaults match scripts/e2e/e2e_lerobot_roundtrip.sh /
# scripts/e2e/lerobot_container_smoke.sh):
#   SCENEOPS_DATABASE_URL       real Postgres DSN (host form)
#   MINIO_ENDPOINT_URL          host-form MinIO URL for Step 1 (default: http://localhost:9000)
#   MINIO_ROOT_USER / MINIO_ROOT_PASSWORD / MINIO_BUCKET   (Step 1, and used to derive
#                               Step 2/4's SCENEOPS_INTEGRATION_ARTIFACT__* container env)
#   LEROBOT_CONTAINER_IMAGE     (default: sceneops-platform/lerobot-integration:local)
#   LEROBOT_CONTAINER_NETWORK   (default: sceneops-network -- must already exist, from `make local-up`)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
source "$SCRIPT_DIR/lib.sh"

resolve_e2e_fixture interop

MINIO_ROOT_USER="${MINIO_ROOT_USER:-minioadmin}"
MINIO_ROOT_PASSWORD="${MINIO_ROOT_PASSWORD:-minioadmin}"
MINIO_BUCKET="${MINIO_BUCKET:-sceneops}"
LEROBOT_CONTAINER_IMAGE="${LEROBOT_CONTAINER_IMAGE:-sceneops-platform/lerobot-integration:local}"
LEROBOT_CONTAINER_NETWORK="${LEROBOT_CONTAINER_NETWORK:-sceneops-network}"
REPO_ID="test-e2e-lerobot-container-roundtrip"

IO_DIR="$REPO_ROOT/data/runs/e2e-lerobot-container/_io"
EXPORT_ROOT_HOST="$REPO_ROOT/data/runs/e2e-lerobot-container/$REPO_ID"
EXPORT_ROOT_CONTAINER="/data/runs/e2e-lerobot-container/$REPO_ID"

rm -rf "$IO_DIR" "$EXPORT_ROOT_HOST"
mkdir -p "$IO_DIR"

run_container() {
  # $1 = output filename (under $IO_DIR)
  docker run --rm \
    --network "$LEROBOT_CONTAINER_NETWORK" \
    -e SCENEOPS_INTEGRATION_ARTIFACT__BACKEND="minio" \
    -e SCENEOPS_INTEGRATION_ARTIFACT__ROOT_URI="s3://$MINIO_BUCKET/artifacts" \
    -e SCENEOPS_INTEGRATION_ARTIFACT__ENDPOINT_URL="http://minio:9000" \
    -e SCENEOPS_INTEGRATION_ARTIFACT__ACCESS_KEY_ID="$MINIO_ROOT_USER" \
    -e SCENEOPS_INTEGRATION_ARTIFACT__SECRET_ACCESS_KEY="$MINIO_ROOT_PASSWORD" \
    -v "$REPO_ROOT/data:/data" \
    "$LEROBOT_CONTAINER_IMAGE" \
    --request-file "/data/runs/e2e-lerobot-container/_io/request.json" \
    --output-file "/data/runs/e2e-lerobot-container/_io/$1"
}

echo "=== LeRobot containerized round-trip E2E ==="
echo "  DATASET_ID=$DATASET_ID  DATASET_VERSION=$DATASET_VERSION"
echo "  image=$LEROBOT_CONTAINER_IMAGE  network=$LEROBOT_CONTAINER_NETWORK"
echo ""

echo "--- 1. Build IntegrationRequest (main workspace venv, has sceneops-db) ---"
cd "$REPO_ROOT"
SCENEOPS_DATABASE_URL="${SCENEOPS_DATABASE_URL:-}" \
MINIO_ENDPOINT_URL="${MINIO_ENDPOINT_URL:-http://localhost:9000}" \
MINIO_ROOT_USER="$MINIO_ROOT_USER" \
MINIO_ROOT_PASSWORD="$MINIO_ROOT_PASSWORD" \
MINIO_BUCKET="$MINIO_BUCKET" \
uv run python scripts/e2e/lerobot_container_build_request.py \
  --export-root-uri "$EXPORT_ROOT_CONTAINER" \
  --repo-id "$REPO_ID" \
  >"$IO_DIR/request.json"
echo "  wrote $IO_DIR/request.json"
echo ""

echo "--- 2. Export via the LeRobot integration container (isolated, no DB) ---"
run_container "result.json"
echo "  wrote $IO_DIR/result.json"
echo ""

echo "--- 3. Verify IntegrationResult + official LeRobot read-back (isolated venv) ---"
cd "$REPO_ROOT/tools/lerobot-integration"
VERIFY_JSON="$(
  uv run python ../../scripts/e2e/e2e_lerobot_container_verify.py \
    --result-file "$IO_DIR/result.json" \
    --export-root-host "$EXPORT_ROOT_HOST" \
    --export-root-container "$EXPORT_ROOT_CONTAINER" \
    --dataset-id "$DATASET_ID" \
    --dataset-version "$DATASET_VERSION"
)"
cd "$REPO_ROOT"
echo ""

echo "--- 4. Verify a direct container rerun against the now-populated target fails clearly, target untouched ---"
BEFORE_LISTING="$(find "$EXPORT_ROOT_HOST" -type f | sort)"
set +e
run_container "rerun-result.json" >"$IO_DIR/rerun-stdout.log" 2>"$IO_DIR/rerun-stderr.log"
RERUN_EXIT=$?
set -e

if [ "$RERUN_EXIT" -eq 0 ]; then
  echo "❌ container rerun against an existing target unexpectedly succeeded" >&2
  exit 1
fi
if ! grep -q "already exists" "$IO_DIR/rerun-stderr.log"; then
  echo "❌ container rerun failure did not mention 'already exists':" >&2
  cat "$IO_DIR/rerun-stderr.log" >&2
  exit 1
fi
AFTER_LISTING="$(find "$EXPORT_ROOT_HOST" -type f | sort)"
if [ "$BEFORE_LISTING" != "$AFTER_LISTING" ]; then
  echo "❌ export target's file listing changed after the failed rerun -- the container must never touch a caller-owned target it refuses to write" >&2
  exit 1
fi
echo "  OK (rerun exit=$RERUN_EXIT, target file listing unchanged, no rerun-result.json written)"
echo ""

echo "=== PASSED ==="
echo "$VERIFY_JSON" | jq -r '"  export_root=" + .export_root, "  exported_episode_count=" + (.report.exported_episode_count | tostring), "  exported_step_count=" + (.report.exported_step_count | tostring), "  total_frames_readback=" + (.readback.total_frames | tostring)'
