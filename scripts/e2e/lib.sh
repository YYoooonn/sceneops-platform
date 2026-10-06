#!/usr/bin/env bash
# lib.sh — shared helpers for the SceneOps E2E journeys, the canonical
# baseline and the infrastructure acceptance tests.
#
# Platform operations go through the FastAPI control plane (curl + jq);
# bulk data moves between one-shot containers (dataset-acquisition,
# recording-publisher, worker-cli, lerobot-integration) and the ArtifactStore.
# Nothing here reads PostgreSQL or MinIO directly.
#
# The sourcing script sets REPO_ROOT (the repository root) before sourcing.

API_PREFIX="${API_PREFIX:-/api/v1}"
ENV_FILE="${ENV_FILE:-.env.local}"
POLL_ATTEMPTS="${POLL_ATTEMPTS:-180}"

# ── Output ───────────────────────────────────────────────────────────────────

fail() {
  echo "❌ $*" >&2
  exit 1
}

# check <label> <command...> — run the command; print a ✅ line or fail.
check() {
  local label="$1"
  shift
  if "$@"; then
    echo "  ✅  $label"
  else
    fail "$label"
  fi
}

# ── Runtime role ─────────────────────────────────────────────────────────────
#
# The local runtime is either the reference environment (exactly the golden
# reference contract's RobotRuns; docs/development/reference-contract.md) or a
# disposable one. A workflow that registers RobotRuns, or captures a stream, of
# its own leaves durable non-contract state: PostgreSQL rows and MinIO objects
# that no production API removes. Such a workflow must not run on the reference
# environment; it runs on a runtime that is dropped afterwards (`make local-reset`
# and, for the reference environment, `make reference-contract-bootstrap` again).
# DISPOSABLE_RUNTIME=1 states that this runtime is one.

# require_disposable_runtime <what-would-be-created>
require_disposable_runtime() {
  [ "${DISPOSABLE_RUNTIME:-0}" = 1 ] && return 0
  echo "❌ $1." >&2
  echo "   This creates RobotRuns that outlive the run and are not part of the reference contract," >&2
  echo "   and nothing in the platform removes them. Run it on a disposable runtime and say so:" >&2
  echo "     make local-reset && make <target> DISPOSABLE_RUNTIME=1" >&2
  echo "   then restore the reference environment with \`make local-reset && make reference-contract-bootstrap\`." >&2
  exit 1
}

# ── Canonical build configurations ───────────────────────────────────────────
#
# The Scene / Episode build configurations of the canonical baseline are the
# files under config/baselines/. Every journey that builds from the baseline
# recording uses them, so "the same configuration" is one definition.

BASELINE_CONFIG_DIR="${BASELINE_CONFIG_DIR:-$REPO_ROOT/config/baselines}"

# scene_build_config
scene_build_config() {
  jq -c . "$BASELINE_CONFIG_DIR/scene_build_config.json"
}

# episode_build_config [segmentation-json]
episode_build_config() {
  if [ -n "${1:-}" ]; then
    jq -c --argjson s "$1" '.segmentation = $s' "$BASELINE_CONFIG_DIR/episode_build_config.json"
  else
    jq -c . "$BASELINE_CONFIG_DIR/episode_build_config.json"
  fi
}

# ── API ──────────────────────────────────────────────────────────────────────

api_url() {
  echo "${1}${API_PREFIX}${2}"
}

# api_get <api-base-url> <path>
api_get() {
  curl -fsS "$(api_url "$1" "$2")"
}

require_json_field() {
  local json="$1" jq_path="$2" label="$3" value
  value="$(echo "$json" | jq -r "$jq_path // empty")"
  if [ -z "$value" ]; then
    echo "❌ Missing $label in response:" >&2
    echo "$json" | jq . >&2 2>/dev/null || echo "$json" >&2
    exit 1
  fi
  echo "$value"
}

require_api() {
  curl -fsS "$1/health" >/dev/null || fail "SceneOps API not reachable at $1 (make local-up)"
}

# ── Pipeline API ─────────────────────────────────────────────────────────────

create_pipeline_run() {
  curl -sS -X POST "$(api_url "$1" "/pipelines/runs")" \
    -H "Content-Type: application/json" -d "$2"
}

dispatch_pipeline_run() {
  curl -sS -X POST "$(api_url "$1" "/pipelines/runs/$2/execute")"
}

