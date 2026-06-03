#!/bin/bash
set -euo pipefail

ENDPOINT="${MINIO_ENDPOINT:-http://localhost:9000}"

echo "Checking MinIO at $ENDPOINT ..."
uv run python scripts/checks/check_minio.py --endpoint "$ENDPOINT"
