#!/usr/bin/env bash
set -euo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.local.yml}"
API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
API_PREFIX="${API_PREFIX:-}"

DATASET_ID="${DATASET_ID:-nuscenes}"
DATASET_VERSION="${DATASET_VERSION:-v1.0-mini}"
DATASET_TYPE="${DATASET_TYPE:-nuscenes}"

PIPELINE_TYPE="${PIPELINE_TYPE:-dataset_ingestion}"
MAX_SCENES="${MAX_SCENES:-2}"
INGEST_MODE="${INGEST_MODE:-upsert}"

VALIDATE_SAMPLES="${VALIDATE_SAMPLES:-true}"
REQUIRE_TARGET_CHANNELS_JSON="${REQUIRE_TARGET_CHANNELS_JSON:-[\"CAM_FRONT\", \"LIDAR_TOP\"]}"

PIPELINE_CREATE_PATH="${PIPELINE_CREATE_PATH:-/pipelines/runs}"
PIPELINE_GET_PATH_TEMPLATE="${PIPELINE_GET_PATH_TEMPLATE:-/pipelines/runs/__PIPELINE_RUN_ID__}"
PIPELINE_EXECUTE_PATH_TEMPLATE="${PIPELINE_EXECUTE_PATH_TEMPLATE:-/pipelines/runs/__PIPELINE_RUN_ID__/execute}"
DATASET_VERSION_GET_PATH="${DATASET_VERSION_GET_PATH:-/datasets/${DATASET_ID}/versions/${DATASET_VERSION}}"

POLL_INTERVAL_SECONDS="${POLL_INTERVAL_SECONDS:-3}"
POLL_MAX_ATTEMPTS="${POLL_MAX_ATTEMPTS:-60}"

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

replace_pipeline_run_id() {
  local template="$1"
  local pipeline_run_id="$2"

  echo "${template//__PIPELINE_RUN_ID__/${pipeline_run_id}}"
}

extract_pipeline_run_id() {
  python -c '
import json, sys
data = json.load(sys.stdin)

candidates = [
    data.get("pipelineRunId"),
    data.get("pipeline_run_id"),
    data.get("id"),
]

if isinstance(data.get("pipelineRun"), dict):
    candidates.extend([
        data["pipelineRun"].get("pipelineRunId"),
        data["pipelineRun"].get("pipeline_run_id"),
        data["pipelineRun"].get("id"),
    ])

if isinstance(data.get("pipeline_run"), dict):
    candidates.extend([
        data["pipeline_run"].get("pipelineRunId"),
        data["pipeline_run"].get("pipeline_run_id"),
        data["pipeline_run"].get("id"),
    ])

for value in candidates:
    if value:
        print(value)
        sys.exit(0)

raise SystemExit(f"Could not extract pipeline run id from response: {data}")
'
}

extract_status() {
  python -c '
import json, sys
data = json.load(sys.stdin)

candidates = [data.get("status")]

if isinstance(data.get("pipelineRun"), dict):
    candidates.append(data["pipelineRun"].get("status"))

if isinstance(data.get("pipeline_run"), dict):
    candidates.append(data["pipeline_run"].get("status"))

for value in candidates:
    if value:
        print(value)
        sys.exit(0)

print("unknown")
'
}

echo ""
echo "== 0. Check compose services =="
docker compose -f "${COMPOSE_FILE}" ps

echo ""
echo "== 1. Create dataset_ingestion pipeline run =="

PIPELINE_PAYLOAD="{
  \"type\": \"${PIPELINE_TYPE}\",
  \"datasetId\": \"${DATASET_ID}\",
  \"datasetVersion\": \"${DATASET_VERSION}\",
  \"params\": {
    \"ingest\": {
      \"datasetType\": \"${DATASET_TYPE}\",
      \"maxScenes\": ${MAX_SCENES},
      \"mode\": \"${INGEST_MODE}\"
    },
    \"validate\": {
      \"validateSamples\": ${VALIDATE_SAMPLES},
      \"requireTargetChannels\": ${REQUIRE_TARGET_CHANNELS_JSON}
    }
  }
}"

echo "${PIPELINE_PAYLOAD}" | pretty_json

PIPELINE_CREATE_RESPONSE="$(
  request_json POST "${PIPELINE_CREATE_PATH}" "${PIPELINE_PAYLOAD}"
)"

echo "${PIPELINE_CREATE_RESPONSE}" | pretty_json

PIPELINE_RUN_ID="$(echo "${PIPELINE_CREATE_RESPONSE}" | extract_pipeline_run_id)"
echo "Created pipeline run: ${PIPELINE_RUN_ID}"

echo ""
echo "== 2. Dispatch dataset_ingestion pipeline run through Celery =="

EXECUTE_PATH="$(replace_pipeline_run_id "${PIPELINE_EXECUTE_PATH_TEMPLATE}" "${PIPELINE_RUN_ID}")"

echo "Execute path: ${EXECUTE_PATH}"
echo "Execute URL: $(url "${EXECUTE_PATH}")"

PIPELINE_EXECUTE_RESPONSE="$(
  request_json POST "${EXECUTE_PATH}"
)"

echo "${PIPELINE_EXECUTE_RESPONSE}" | pretty_json

echo ""
echo "== 3. Poll pipeline status =="

GET_PATH="$(replace_pipeline_run_id "${PIPELINE_GET_PATH_TEMPLATE}" "${PIPELINE_RUN_ID}")"

echo "Get path: ${GET_PATH}"
echo "Get URL: $(url "${GET_PATH}")"

for attempt in $(seq 1 "${POLL_MAX_ATTEMPTS}"); do
  STATUS_RESPONSE="$(curl -sS "$(url "${GET_PATH}")")"
  STATUS="$(echo "${STATUS_RESPONSE}" | extract_status)"

  echo "attempt=${attempt}/${POLL_MAX_ATTEMPTS} status=${STATUS}"

  if [ "${STATUS}" = "succeeded" ]; then
    echo ""
    echo "Dataset ingestion pipeline succeeded."
    echo "${STATUS_RESPONSE}" | pretty_json
    break
  fi

  if [ "${STATUS}" = "failed" ] || [ "${STATUS}" = "canceled" ]; then
    echo ""
    echo "Dataset ingestion pipeline finished with status=${STATUS}"
    echo "${STATUS_RESPONSE}" | pretty_json

    echo ""
    echo "Recent worker logs:"
    docker compose -f "${COMPOSE_FILE}" logs --tail=180 worker-celery
    exit 1
  fi

  if [ "${attempt}" = "${POLL_MAX_ATTEMPTS}" ]; then
    echo ""
    echo "Polling timed out."
    echo "${STATUS_RESPONSE}" | pretty_json

    echo ""
    echo "Recent worker logs:"
    docker compose -f "${COMPOSE_FILE}" logs --tail=180 worker-celery
    exit 1
  fi

  sleep "${POLL_INTERVAL_SECONDS}"
done

echo ""
echo "== 4. Check dataset version registry =="

DATASET_VERSION_RESPONSE="$(
  request_json GET "${DATASET_VERSION_GET_PATH}"
)"

echo "${DATASET_VERSION_RESPONSE}" | pretty_json

echo ""
echo "== 5. Recent worker logs =="
docker compose -f "${COMPOSE_FILE}" logs --tail=120 worker-celery

echo ""
echo "E2E dataset_ingestion pipeline + Celery test completed."