fetch_pipeline_run() {
  curl -sS "$(api_url "$1" "/pipelines/runs/$2")"
}

fetch_pipeline_tasks() {
  curl -sS "$(api_url "$1" "/pipelines/runs/$2/tasks")"
}

extract_pipeline_run_id() {
  require_json_field "$1" '.pipelineRun.pipelineRunId' 'pipelineRunId'
}

# poll_pipeline_terminal <api> <run-id> [attempts] [sleep-seconds]
# Prints the terminal run JSON.
poll_pipeline_terminal() {
  local api_base_url="$1" pipeline_run_id="$2"
  local max_attempts="${3:-60}" sleep_seconds="${4:-5}"
  local pipeline_json status i
  for i in $(seq 1 "$max_attempts"); do
    pipeline_json="$(fetch_pipeline_run "$api_base_url" "$pipeline_run_id")"
    status="$(echo "$pipeline_json" | jq -r '.pipelineRun.status // empty')"
    echo "  [$i/$max_attempts] status=$status" >&2
    case "$status" in
      succeeded | failed | cancelled | blocked)
        echo "$pipeline_json"
        return 0
        ;;
    esac
    sleep "$sleep_seconds"
  done
  echo "❌ Pipeline did not reach a terminal state after $max_attempts attempts: $pipeline_run_id" >&2
  exit 1
}

# assert_pipeline_succeeded <run-json> <message> [api] [run-id]
# On failure prints the per-task statuses and errors.
assert_pipeline_succeeded() {
  local pipeline_json="$1" message="${2:-pipeline should succeed}"
  local api_base_url="${3:-}" pipeline_run_id="${4:-}"
  local status
  status="$(echo "$pipeline_json" | jq -r '.pipelineRun.status')"
  [ "$status" = "succeeded" ] && return 0

  echo "❌ Assertion failed: $message" >&2
  echo "  pipeline_run_id=${pipeline_run_id:-$(echo "$pipeline_json" | jq -r '.pipelineRun.pipelineRunId // "unknown"')}" >&2
  echo "  status=$status" >&2
  echo "  error=$(echo "$pipeline_json" | jq -r '.pipelineRun.error.message // "none"')" >&2
  if [ -n "$api_base_url" ] && [ -n "$pipeline_run_id" ]; then
    echo "  task statuses:" >&2
    fetch_pipeline_tasks "$api_base_url" "$pipeline_run_id" 2>/dev/null \
      | jq -r '.tasks[] | "    \(.pipelineTaskId): \(.status)" + (if .error then "  error=\(.error.message // .error)" else "" end)' >&2 \
      || echo "    (failed to fetch task statuses)" >&2
  fi
  exit 1
}

# run_pipeline <api> <type> <dataset-id> <dataset-version> <params-json> [extra-json]
# Create, dispatch and wait for one PipelineRun (force=true: always a fresh
# run). Prints the pipeline_run_id; the caller asserts on its status.
run_pipeline() {
  local api_base_url="$1" type="$2" dataset_id="$3" dataset_version="$4" params="$5"
  local extra="${6:-}" payload run_id
  [ -n "$extra" ] || extra='{}'
  payload="$(jq -cn --arg t "$type" --arg ds "$dataset_id" --arg v "$dataset_version" \
    --argjson p "$params" --argjson extra "$extra" \
    '{type: $t, dataset_id: $ds, dataset_version: $v, force: true, params: $p} + $extra')"
  run_id="$(extract_pipeline_run_id "$(create_pipeline_run "$api_base_url" "$payload")")"
  dispatch_pipeline_run "$api_base_url" "$run_id" >/dev/null
  poll_pipeline_terminal "$api_base_url" "$run_id" "$POLL_ATTEMPTS" 5 >/dev/null
  echo "$run_id"
}

# task_json <api> <run-id> <pipeline-task-id>
task_json() {
  fetch_pipeline_tasks "$1" "$2" | jq -c --arg t "$3" '.tasks[] | select(.pipelineTaskId == $t)'
}

# ── Job API (atomic jobs, outside any pipeline) ──────────────────────────────

create_job() {
  curl -sS -X POST "$(api_url "$1" "/jobs")" -H "Content-Type: application/json" -d "$2"
}

