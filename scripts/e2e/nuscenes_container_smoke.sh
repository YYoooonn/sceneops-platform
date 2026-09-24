#!/usr/bin/env bash
# nuscenes_container_smoke.sh (SceneOps V2 Request 4.5)
#
# Minimal container-level smoke test for the nuScenes integration container
# (tools/nuscenes-integration/Dockerfile): build image -> start container ->
# parse a real IntegrationRequest -> read a real local nuScenes dataroot via
# real nuscenes-devkit -> write raw-log artifacts to a real MinIO
# ArtifactStore -> return a valid IntegrationResult. Mirrors
# scripts/e2e/lerobot_container_smoke.sh's own structure (Request 4.2 §7).
#
# Two processes:
#
#   Step 1  scripts/e2e/nuscenes_container_build_request.py, run from the
#           MAIN workspace venv. Unlike the LeRobot smoke test, this needs
#           no DB session at all -- a nuScenes INGEST request names no
#           pre-existing canonical artifact (canonical_ref is bare
#           identity, canonical_inputs is legitimately empty). Prints one
#           IntegrationRequest as JSON.
#
#   Step 2  `docker run` the built nuscenes-integration image. Given only
#           that IntegrationRequest JSON (mounted as a file), the real
#           nuScenes dataroot (mounted read-only), --raw-log-id/
#           --manifest-uri/--frame-index-uri (the destination URIs, which
#           this script -- not the container -- computes, exactly like
#           ObservationArtifactStore's own layout policy: Request 4.5's own
#           entrypoint.py docstring), and SCENEOPS_INTEGRATION_ARTIFACT__*
#           env vars, it independently runs nuscenes-devkit and writes the
#           two raw-log artifacts -- never touching Postgres or
#           sceneops-db (the image has neither installed).
#
# Usage:
#   bash scripts/e2e/nuscenes_container_smoke.sh
#
# Prerequisites (this script does not do either of these for you):
#   make local-up          # MinIO, on the sceneops-network
#   make nuscenes-image    # builds sceneops-platform/nuscenes-integration:local
#
# Env overrides:
#   MINIO_ENDPOINT_URL           (default: http://localhost:9000, Step 1 -- unused,
#                                 kept for symmetry with the MinIO env vars below)
#   MINIO_ROOT_USER / MINIO_ROOT_PASSWORD / MINIO_BUCKET
#   NUSCENES_CONTAINER_IMAGE     (default: sceneops-platform/nuscenes-integration:local)
#   NUSCENES_CONTAINER_NETWORK   (default: sceneops-network -- must already exist, from `make local-up`)
#   SOURCE_ROOT_URI              real nuScenes dataroot on the HOST (default: $REPO_ROOT/data/raw/nuscenes)
#   SOURCE_FORMAT_VERSION        (default: v1.0-mini)
#   MAX_SEQUENCES                (default: 2 -- kept small for a fast smoke test)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

MINIO_ROOT_USER="${MINIO_ROOT_USER:-minioadmin}"
MINIO_ROOT_PASSWORD="${MINIO_ROOT_PASSWORD:-minioadmin}"
MINIO_BUCKET="${MINIO_BUCKET:-sceneops}"
NUSCENES_CONTAINER_IMAGE="${NUSCENES_CONTAINER_IMAGE:-sceneops-platform/nuscenes-integration:local}"
NUSCENES_CONTAINER_NETWORK="${NUSCENES_CONTAINER_NETWORK:-sceneops-network}"

SOURCE_ROOT_URI_HOST="${SOURCE_ROOT_URI:-$REPO_ROOT/data/raw/nuscenes}"
SOURCE_ROOT_URI_CONTAINER="/data/raw/nuscenes"
SOURCE_FORMAT_VERSION="${SOURCE_FORMAT_VERSION:-v1.0-mini}"
MAX_SEQUENCES="${MAX_SEQUENCES:-2}"

DATASET_ID="test-e2e-nuscenes-container-smoke"
DATASET_VERSION="test-v1"
RAW_LOG_ID="log-container-smoke"

if [ ! -d "$SOURCE_ROOT_URI_HOST/v1.0-mini" ]; then
  echo "❌ real nuScenes v1.0-mini fixture not found at $SOURCE_ROOT_URI_HOST" >&2
  exit 1
fi

IO_DIR="$REPO_ROOT/data/runs/nuscenes-container-smoke/_io"
rm -rf "$IO_DIR"
mkdir -p "$IO_DIR"

MANIFEST_URI="s3://$MINIO_BUCKET/artifacts/datasets/$DATASET_ID/versions/$DATASET_VERSION/raw/$RAW_LOG_ID/raw_log.json"
FRAME_INDEX_URI="s3://$MINIO_BUCKET/artifacts/datasets/$DATASET_ID/versions/$DATASET_VERSION/raw/$RAW_LOG_ID/frames.json"

echo "=== nuScenes container smoke test ==="
echo "  DATASET_ID=$DATASET_ID  DATASET_VERSION=$DATASET_VERSION  RAW_LOG_ID=$RAW_LOG_ID"
echo "  SOURCE_FORMAT_VERSION=$SOURCE_FORMAT_VERSION  MAX_SEQUENCES=$MAX_SEQUENCES"
echo "  image=$NUSCENES_CONTAINER_IMAGE  network=$NUSCENES_CONTAINER_NETWORK"
echo ""

