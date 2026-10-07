#!/bin/sh
# Idempotent object-storage bootstrap: ensures the bucket exists. It copies no
# data into it; every object under the bucket is written by the platform
# through the ArtifactStore.
set -e

MINIO_ALIAS="minio"
MINIO_URL="${MINIO_URL:-http://minio:9000}"
MINIO_USER="${MINIO_ROOT_USER:-minioadmin}"
MINIO_PASSWORD="${MINIO_ROOT_PASSWORD:-minioadmin}"
BUCKET="${MINIO_BUCKET:-sceneops}"

mc alias set "$MINIO_ALIAS" "$MINIO_URL" "$MINIO_USER" "$MINIO_PASSWORD"

mc mb "$MINIO_ALIAS/$BUCKET" --ignore-existing
echo "bucket $BUCKET ready"