execute_job() {
  curl -sS -X POST "$(api_url "$1" "/jobs/$2/execute")"
}

fetch_job() {
  curl -sS "$(api_url "$1" "/jobs/$2")"
}

extract_job_id() {
  require_json_field "$1" '.job.jobId' 'jobId'
}

poll_job_terminal() {
  local api_base_url="$1" job_id="$2"
  local max_attempts="${3:-60}" sleep_seconds="${4:-5}"
  local job_json status i
  for i in $(seq 1 "$max_attempts"); do
    job_json="$(fetch_job "$api_base_url" "$job_id")"
    status="$(echo "$job_json" | jq -r '.job.status // empty')"
    echo "  [$i/$max_attempts] status=$status" >&2
    case "$status" in
      succeeded | failed | cancelled | skipped)
        echo "$job_json"
        return 0
        ;;
    esac
    sleep "$sleep_seconds"
  done
  echo "❌ Job did not reach a terminal state after $max_attempts attempts: $job_id" >&2
  exit 1
}

assert_job_succeeded() {
  local job_json="$1" message="${2:-job should succeed}" status
  status="$(echo "$job_json" | jq -r '.job.status')"
  [ "$status" = "succeeded" ] && return 0
  echo "❌ Assertion failed: $message" >&2
  echo "  job_id=$(echo "$job_json" | jq -r '.job.jobId // "unknown"')" >&2
  echo "  status=$status" >&2
  echo "  error=$(echo "$job_json" | jq -r '.job.error.message // .job.error // "none"')" >&2
  exit 1
}

# run_job <api> <type> <dataset-id> <dataset-version> <params-json> [force=true]
# Create, execute and wait for one Job. Prints the terminal job JSON.
run_job() {
  local api_base_url="$1" type="$2" dataset_id="$3" dataset_version="$4" params="$5"
  local force="${6:-true}" payload created job_id
  payload="$(jq -cn --arg t "$type" --arg ds "$dataset_id" --arg v "$dataset_version" \
    --argjson p "$params" --argjson force "$force" \
    '{type: $t, dataset_id: $ds, dataset_version: $v, params: $p, force: $force}')"
  created="$(create_job "$api_base_url" "$payload")"
  job_id="$(extract_job_id "$created")"
  execute_job "$api_base_url" "$job_id" >/dev/null
  poll_job_terminal "$api_base_url" "$job_id" "$POLL_ATTEMPTS" 2 2>/dev/null
}

job_result() {
  echo "$1" | jq -c '.job.result'
}

# ── RobotRun registration ────────────────────────────────────────────────────
#
# The only way to create a RobotRun (ADR-007 §7, §12): publish a finalized
# MCAP + RobotRunManifest with the database-free Recording Publisher, then
# POST /robot-runs:register and wait for the REGISTER_ROBOT_RUN Job.

# register_robot_run <api> <manifest-uri> — prints the terminal Job JSON.
register_robot_run() {
  local submitted job_id
  submitted="$(curl -sS -X POST "$(api_url "$1" "/robot-runs:register")" \
    -H "Content-Type: application/json" -d "{\"manifest_uri\": \"$2\"}")"
  job_id="$(extract_job_id "$submitted")"
  poll_job_terminal "$1" "$job_id" 60 2
}

# register_recording <api> <mcap-path> <run-id> <robot-id> [source-kind=file]
# Publishes a finalized MCAP (a path inside the recording-publisher container,
# e.g. a prepared reference recording under /reference) and registers it:
# publish -> POST /robot-runs:register -> REGISTER_ROBOT_RUN. Prints the
# publication JSON; fails unless the registration Job succeeded.
register_recording() {
  local api="$1" path="$2" run_id="$3" robot_id="$4" kind="${5:-file}"
  local publication registration
  publication="$(compose run --rm -T recording-publisher publish --mcap-path "$path" \
    --run-id "$run_id" --robot-id "$robot_id" --source-kind "$kind" </dev/null)" \
    || fail "publishing $path as $run_id failed"
  registration="$(register_robot_run "$api" "$(echo "$publication" | jq -r '.manifest_uri')")"
  assert_job_succeeded "$registration" "REGISTER_ROBOT_RUN for $run_id should succeed" >&2
  echo "$publication"
}

# ── Datasets and models ──────────────────────────────────────────────────────

