#!/usr/bin/env bash
# lerobot_container_smoke.sh (SceneOps V2 Request 4.2 §7)
#
# Minimal container-level smoke test for the LeRobot integration container
# (tools/lerobot-integration/Dockerfile): build image -> start container ->
# parse a real IntegrationRequest -> access a real MinIO ArtifactStore ->
# run a real LeRobotDatasetAdapter.export() -> return a valid
# IntegrationResult. Deliberately NOT the golden round-trip comparison
# scripts/e2e/e2e_lerobot_roundtrip.sh already owns (Request 3.4) and
# Request 4.3 will own containerized -- this only proves the container
# itself executes the frozen IntegrationRequest -> IntegrationResult
# contract end-to-end, without duplicating every per-frame/official-reader
# assertion.
#
# Two processes, like every other scripts/e2e/e2e_lerobot_*.py split
# (Request 3.3A's dependency isolation, unchanged):
#
#   Step 1  scripts/e2e/lerobot_container_build_request.py, run from the
#           MAIN workspace venv (needs sceneops-db). Resolves the real
#           persistent "interop" fixture and prints one IntegrationRequest
#           as JSON.
#
#   Step 2  `docker run` the built lerobot-integration image. Given only
#           that IntegrationRequest JSON (mounted as a file) and
#           SCENEOPS_INTEGRATION_ARTIFACT__* env vars (the container's own
#           environment-driven ArtifactStore configuration -- see
#           sceneops_analytics.external_adapters.lerobot.entrypoint.
#           LeRobotContainerSettings), it independently re-fetches+
#           re-verifies the manifest, opens the real persistent
#           SceneOpsDataset, and exports it -- never touching Postgres or
#           sceneops-db (the image has neither installed).
#
# This script owns the ONE known test-owned output directory's cleanup
# (below, before Step 1) -- the container itself never deletes an
# external_ref.uri target (see entrypoint.py's own docstring); rerunning
# this script is what makes repeated runs idempotent, not the container.
#
# Usage:
#   bash scripts/e2e/lerobot_container_smoke.sh
#
# Prerequisites (this script does not do either of these for you):
#   make local-up        # Postgres + MinIO, on the sceneops-network
#   make lerobot-image    # builds sceneops-platform/lerobot-integration:local
#
# Env overrides (defaults match scripts/e2e/e2e_lerobot_resolve.py /
# makefiles/e2e.mk's E2E_BOOTSTRAP_ENV):
#   SCENEOPS_DATABASE_URL       real Postgres DSN (host form)
#   MINIO_ENDPOINT_URL          host-form MinIO URL for Step 1 (default: http://localhost:9000)
#   MINIO_ROOT_USER / MINIO_ROOT_PASSWORD / MINIO_BUCKET   (Step 1, and used to derive
#                               Step 2's SCENEOPS_INTEGRATION_ARTIFACT__* container env)
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
REPO_ID="test-e2e-lerobot-container-smoke"

IO_DIR="$REPO_ROOT/data/runs/lerobot-container-smoke/_io"
EXPORT_ROOT_HOST="$REPO_ROOT/data/runs/lerobot-container-smoke/$REPO_ID"
EXPORT_ROOT_CONTAINER="/data/runs/lerobot-container-smoke/$REPO_ID"

rm -rf "$IO_DIR" "$EXPORT_ROOT_HOST"
mkdir -p "$IO_DIR"

echo "=== LeRobot container smoke test ==="
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

echo "--- 2. Run the container (isolated LeRobot image, no DB access) ---"
# SCENEOPS_INTEGRATION_ARTIFACT__* -- the container's own environment-driven
# ArtifactStore configuration (entrypoint.py's LeRobotContainerSettings),
# never a hardcoded backend inside the entrypoint. MinIO is simply what
# this smoke test's local stack provides; another caller could point the
# same container at a different backend entirely via the same env vars.
docker run --rm \
  --network "$LEROBOT_CONTAINER_NETWORK" \
  -e SCENEOPS_INTEGRATION_ARTIFACT__BACKEND="minio" \
  -e SCENEOPS_INTEGRATION_ARTIFACT__ROOT_URI="s3://$MINIO_BUCKET/artifacts" \
  -e SCENEOPS_INTEGRATION_ARTIFACT__ENDPOINT_URL="http://minio:9000" \
  -e SCENEOPS_INTEGRATION_ARTIFACT__ACCESS_KEY_ID="$MINIO_ROOT_USER" \
  -e SCENEOPS_INTEGRATION_ARTIFACT__SECRET_ACCESS_KEY="$MINIO_ROOT_PASSWORD" \
  -v "$REPO_ROOT/data:/data" \
  "$LEROBOT_CONTAINER_IMAGE" \
  --request-file "/data/runs/lerobot-container-smoke/_io/request.json" \
  --output-file "/data/runs/lerobot-container-smoke/_io/result.json"
echo ""

echo "--- 3. Verify IntegrationResult ---"
RESULT_JSON="$(cat "$IO_DIR/result.json")"
python3 - "$RESULT_JSON" "$DATASET_ID" "$DATASET_VERSION" "$EXPORT_ROOT_CONTAINER" <<'PYEOF'
import json
import sys

result = json.loads(sys.argv[1])
expected_dataset_id, expected_dataset_version, expected_export_root = sys.argv[2:5]

assert result["operation"] == "export", result
assert result["canonicalRef"]["datasetId"] == expected_dataset_id, result
assert result["canonicalRef"]["datasetVersion"] == expected_dataset_version, result
assert result["externalRef"]["format"] == "lerobot", result
assert result["externalRef"]["uri"] == expected_export_root, result
assert result["producedArtifacts"] == {}, result
assert result["resultMetadata"]["exportedEpisodeCount"] == 3, result
assert result["resultMetadata"]["exportedStepCount"] == 22, result
print("  OK")
PYEOF

echo ""
echo "=== PASSED ==="
echo "  export_root=$EXPORT_ROOT_HOST"