echo "--- 1. Build IntegrationRequest (main workspace venv, no DB needed) ---"
cd "$REPO_ROOT"
uv run python scripts/e2e/nuscenes_container_build_request.py \
  --source-root-uri "$SOURCE_ROOT_URI_CONTAINER" \
  --dataset-id "$DATASET_ID" \
  --dataset-version "$DATASET_VERSION" \
  --source-format-version "$SOURCE_FORMAT_VERSION" \
  --max-source-sequences "$MAX_SEQUENCES" \
  >"$IO_DIR/request.json"
echo "  wrote $IO_DIR/request.json"
echo ""

echo "--- 2. Run the container (isolated nuScenes image, no DB access) ---"
# SCENEOPS_INTEGRATION_ARTIFACT__* -- the container's own environment-driven
# ArtifactStore configuration (entrypoint.py's NuScenesContainerSettings),
# never a hardcoded backend inside the entrypoint. MinIO is simply what
# this smoke test's local stack provides.
docker run --rm \
  --network "$NUSCENES_CONTAINER_NETWORK" \
  -e SCENEOPS_INTEGRATION_ARTIFACT__BACKEND="minio" \
  -e SCENEOPS_INTEGRATION_ARTIFACT__ROOT_URI="s3://$MINIO_BUCKET/artifacts" \
  -e SCENEOPS_INTEGRATION_ARTIFACT__ENDPOINT_URL="http://minio:9000" \
  -e SCENEOPS_INTEGRATION_ARTIFACT__ACCESS_KEY_ID="$MINIO_ROOT_USER" \
  -e SCENEOPS_INTEGRATION_ARTIFACT__SECRET_ACCESS_KEY="$MINIO_ROOT_PASSWORD" \
  -v "$REPO_ROOT/data:/data" \
  "$NUSCENES_CONTAINER_IMAGE" \
  --request-file "/data/runs/nuscenes-container-smoke/_io/request.json" \
  --output-file "/data/runs/nuscenes-container-smoke/_io/result.json" \
  --raw-log-id "$RAW_LOG_ID" \
  --manifest-uri "$MANIFEST_URI" \
  --frame-index-uri "$FRAME_INDEX_URI"
echo ""

echo "--- 3. Verify IntegrationResult ---"
RESULT_JSON="$(cat "$IO_DIR/result.json")"
python3 - "$RESULT_JSON" "$DATASET_ID" "$DATASET_VERSION" "$SOURCE_ROOT_URI_CONTAINER" "$MANIFEST_URI" "$FRAME_INDEX_URI" <<'PYEOF'
import json
import sys

result = json.loads(sys.argv[1])
expected_dataset_id, expected_dataset_version, expected_source_uri, expected_manifest_uri, expected_frame_index_uri = sys.argv[2:7]

assert result["operation"] == "ingest", result
assert result["canonicalRef"]["datasetId"] == expected_dataset_id, result
assert result["canonicalRef"]["datasetVersion"] == expected_dataset_version, result
assert result["externalRef"]["format"] == "nuscenes", result
assert result["externalRef"]["uri"] == expected_source_uri, result

produced = result["producedArtifacts"]
assert set(produced) == {"raw_log_manifest", "raw_log_frame_index"}, result
assert produced["raw_log_manifest"]["kind"] == "raw_log_manifest", result
assert produced["raw_log_manifest"]["uri"] == expected_manifest_uri, result
assert produced["raw_log_manifest"]["checksum"], result
assert produced["raw_log_frame_index"]["kind"] == "raw_log_frame_index", result
assert produced["raw_log_frame_index"]["uri"] == expected_frame_index_uri, result
assert produced["raw_log_frame_index"]["checksum"], result

meta = result["resultMetadata"]
assert meta["sequence_count"] == 2, result
assert meta["frame_count"] == 948, result
assert "CAM_FRONT" in meta["channels"] and "LIDAR_TOP" in meta["channels"], result
print("  OK")
PYEOF
echo ""

echo "--- 4. Verify the written artifacts are real, readable JSON in MinIO ---"
uv run python - "$MINIO_BUCKET" "$DATASET_ID" "$DATASET_VERSION" "$RAW_LOG_ID" <<'PYEOF'
import json
import os
import sys

import boto3

bucket, dataset_id, dataset_version, raw_log_id = sys.argv[1:5]
s3 = boto3.client(
    "s3",
    endpoint_url=os.environ.get("MINIO_ENDPOINT_URL", "http://localhost:9000"),
    aws_access_key_id=os.environ.get("MINIO_ROOT_USER", "minioadmin"),
    aws_secret_access_key=os.environ.get("MINIO_ROOT_PASSWORD", "minioadmin"),
)
key_prefix = f"artifacts/datasets/{dataset_id}/versions/{dataset_version}/raw/{raw_log_id}"
manifest = json.loads(s3.get_object(Bucket=bucket, Key=f"{key_prefix}/raw_log.json")["Body"].read())
frame_index = json.loads(s3.get_object(Bucket=bucket, Key=f"{key_prefix}/frames.json")["Body"].read())

assert manifest["sequenceCount"] == 2, manifest
assert manifest["frameCount"] == 948, manifest
assert manifest["sourceFormat"] == "nuscenes", manifest
assert len(frame_index["frames"]) == manifest["frameCount"], (len(frame_index["frames"]), manifest)
print(f"  OK ({manifest['frameCount']} frames, {manifest['sequenceCount']} sequences)")
PYEOF

echo ""
echo "=== PASSED ==="
echo "  manifest_uri=$MANIFEST_URI"
echo "  frame_index_uri=$FRAME_INDEX_URI"
