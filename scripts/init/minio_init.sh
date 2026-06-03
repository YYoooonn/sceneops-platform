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

if [ -d "$DATA_DIR" ] && [ -n "$(find "$DATA_DIR" -not -name '.DS_Store' -type f 2>/dev/null | head -1)" ]; then
    echo "migrating $DATA_DIR → s3://$BUCKET ..."
    mc mirror \
        --exclude "*.DS_Store" \
        --overwrite \
        "$DATA_DIR" \
        "$MINIO_ALIAS/$BUCKET"
    echo "migration done"
else
    echo "no local data to migrate"
fi