upsert_dataset() {
  local api_base_url="$1" dataset_id="$2" name="$3"
  curl -sS -X POST "$(api_url "$api_base_url" "/datasets")" \
    -H "Content-Type: application/json" \
    -d "{\"dataset_id\": \"$dataset_id\", \"name\": \"$name\"}"
}

upsert_dataset_version() {
  local api_base_url="$1" dataset_id="$2" version="$3"
  curl -sS -X POST "$(api_url "$api_base_url" "/datasets/$dataset_id/versions")" \
    -H "Content-Type: application/json" -d "{\"version\": \"$version\"}"
}

# upsert_model <api> <model-id> <model-version> <name> [backend=mock] [endpoint-url]
upsert_model() {
  local api_base_url="$1" model_id="$2" model_version="$3" name="$4"
  local backend="${5:-mock}" endpoint_url="${6:-}" body
  if ! curl -fsS "$(api_url "$api_base_url" "/models/$model_id")" >/dev/null 2>&1; then
    curl -sS -X POST "$(api_url "$api_base_url" "/models")" \
      -H "Content-Type: application/json" \
      -d "{\"modelId\": \"$model_id\", \"name\": \"$name\", \"metadata\": {}}" >/dev/null
  fi
  if curl -fsS "$(api_url "$api_base_url" "/models/$model_id/versions/$model_version")" >/dev/null 2>&1; then
    return 0
  fi
  body="$(jq -cn --arg v "$model_version" --arg b "$backend" --arg e "$endpoint_url" \
    '{version: $v, backend: $b, metadata: {}} + (if $e == "" then {} else {endpoint_url: $e} end)')"
  curl -sS -X POST "$(api_url "$api_base_url" "/models/$model_id/versions")" \
    -H "Content-Type: application/json" -d "$body" >/dev/null
}

# poll_inference_ready <inference-server-url> [attempts] [sleep-seconds]
poll_inference_ready() {
  local inference_url="$1" max_attempts="${2:-60}" sleep_seconds="${3:-2}"
  local resp status model_loaded i
  for i in $(seq 1 "$max_attempts"); do
    resp="$(curl -sf "${inference_url}/readyz" 2>/dev/null || echo '{}')"
    status="$(echo "$resp" | jq -r '.status // empty' 2>/dev/null || true)"
    model_loaded="$(echo "$resp" | jq -r '.model_loaded // false' 2>/dev/null || true)"
    echo "  [$i/$max_attempts] status=$status model_loaded=$model_loaded" >&2
    if [ "$status" = "ready" ] && [ "$model_loaded" = "true" ]; then
      echo "$resp"
      return 0
    fi
    sleep "$sleep_seconds"
  done
  fail "inference server did not become ready after $((max_attempts * sleep_seconds))s"
}

# ── Artifacts ────────────────────────────────────────────────────────────────

# count_artifacts <api> <kind> <owner-type> <owner-id> — pages through the list.
count_artifacts() {
  local api_base_url="$1" kind="$2" owner_type="$3" owner_id="$4"
  local offset=0 total=0 page
  while :; do
    page="$(api_get "$api_base_url" "/artifacts?kind=$kind&owner_type=$owner_type&owner_id=$owner_id&limit=500&offset=$offset" | jq '.artifacts | length')"
    total=$((total + page))
    [ "$page" -lt 500 ] && break
    offset=$((offset + 500))
  done
  echo "$total"
}

# assert_artifact_kind_present <artifacts-json> <kind> <message>
assert_artifact_kind_present() {
  local count
  count="$(echo "$1" | jq --arg k "$2" '[.artifacts[] | select(.kind == $k)] | length')"
  if [ "${count:-0}" -lt 1 ]; then
    echo "❌ Artifact kind '$2' not registered: $3" >&2
    echo "$1" | jq . >&2
    exit 1
  fi
}

# ── Containers ───────────────────────────────────────────────────────────────
#
# One-shot data-plane containers.

compose() {
  docker compose --env-file "$ENV_FILE" --profile acquisition "$@"
}

# worker_python <args...> — run Python inside the worker image, with the
# ArtifactStore settings of the compose environment and the repository's
# scripts mounted at /workspace/scripts. The host needs neither uv nor Python.
worker_python() {
  docker compose --env-file "$ENV_FILE" --profile debug run --rm -T --entrypoint python worker-cli "$@"
}
