#!/usr/bin/env bash
# lib.sh — shared helpers for SceneOps E2E scripts

api_url() {
  local api_base_url="$1"
  local path="$2"
  local prefix="${API_PREFIX:-/api/v1}"
  echo "${api_base_url}${prefix}${path}"
}

# ── Assertions ────────────────────────────────────────────────────────────────

require_json_field() {
  local json="$1"
  local jq_expr="$2"
  local message="$3"

  local value
  value="$(echo "$json" | jq -r "$jq_expr")"

  if [ "$value" = "null" ] || [ -z "$value" ]; then
    echo "❌ Missing field: $message ($jq_expr)" >&2
    echo "$json" | jq . >&2
    exit 1
  fi

  echo "$value"
}

assert_json_equals() {
  local json="$1"
  local jq_expr="$2"
  local expected="$3"
  local message="$4"

  local actual
  actual="$(echo "$json" | jq -r "$jq_expr")"

  if [ "$actual" != "$expected" ]; then
    echo "❌ Assertion failed: $message" >&2
    echo "  expected=$expected actual=$actual expr=$jq_expr" >&2
    echo "$json" | jq . >&2
    exit 1
  fi
}

assert_json_not_empty() {
  local json="$1"
  local jq_expr="$2"
  local message="$3"

  local value
  value="$(echo "$json" | jq -r "$jq_expr // empty")"

  if [ -z "$value" ]; then
    echo "❌ Expected non-empty value: $message ($jq_expr)" >&2
    echo "$json" | jq . >&2
    exit 1
  fi
}

assert_json_gt() {
  local json="$1"
  local jq_expr="$2"
  local min="$3"
  local message="$4"

  local value
  value="$(echo "$json" | jq -r "$jq_expr // 0")"

  if [ "$value" -le "$min" ] 2>/dev/null; then
    echo "❌ Assertion failed: $message — expected >$min, got $value" >&2
    echo "$json" | jq . >&2
    exit 1
  fi
}

# ── Pipeline API ──────────────────────────────────────────────────────────────

create_pipeline_run() {
  local api_base_url="$1"
  local payload="$2"
  curl -sS -X POST "$(api_url "$api_base_url" "/pipelines/runs")" \
    -H "Content-Type: application/json" \
    -d "$payload"
}

dispatch_pipeline_run() {
  local api_base_url="$1"
  local pipeline_run_id="$2"
  curl -sS -X POST "$(api_url "$api_base_url" "/pipelines/runs/$pipeline_run_id/execute")"
}

fetch_pipeline_run() {
  local api_base_url="$1"
  local pipeline_run_id="$2"
  curl -sS "$(api_url "$api_base_url" "/pipelines/runs/$pipeline_run_id")"
}

fetch_pipeline_tasks() {
  local api_base_url="$1"
  local pipeline_run_id="$2"
  curl -sS "$(api_url "$api_base_url" "/pipelines/runs/$pipeline_run_id/tasks")"
}

extract_pipeline_run_id() {
  local json="$1"
  require_json_field "$json" '.pipelineRun.pipelineRunId' 'pipelineRunId'
}

poll_pipeline_terminal() {
  local api_base_url="$1"
  local pipeline_run_id="$2"
  local max_attempts="${3:-60}"
  local sleep_seconds="${4:-5}"

  local pipeline_json status

  for i in $(seq 1 "$max_attempts"); do
    pipeline_json="$(fetch_pipeline_run "$api_base_url" "$pipeline_run_id")"
    status="$(echo "$pipeline_json" | jq -r '.pipelineRun.status // empty')"

    echo "  [$i/$max_attempts] status=$status" >&2

    case "$status" in
      succeeded|failed|cancelled|blocked)
        echo "$pipeline_json"
        return 0
        ;;
    esac

    sleep "$sleep_seconds"
  done

  echo "❌ Pipeline did not reach terminal state after $max_attempts attempts: $pipeline_run_id" >&2
  exit 1
}

assert_pipeline_succeeded() {
  local pipeline_json="$1"
  local message="${2:-pipeline should succeed}"
  assert_json_equals "$pipeline_json" '.pipelineRun.status' 'succeeded' "$message"
}

# ── Dataset / Scene API ───────────────────────────────────────────────────────

upsert_dataset() {
  local api_base_url="$1"
  local dataset_id="$2"
  local name="$3"

  local existing
  existing="$(curl -sS "$(api_url "$api_base_url" "/datasets/$dataset_id")")"

  if echo "$existing" | jq -e '.dataset' >/dev/null 2>&1; then
    echo "$existing"
    return 0
  fi

  curl -sS -X POST "$(api_url "$api_base_url" "/datasets")" \
    -H "Content-Type: application/json" \
    -d "{\"dataset_id\": \"$dataset_id\", \"name\": \"$name\", \"metadata\": {}}"
}


