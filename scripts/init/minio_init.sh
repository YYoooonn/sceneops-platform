#!/bin/sh
set -e

MINIO_ALIAS="minio"
MINIO_URL="${MINIO_URL:-http://minio:9000}"
MINIO_USER="${MINIO_ROOT_USER:-minioadmin}"
MINIO_PASSWORD="${MINIO_ROOT_PASSWORD:-minioadmin}"
BUCKET="${MINIO_BUCKET:-sceneops}"
DATA_DIR="${DATA_DIR:-/data}"

mc alias set "$MINIO_ALIAS" "$MINIO_URL" "$MINIO_USER" "$MINIO_PASSWORD"

mc mb "$MINIO_ALIAS/$BUCKET" --ignore-existing
echo "bucket $BUCKET ready"

if [ -d "$DATA_DIR" ]; then
    echo "migrating $DATA_DIR/raw → s3://$BUCKET/raw ..."
    mc mirror \
        --exclude "*.DS_Store" \
        --overwrite \
        "$DATA_DIR/raw" \
        "$MINIO_ALIAS/$BUCKET/raw"
    echo "migration done"
else
    echo "no local data to migrate"
fi
