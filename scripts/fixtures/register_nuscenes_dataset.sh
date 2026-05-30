#!/usr/bin/env bash
set -euo pipefail

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
API_PREFIX="${API_PREFIX:-}"

DATASET_ID="${DATASET_ID:-nuscenes}"
DATASET_TYPE="${DATASET_TYPE:-nuscenes}"
DATASET_NAME="${DATASET_NAME:-nuScenes}"
DATASET_DESCRIPTION="${DATASET_DESCRIPTION:-nuScenes autonomous driving dataset}"

DATASET_VERSION="${DATASET_VERSION:-v1.0-mini}"
SOURCE_URI="${SOURCE_URI:-/data/raw/nuscenes}"

DATASET_CREATE_PATH="${DATASET_CREATE_PATH:-/datasets}"
DATASET_VERSION_CREATE_PATH="${DATASET_VERSION_CREATE_PATH:-/datasets/${DATASET_ID}/versions}"

url() {
  local path="$1"
  echo "${API_BASE_URL}${API_PREFIX}${path}"
}

pretty_json() {
  python -m json.tool
}

request_json() {
  local method="$1"
  local path="$2"
  local payload="${3:-}"

  local tmp_body
  tmp_body="$(mktemp)"

  local status_code

  if [ -n "${payload}" ]; then
    status_code="$(
      curl -sS -o "${tmp_body}" -w "%{http_code}" \
        -X "${method}" "$(url "${path}")" \
        -H "Content-Type: application/json" \
        -d "${payload}"
    )"
  else
    status_code="$(
      curl -sS -o "${tmp_body}" -w "%{http_code}" \
        -X "${method}" "$(url "${path}")" \
        -H "Content-Type: application/json"
    )"
  fi

  if [ "${status_code}" -lt 200 ] || [ "${status_code}" -ge 300 ]; then
    echo "HTTP ${status_code} ${method} $(url "${path}")" >&2
    cat "${tmp_body}" >&2
    echo "" >&2
    rm -f "${tmp_body}"
    exit 1
  fi

  cat "${tmp_body}"
  rm -f "${tmp_body}"
}

echo "== Register dataset =="
DATASET_RESPONSE="$(
  request_json PUT "${DATASET_CREATE_PATH}/${DATASET_ID}" "{
    \"id\": \"${DATASET_ID}\",
    \"datasetType\": \"${DATASET_TYPE}\",
    \"name\": \"${DATASET_NAME}\",
    \"description\": \"${DATASET_DESCRIPTION}\",
    \"metadata\": {
      \"source\": \"fixture\"
    }
  }"
)"
echo "${DATASET_RESPONSE}" | pretty_json

echo ""
echo "== Register dataset version =="
DATASET_VERSION_RESPONSE="$(
  request_json PUT "${DATASET_VERSION_CREATE_PATH}/${DATASET_VERSION}" "{
    \"version\": \"${DATASET_VERSION}\",
    \"datasetType\": \"${DATASET_TYPE}\",
    \"sourceUri\": \"${SOURCE_URI}\",
    \"metadata\": {
      \"split\": \"mini\",
      \"source\": \"fixture\"
    }
  }"
)"
echo "${DATASET_VERSION_RESPONSE}" | pretty_json
