#!/usr/bin/env bash
set -euo pipefail

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
API_PREFIX="${API_PREFIX:-}"

MODEL_ID="${MODEL_ID:-dummy-detector}"
MODEL_VERSION="${MODEL_VERSION:-v0}"
MODEL_BACKEND="${MODEL_BACKEND:-onnx_runtime}"
MODEL_URI="${MODEL_URI:-/data/models/dummy-detector/versions/v0/model.onnx}"
MODEL_NAME="${MODEL_NAME:-Dummy Detector}"
MODEL_TASK_TYPE="${MODEL_TASK_TYPE:-detection}"

MODEL_CREATE_PATH="${MODEL_CREATE_PATH:-/models}"
MODEL_VERSION_CREATE_PATH="${MODEL_VERSION_CREATE_PATH:-/models/${MODEL_ID}/versions}"

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

echo "== Register model =="
MODEL_RESPONSE="$(
  request_json POST "${MODEL_CREATE_PATH}" "{
    \"modelId\": \"${MODEL_ID}\",
    \"taskType\": \"${MODEL_TASK_TYPE}\",
    \"name\": \"${MODEL_NAME}\",
    \"description\": \"Dummy detector model for SceneOps E2E tests\",
    \"metadata\": {}
  }"
)"
echo "${MODEL_RESPONSE}" | pretty_json

echo ""
echo "== Register model version =="

if [ "${MODEL_BACKEND}" = "mock" ]; then
  MODEL_VERSION_PAYLOAD="{
    \"version\": \"${MODEL_VERSION}\",
    \"backend\": \"${MODEL_BACKEND}\",
    \"status\": \"ready\",
    \"description\": \"Dummy ${MODEL_BACKEND} model version for SceneOps E2E tests\",
    \"metadata\": {}
  }"
else
  MODEL_VERSION_PAYLOAD="{
    \"version\": \"${MODEL_VERSION}\",
    \"backend\": \"${MODEL_BACKEND}\",
    \"modelUri\": \"${MODEL_URI}\",
    \"status\": \"ready\",
    \"description\": \"Dummy ${MODEL_BACKEND} model version for SceneOps E2E tests\",
    \"metadata\": {}
  }"
fi

MODEL_VERSION_RESPONSE="$(
  request_json POST "${MODEL_VERSION_CREATE_PATH}" "${MODEL_VERSION_PAYLOAD}"
)"
echo "${MODEL_VERSION_RESPONSE}" | pretty_json