upsert_dataset_version() {
  local api_base_url="$1"
  local dataset_id="$2"
  local version="$3"
  local raw_source_root_uri="$4"

  local existing
  existing="$(curl -sS "$(api_url "$api_base_url" "/datasets/$dataset_id/versions/$version")")"

  if echo "$existing" | jq -e '.version' >/dev/null 2>&1; then
    # Version exists — patch raw_source_root_uri in case it changed.
    curl -sS -X PATCH "$(api_url "$api_base_url" "/datasets/$dataset_id/versions/$version")" \
      -H "Content-Type: application/json" \
      -d "{\"raw_source_root_uri\": \"$raw_source_root_uri\", \"required_channels\": ["CAM_FRONT", "LIDAR_TOP"]}"
    return 0
  fi

  curl -sS -X POST "$(api_url "$api_base_url" "/datasets/$dataset_id/versions")" \
    -H "Content-Type: application/json" \
    -d "{\"version\": \"$version\", \"raw_source_root_uri\": \"$raw_source_root_uri\", \"metadata\": {}}"
}


upsert_model() {
  local api_base_url="$1"
  local model_id="$2"
  local model_version="$3"
  local name="$4"

  local existing
  existing="$(curl -sS "$(api_url "$api_base_url" "/models/$model_id/versions/$model_version")")"

  if echo "$existing" | jq -e '.version' >/dev/null 2>&1; then
    echo "$existing"
    return 0
  fi

  curl -sS -X POST "$(api_url "$api_base_url" "/models")" \
    -H "Content-Type: application/json" \
    -d "{\"modelId\": \"$model_id\", \"name\": \"$name\", \"metadata\": {}}"

  curl -sS -X POST "$(api_url "$api_base_url" "/models/$model_id/versions")" \
    -H "Content-Type: application/json" \
    -d "{\"version\": \"$model_version\", \"backend\": \"mock\", \"metadata\": {}}"
}

upsert_model_with_backend() {
  local api_base_url="$1"
  local model_id="$2"
  local model_version="$3"
  local endpoint_url="$4"
  local name="$5"
  local backend="$6"

  local existing
  existing="$(curl -sS "$(api_url "$api_base_url" "/models/$model_id/versions/$model_version")")"
  if echo "$existing" | jq -e '.version.endpointUrl' >/dev/null 2>&1; then
    echo "$existing"
    return 0
  fi

  local model_check
  model_check="$(curl -sS "$(api_url "$api_base_url" "/models/$model_id")")"
  if ! echo "$model_check" | jq -e '.model' >/dev/null 2>&1; then
    curl -sS -X POST "$(api_url "$api_base_url" "/models")" \
      -H "Content-Type: application/json" \
      -d "{\"modelId\": \"$model_id\", \"name\": \"$name\", \"metadata\": {}}" \
      > /dev/null
  fi

  curl -sS -X POST "$(api_url "$api_base_url" "/models/$model_id/versions")" \
    -H "Content-Type: application/json" \
    -d "{\"version\": \"$model_version\", \"backend\": \"$backend\", \"endpoint_url\": \"$endpoint_url\", \"metadata\": {}}"
}

# ── Inference Server ──────────────────────────────────────────────────────────

poll_inference_ready() {
  local inference_url="$1"
  local max_attempts="${2:-60}"
  local sleep_seconds="${3:-2}"

  local resp status model_loaded warmup_completed warmup_succeeded

  for i in $(seq 1 "$max_attempts"); do
    resp="$(curl -sf "${inference_url}/readyz" 2>/dev/null || echo '{}')"
    status="$(echo "$resp" | jq -r '.status // empty' 2>/dev/null || true)"
    model_loaded="$(echo "$resp" | jq -r '.model_loaded // false' 2>/dev/null || true)"
    warmup_completed="$(echo "$resp" | jq -r '.warmup_completed // false' 2>/dev/null || true)"
    warmup_succeeded="$(echo "$resp" | jq -r '.warmup_succeeded // null' 2>/dev/null || true)"

    printf "  [%d/%d] status=%s model_loaded=%s warmup_completed=%s warmup_succeeded=%s\n" \
      "$i" "$max_attempts" "$status" "$model_loaded" "$warmup_completed" "$warmup_succeeded" >&2

    if [ "$status" = "ready" ] && [ "$model_loaded" = "true" ]; then
      echo "$resp"
      return 0
    fi

    sleep "$sleep_seconds"
  done

  echo "❌ Inference server did not become ready after $((max_attempts * sleep_seconds))s" >&2
  exit 1
}
