#!/usr/bin/env bash
set -euo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.local.yml}"
API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
API_PREFIX="${API_PREFIX:-}"

MODEL_ID="${MODEL_ID:-dummy-detector}"
MODEL_VERSION="${MODEL_VERSION:-v0}"
MODEL_BACKEND="${MODEL_BACKEND:-onnx_runtime}"
MODEL_URI="${MODEL_URI:-/data/models/dummy-detector/versions/v0/model.onnx}"
MODEL_NAME="${MODEL_NAME:-Dummy ONNX Detector}"
MODEL_TASK_TYPE="${MODEL_TASK_TYPE:-detection}"

DATASET_ID="${DATASET_ID:-nuscenes}"
DATASET_VERSION="${DATASET_VERSION:-v1.0-mini}"

PIPELINE_TYPE="${PIPELINE_TYPE:-detection_validation}"
MAX_SAMPLES="${MAX_SAMPLES:-3}"
MATCH_DISTANCE_M="${MATCH_DISTANCE_M:-2.0}"

PIPELINE_CREATE_PATH="${PIPELINE_CREATE_PATH:-/pipelines/runs}"
PIPELINE_GET_PATH_TEMPLATE="${PIPELINE_GET_PATH_TEMPLATE:-/pipelines/runs/__PIPELINE_RUN_ID__}"
PIPELINE_EXECUTE_PATH_TEMPLATE="${PIPELINE_EXECUTE_PATH_TEMPLATE:-/pipelines/runs/__PIPELINE_RUN_ID__/execute}"
INFERENCE_RUNS_PATH="${INFERENCE_RUNS_PATH:-/runs/inference}"
EVALUATION_RUNS_PATH="${EVALUATION_RUNS_PATH:-/runs/evaluations}"

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
echo "== 1. Create dummy ONNX model =="
docker compose -f "${COMPOSE_FILE}" --profile debug run --rm \
  --entrypoint python \
  worker-cli \
  /workspace/scripts/fixtures/create_dummy_onnx_model.py \
  --output "${MODEL_URI}"

echo ""
echo "== 2. Register ONNX model fixture =="
API_BASE_URL="${API_BASE_URL}" \
API_PREFIX="${API_PREFIX}" \
MODEL_ID="${MODEL_ID}" \
MODEL_VERSION="${MODEL_VERSION}" \
MODEL_BACKEND="${MODEL_BACKEND}" \
MODEL_URI="${MODEL_URI}" \
MODEL_NAME="${MODEL_NAME}" \
MODEL_TASK_TYPE="${MODEL_TASK_TYPE}" \
scripts/fixtures/register_dummy_model.sh

echo ""
echo "== 3. Create ONNX pipeline run =="
PIPELINE_CREATE_RESPONSE="$(
  request_json POST "${PIPELINE_CREATE_PATH}" "{
    \"type\": \"${PIPELINE_TYPE}\",
    \"datasetId\": \"${DATASET_ID}\",
    \"datasetVersion\": \"${DATASET_VERSION}\",
    \"modelId\": \"${MODEL_ID}\",
    \"modelVersion\": \"${MODEL_VERSION}\",
    \"params\": {
      \"predict\": {
        \"inferenceBackend\": \"${MODEL_BACKEND}\",
        \"maxSamples\": ${MAX_SAMPLES}
      },
      \"evaluate\": {
        \"matchDistanceM\": ${MATCH_DISTANCE_M},
        \"evaluatorId\": \"center-distance\"
      }
    }
  }"
)"

echo "${PIPELINE_CREATE_RESPONSE}" | pretty_json
PIPELINE_RUN_ID="$(echo "${PIPELINE_CREATE_RESPONSE}" | extract_pipeline_run_id)"
echo "Created pipeline run: ${PIPELINE_RUN_ID}"

echo ""
echo "== 4. Dispatch pipeline run through Celery =="
EXECUTE_PATH="$(replace_pipeline_run_id "${PIPELINE_EXECUTE_PATH_TEMPLATE}" "${PIPELINE_RUN_ID}")"
PIPELINE_EXECUTE_RESPONSE="$(
  request_json POST "${EXECUTE_PATH}"
)"
echo "${PIPELINE_EXECUTE_RESPONSE}" | pretty_json

echo ""
echo "== 5. Poll pipeline status =="
GET_PATH="$(replace_pipeline_run_id "${PIPELINE_GET_PATH_TEMPLATE}" "${PIPELINE_RUN_ID}")"

for attempt in $(seq 1 "${POLL_MAX_ATTEMPTS}"); do
  STATUS_RESPONSE="$(curl -sS "$(url "${GET_PATH}")")"
  STATUS="$(echo "${STATUS_RESPONSE}" | extract_status)"

  echo "attempt=${attempt}/${POLL_MAX_ATTEMPTS} status=${STATUS}"

  if [ "${STATUS}" = "succeeded" ]; then
    echo ""
    echo "Pipeline succeeded."
    echo "${STATUS_RESPONSE}" | pretty_json
    break
  fi

  if [ "${STATUS}" = "failed" ] || [ "${STATUS}" = "canceled" ]; then
    echo ""
    echo "Pipeline finished with status=${STATUS}"
    echo "${STATUS_RESPONSE}" | pretty_json
    echo ""
    echo "Recent worker logs:"
    docker compose -f "${COMPOSE_FILE}" logs --tail=120 worker-celery
    exit 1
  fi

  if [ "${attempt}" = "${POLL_MAX_ATTEMPTS}" ]; then
    echo ""
    echo "Polling timed out."
    echo "${STATUS_RESPONSE}" | pretty_json
    echo ""
    echo "Recent worker logs:"
    docker compose -f "${COMPOSE_FILE}" logs --tail=120 worker-celery
    exit 1
  fi

  sleep "${POLL_INTERVAL_SECONDS}"
done

echo ""
echo "== 6. List inference runs =="
curl -sS "$(url "${INFERENCE_RUNS_PATH}")" | pretty_json || true

echo ""
echo "== 7. List evaluation runs =="
curl -sS "$(url "${EVALUATION_RUNS_PATH}")" | pretty_json || true

echo ""
echo "== 8. Recent worker logs =="
docker compose -f "${COMPOSE_FILE}" logs --tail=80 worker-celery

echo ""
echo "E2E ONNX + Celery pipeline test completed."
