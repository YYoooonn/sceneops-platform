#!/usr/bin/env bash
# lib.sh — shared helpers for SceneOps E2E scripts

# ── Default E2E resource identity ──────────────────────────────────────────────
#
# Every E2E workflow that auto-creates a dataset (the caller supplied no
# DATASET_ID/DATASET_VERSION) must default to an identity that is
# unambiguously test-owned -- never something a real developer might
# independently choose for genuine local-dev data.
#
# An explicit DATASET_ID/DATASET_VERSION from the environment always wins;
# these are only the fallback when the caller supplies neither. This is
# also the naming rule any future automated cleanup must key off (safe to
# target "test-e2e-*"/"test-v1"; never safe to touch a caller-supplied
# identity).
DEFAULT_E2E_DATASET_PREFIX="${DEFAULT_E2E_DATASET_PREFIX:-test-e2e}"
DEFAULT_E2E_DATASET_VERSION="${DEFAULT_E2E_DATASET_VERSION:-test-v1}"

# ── E2E fixture catalog: sceneops-e2e-v1 ───────────────────────────────────────
#
# Two shared logical fixtures cover every E2E workflow, rather than one
# derived identity per workflow:
#
#   core     Recording-backed Scene building, analytics export,
#            reliability / Airflow verification, episode building and
#            curation. Canonical test-e2e-core/test-v1. Source data: the
#            real nuScenes mini fixture, read only by the acquisition tool
#            (Scenes) and the CAN replay (Episodes). Both
#            families coexist on one DatasetVersion by design -- Scene and
#            Episode each own an independent summary sub-object on
#            DatasetVersionRecord that never overwrites the other's (see
#            packages/sceneops-core/tests/test_dataset_version_summaries.py).
#   interop  External-adapter/interoperability round-trip tests. Canonical
#            test-e2e-interop/test-v1. Source: the deterministic golden
#            learning fixture built by
#            sceneops_analytics.testing.interop_dataset -- no shell
#            ingestion path exists for it yet (Python-only today).
#
# CANONICAL vs. SOURCE identity: DATASET_ID/DATASET_VERSION below are
# SceneOps' own canonical identity ONLY -- never constrained by what an
# external format's SDK happens to require. SOURCE_FORMAT/
# SOURCE_FORMAT_VERSION/SOURCE_ROOT_URI describe the nuScenes fixture on
# disk that acquisition reads; they never enter canonical identity.

# resolve_e2e_fixture <fixture-name>
# Sets DATASET_ID/DATASET_VERSION (and, for fixtures with an external
# nuScenes source, SOURCE_FORMAT/SOURCE_FORMAT_VERSION/SOURCE_ROOT_URI) to
# this fixture's defaults -- but ONLY for whichever of those the caller's
# environment left unset, via bash's `: "${VAR:=default}"` assign-if-unset
# idiom, so an explicit `DATASET_ID=... make e2e-...` always wins untouched.
# Call once, right after sourcing lib.sh; no further "${DATASET_ID:-...}"
# line is needed afterward.
#
# SOURCE_ROOT_URI is the one authoritative name for "the nuScenes dataroot's
# parent directory".
resolve_e2e_fixture() {
  local fixture_name="$1"
  case "$fixture_name" in
    core)
      : "${DATASET_ID:=test-e2e-core}"
      : "${DATASET_VERSION:=test-v1}"
      : "${SOURCE_FORMAT:=nuscenes}"
      : "${SOURCE_FORMAT_VERSION:=v1.0-mini}"
      : "${SOURCE_ROOT_URI:=/data/raw/nuscenes}"
      ;;
    interop)
      : "${DATASET_ID:=test-e2e-interop}"
      : "${DATASET_VERSION:=test-v1}"
      ;;
    *)
      echo "❌ unknown E2E fixture: '$fixture_name' (expected core|interop)" >&2
      return 1
      ;;
  esac
}

# require_mcap_file <path>
# Fail-fast existence check for a recorded rosbag2/MCAP file, run BEFORE any
# persistent call (Robot/DatasetVersion upserts, RobotRun publication and
# registration) so a missing recording fails immediately and cleanly instead
# of partway through already-committed state. <path> is the REPO-ROOT-RELATIVE
# path (e.g. /data/raw/rosbag/scene-0061/scene-0061_0.mcap) -- the caller is
# responsible for resolving it against $REPO_ROOT on the host filesystem.
require_mcap_file() {
  local repo_root="$1"
  local mcap_uri="$2"
  if [ ! -f "${repo_root}${mcap_uri}" ]; then
    echo "❌ Expected bag file not found: ${mcap_uri}" >&2
    echo "   Record it first: make e2e-robot-can-replay SCENE=<scene>" >&2
    echo "   (or run make e2e-robot-learning, which records it automatically)" >&2
    exit 1
  fi
}

# episode_building_run_id <repo_root> <scene>
# The RobotRun id e2e_episode_building.sh registers for <scene>'s recorded
# bag, derived from the bag's content. RobotRuns are immutable and a run's
# recording key is write-once, while e2e-robot-can-replay re-records the bag
# with new bytes: a rerun over the same bag reuses its RobotRun (idempotent
# publish + registration), a re-recorded bag gets a new one.
episode_building_run_id() {
  local repo_root="$1"
  local scene="$2"
  local sha
  sha="$(shasum -a 256 "${repo_root}/data/raw/rosbag/${scene}/${scene}_0.mcap" | cut -c1-12)"
  echo "run-${scene}-episodes-${sha}"
}

# ── Service readiness ───────────────────────────────────────────────────────────

# require_service <name> <health_url> [max_attempts=1] [sleep_seconds=1] [hint]
# Polls <health_url> with `curl -sf` until it responds successfully, or exits
# with a clear, actionable error. One call site covers both a polling wait
# (max_attempts > 1) and a one-shot readiness check (max_attempts=1).
require_service() {
  local name="$1"
  local health_url="$2"
  local max_attempts="${3:-1}"
  local sleep_seconds="${4:-1}"
  local hint="${5:-}"

  local attempt
  for attempt in $(seq 1 "$max_attempts"); do
    if curl -sf "$health_url" >/dev/null 2>&1; then
      echo "✅ $name reachable at $health_url" >&2
      return 0
    fi
    if [ "$attempt" -lt "$max_attempts" ]; then
      sleep "$sleep_seconds"
    fi
  done

  echo "❌ $name not reachable at $health_url (after $max_attempts attempt(s))" >&2
  if [ -n "$hint" ]; then
    echo "  $hint" >&2
  fi
  exit 1
}

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

assert_json_less_or_equal() {
  local json="$1"
  local jq_expr="$2"
  local max_value="$3"
  local message="$4"

  local is_lte
  is_lte=$(echo "$json" | jq "$jq_expr <= $max_value")

  if [ "$is_lte" != "true" ]; then
    local actual
    actual=$(echo "$json" | jq -r "$jq_expr")

    echo "❌ Assertion failed: $message" >&2
    echo "  Expected actual value to be LESS THAN OR EQUAL TO $max_value, but got $actual" >&2
    echo "  expr=$jq_expr" >&2
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
  # Optional — when provided, a failure also prints per-task statuses so a
  # failing E2E run doesn't require a second manual API call to see which
  # task actually broke.
  local api_base_url="${3:-}"
  local pipeline_run_id="${4:-}"

  local status
  status="$(echo "$pipeline_json" | jq -r '.pipelineRun.status')"
  if [ "$status" = "succeeded" ]; then
    return 0
  fi

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

# ── Job API (standalone jobs, not part of a pipeline) ──────────────────────────

create_job() {
  local api_base_url="$1"
  local payload="$2"
  curl -sS -X POST "$(api_url "$api_base_url" "/jobs")" \
    -H "Content-Type: application/json" \
    -d "$payload"
}

execute_job() {
  local api_base_url="$1"
  local job_id="$2"
  curl -sS -X POST "$(api_url "$api_base_url" "/jobs/$job_id/execute")"
}

fetch_job() {
  local api_base_url="$1"
  local job_id="$2"
  curl -sS "$(api_url "$api_base_url" "/jobs/$job_id")"
}

extract_job_id() {
  local json="$1"
  require_json_field "$json" '.job.jobId' 'jobId'
}

poll_job_terminal() {
  local api_base_url="$1"
  local job_id="$2"
  local max_attempts="${3:-60}"
  local sleep_seconds="${4:-5}"

  local job_json status

  for i in $(seq 1 "$max_attempts"); do
    job_json="$(fetch_job "$api_base_url" "$job_id")"
    status="$(echo "$job_json" | jq -r '.job.status // empty')"

    echo "  [$i/$max_attempts] status=$status" >&2

    case "$status" in
      succeeded|failed|cancelled|skipped)
        echo "$job_json"
        return 0
        ;;
    esac

    sleep "$sleep_seconds"
  done

  echo "❌ Job did not reach terminal state after $max_attempts attempts: $job_id" >&2
  exit 1
}

assert_job_succeeded() {
  local job_json="$1"
  local message="${2:-job should succeed}"

  local status
  status="$(echo "$job_json" | jq -r '.job.status')"
  if [ "$status" = "succeeded" ]; then
    return 0
  fi

  echo "❌ Assertion failed: $message" >&2
  echo "  job_id=$(echo "$job_json" | jq -r '.job.jobId // "unknown"')" >&2
  echo "  status=$status" >&2
  echo "  error=$(echo "$job_json" | jq -r '.job.error.message // .job.error // "none"')" >&2
  exit 1
}

# ── Robot / RobotRun API ────────────────────────────────────────────────────

upsert_robot() {
  local api_base_url="$1"
  local robot_id="$2"
  local platform="$3"

  local existing
  existing="$(curl -sS "$(api_url "$api_base_url" "/robots/$robot_id")")"

  if echo "$existing" | jq -e '.robot' >/dev/null 2>&1; then
    echo "$existing"
    return 0
  fi

  curl -sS -X POST "$(api_url "$api_base_url" "/robots")" \
    -H "Content-Type: application/json" \
    -d "{\"robot_id\": \"$robot_id\", \"platform\": \"$platform\"}"
}

# Canonical RobotRun creation (ADR-007 §7, §12): publish a finalized local
# MCAP + its RobotRunManifest with the database-free Recording Publisher,
# then submit REGISTER_ROBOT_RUN via POST /robot-runs:register and wait for
# the Job. There is no other way to create a RobotRun.
#
# publish_robot_run_recording runs the publisher as its own process
# (`python -m sceneops_integrations.recording publish`, never inside Celery)
# in the worker-cli image, mapping the worker's ArtifactStore settings onto
# the publisher's SCENEOPS_PUBLISHER_ARTIFACT__* env. mcap_path must be
# visible inside that container (the repo's bind-mounted ./data:/data).
# Prints the publication JSON; exits non-zero on failure (e.g. a write-once
# conflict).
#
# Usage: publish_robot_run_recording REPO_ROOT ROBOT_ID RUN_ID MCAP_PATH
#          [SOURCE_KIND=file] [ROBOT_PLATFORM] [SOURCE_TOPIC]
publish_robot_run_recording() {
  local repo_root="$1"
  local robot_id="$2"
  local run_id="$3"
  local mcap_path="$4"
  local source_kind="${5:-file}"
  local robot_platform="${6:-}"
  local source_topic="${7:-}"

  local -a extra_args=()
  if [ -n "$robot_platform" ]; then
    extra_args+=(--robot-platform "$robot_platform")
  fi
  if [ -n "$source_topic" ]; then
    extra_args+=(--source-topic "$source_topic")
  fi

  docker compose -f "$repo_root/compose.yaml" --env-file "$repo_root/.env.local" \
    --profile debug --profile worker run --rm -T worker-cli sh -c '
      export SCENEOPS_PUBLISHER_ARTIFACT__BACKEND="$SCENEOPS_WORKER_ARTIFACT__BACKEND"
      export SCENEOPS_PUBLISHER_ARTIFACT__ROOT_URI="$SCENEOPS_WORKER_ARTIFACT__ROOT_URI"
      export SCENEOPS_PUBLISHER_ARTIFACT__ENDPOINT_URL="$SCENEOPS_WORKER_ARTIFACT__ENDPOINT_URL"
      export SCENEOPS_PUBLISHER_ARTIFACT__REGION="$SCENEOPS_WORKER_ARTIFACT__REGION"
      export SCENEOPS_PUBLISHER_ARTIFACT__ACCESS_KEY_ID="$SCENEOPS_WORKER_ARTIFACT__ACCESS_KEY_ID"
      export SCENEOPS_PUBLISHER_ARTIFACT__SECRET_ACCESS_KEY="$SCENEOPS_WORKER_ARTIFACT__SECRET_ACCESS_KEY"
      exec python -m sceneops_integrations.recording publish "$@"
    ' publish \
    --mcap-path "$mcap_path" --run-id "$run_id" --robot-id "$robot_id" \
    --source-kind "$source_kind" ${extra_args[@]+"${extra_args[@]}"}
}

# Submits POST /robot-runs:register and prints the submission response
# ({job, execution}). An identical manifest_uri returns the existing
# equivalent Job (execution-key dedup) with execution=null.
submit_robot_run_registration() {
  local api_base_url="$1"
  local manifest_uri="$2"
  curl -sS -X POST "$(api_url "$api_base_url" "/robot-runs:register")" \
    -H "Content-Type: application/json" \
    -d "{\"manifest_uri\": \"$manifest_uri\"}"
}

# Submits POST /robot-runs:register and prints the terminal Job JSON
# (succeeded or failed -- callers assert).
register_robot_run() {
  local api_base_url="$1"
  local manifest_uri="$2"

  local submitted job_id
  submitted="$(submit_robot_run_registration "$api_base_url" "$manifest_uri")"
  job_id="$(extract_job_id "$submitted")"
  poll_job_terminal "$api_base_url" "$job_id" 60 2
}

# publish_robot_run_recording + register_robot_run, asserting success. Both
# steps are idempotent: an identical retry re-uses the published objects
# and the registration Job reports created=false. Prints the succeeded Job
# JSON.
#
# Usage: publish_and_register_robot_run REPO_ROOT API_BASE_URL ROBOT_ID RUN_ID
#          MCAP_PATH [SOURCE_KIND=file] [ROBOT_PLATFORM] [SOURCE_TOPIC]
publish_and_register_robot_run() {
  local repo_root="$1"
  local api_base_url="$2"

  local publication manifest_uri job_json
  publication="$(publish_robot_run_recording "$repo_root" "${@:3}")"
  echo "  published: $publication" >&2
  manifest_uri="$(echo "$publication" | jq -r '.manifest_uri // empty')"
  if [ -z "$manifest_uri" ]; then
    echo "❌ Recording publication did not return a manifest_uri" >&2
    exit 1
  fi

  job_json="$(register_robot_run "$api_base_url" "$manifest_uri")"
  assert_job_succeeded "$job_json" "REGISTER_ROBOT_RUN should succeed for $4"
  echo "$job_json"
}

fetch_missions() {
  local api_base_url="$1"
  local robot_run_id="$2"
  curl -sS "$(api_url "$api_base_url" "/missions?robot_run_id=$robot_run_id")"
}

fetch_robot_states() {
  local api_base_url="$1"
  local robot_run_id="$2"
  local limit="${3:-1}"
  curl -sS "$(api_url "$api_base_url" "/robot-states?robot_run_id=$robot_run_id&limit=$limit")"
}

# ── Intentionally unavailable workflows ───────────────────────────────────────

# unavailable_until COMMAND STEP REASON
# Stops a workflow that cannot run on recording-derived Scenes yet, with an
# explicit message instead of failing on removed legacy infrastructure.
# Exit code 3 distinguishes "intentionally unavailable" from a test failure.
unavailable_until() {
  echo "⛔ $1 is unavailable until ADR-007 implementation step $2: $3" >&2
  echo "   See docs/architecture/reserved-and-limitations.md §2." >&2
  exit 3
}

# ── Recording-backed Scene fixture ────────────────────────────────────────────

# ensure_camera_robot_run RUN_ID [SOURCE_UNIT=scene-0061] [SOURCE_VERSION=v1.0-mini]
# A registered, sensor-bearing RobotRun for infrastructure checks that need
# a recording_scene_building input: a camera-only nuScenes acquisition
# (dataset-acquisition container) -> L1 check + publication
# (recording-publisher container) -> POST /robot-runs:register. Acquisition
# is deterministic, so RUN_ID names one recording: when the RobotRun is
# already registered nothing is redone. Needs Docker Compose and the
# acquisition image (make acquisition-image); prints nothing on stdout.
ensure_camera_robot_run() {
  local run_id="$1"
  local source_unit="${2:-scene-0061}"
  local source_version="${3:-v1.0-mini}"
  local api_base_url="${API_BASE_URL:-http://localhost:8000}"
  local compose=(docker compose --env-file "${ENV_FILE:-.env.local}" --profile acquisition)
  local recording="/recordings/$run_id.mcap"

  if [ "$(curl -sS -o /dev/null -w '%{http_code}' "$(api_url "$api_base_url" "/robot-runs/$run_id")")" = "200" ]; then
    echo "  RobotRun $run_id already registered" >&2
    return 0
  fi
  echo "  acquiring $source_version/$source_unit (camera) as RobotRun $run_id" >&2
  "${compose[@]}" run --rm -T dataset-acquisition nuscenes --dataroot /input/nuscenes \
    --version "$source_version" --source-unit "$source_unit" --channels camera \
    --output "$recording" >/dev/null
  local publication job_json
  publication="$("${compose[@]}" run --rm -T recording-publisher publish \
    --mcap-path "$recording" --run-id "$run_id" --robot-id "robot-$run_id" --source-kind file)"
  "${compose[@]}" run --rm -T --entrypoint rm dataset-acquisition -f "$recording" >/dev/null 2>&1 || true
  job_json="$(register_robot_run "$api_base_url" "$(echo "$publication" | jq -r '.manifest_uri')")"
  assert_job_succeeded "$job_json" "REGISTER_ROBOT_RUN of $run_id should succeed"
}

# camera_scene_build_config [DURATION_NS=10000000000]
# The recording_scene_building build_config for an ensure_camera_robot_run
# recording: the front camera, its CameraInfo and /tf_static, segmented on
# the cameras' header-stamp clock.
camera_scene_build_config() {
  jq -cn --argjson d "${1:-10000000000}" '{
    channels: [
      {topic: "/camera/front/image/compressed", modality: "camera", sensor_id: "cam-front",
       time: {source: "header_stamp", clock: "sensor.header_stamp"},
       payload: "compressed_image", camera_info_topic: "/camera/front/camera_info"}
    ],
    frames: {ego_frame_id: "base_link"},
    calibration: {static_transform_topics: ["/tf_static"]},
    segmentation: {policy: "fixed_duration", clock: "sensor.header_stamp", duration_ns: $d}
  }'
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

  local existing
  existing="$(curl -sS "$(api_url "$api_base_url" "/datasets/$dataset_id/versions/$version")")"
  if echo "$existing" | jq -e '.version' >/dev/null 2>&1; then
    echo "$existing"
    return 0
  fi

  curl -sS -X POST "$(api_url "$api_base_url" "/datasets/$dataset_id/versions")" \
    -H "Content-Type: application/json" \
    -d "{\"version\": \"$version\", \"metadata\": {}}"
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

# ── Artifact API ──────────────────────────────────────────────────────────────

fetch_inference_run_artifacts() {
  local api_base_url="$1"
  local inference_run_id="$2"
  curl -sS "$(api_url "$api_base_url" "/inference/runs/$inference_run_id/artifacts")"
}

fetch_evaluation_run_artifacts() {
  local api_base_url="$1"
  local evaluation_run_id="$2"
  curl -sS "$(api_url "$api_base_url" "/evaluations/runs/$evaluation_run_id/artifacts")"
}

fetch_evaluation_run_metrics() {
  local api_base_url="$1"
  local evaluation_run_id="$2"
  curl -sS "$(api_url "$api_base_url" "/evaluations/runs/$evaluation_run_id/metrics")"
}

# fetch_artifacts_by_owner <api_base_url> <owner_type> <owner_id>
# Generic GET /artifacts?owner_type=...&owner_id=... fetch, shared by every
# E2E script that needs to check an owner's registered artifacts. Pair with
# assert_artifact_kind_present below rather than re-deriving a count with ad
# hoc jq.
fetch_artifacts_by_owner() {
  local api_base_url="$1"
  local owner_type="$2"
  local owner_id="$3"
  curl -sS "$(api_url "$api_base_url" "/artifacts?owner_type=$owner_type&owner_id=$owner_id")"
}

# Assert that at least one artifact of the given kind is present.
# artifacts_json: response from fetch_*_run_artifacts
# kind: ArtifactKind value, e.g. "prediction_manifest"
assert_artifact_kind_present() {
  local artifacts_json="$1"
  local kind="$2"
  local message="$3"

  local count
  count="$(echo "$artifacts_json" | jq --arg k "$kind" '[.artifacts[] | select(.kind == $k)] | length')"

  if [ "${count:-0}" -lt 1 ]; then
    echo "❌ Artifact kind '$kind' not registered: $message" >&2
    echo "$artifacts_json" | jq . >&2
    exit 1
  fi
}

# ── Detection-evaluation shared assertions ─────────────────────────────────────
#
# Shared by e2e_perception.sh's mock and grounding_dino BACKEND branches, so
# the assertion logic exists in exactly one place regardless of backend.
# Backend-specific checks
# (lifting-metric counters, warmup/readiness polling) stay in the caller;
# these cover only the fields every InferenceRunRecord/EvaluationRunRecord/
# leaderboard entry must have regardless of backend.

# assert_inference_run <api_base_url> <inference_run_id> <max_sample_count_or_empty>
# Prints the fetched InferenceRunRecord JSON on success (caller captures it
# for any backend-specific follow-up checks).
assert_inference_run() {
  local api_base_url="$1"
  local inference_run_id="$2"
  local max_sample_count="${3:-}"

  local inference_json
  inference_json="$(curl -sS "$(api_url "$api_base_url" "/inference/runs/$inference_run_id")")"

  echo "  status=$(echo "$inference_json" | jq -r '.run.status')" >&2
  echo "  predictionCount=$(echo "$inference_json" | jq -r '.run.predictionCount // 0')" >&2
  echo "  predictionManifestUri=$(echo "$inference_json" | jq -r '.run.predictionManifestUri // empty')" >&2

  assert_json_equals "$inference_json" '.run.status' 'succeeded' \
    'inference run should be succeeded'
  assert_json_not_empty "$inference_json" '.run.predictionManifestUri' \
    'inference run predictionManifestUri'
  if [ -n "$max_sample_count" ]; then
    assert_json_less_or_equal "$inference_json" '.run.sampleCount' "$max_sample_count" \
      "inference run sampleCount should be <= $max_sample_count"
  else
    assert_json_gt "$inference_json" '.run.sampleCount // 0' 0 'inference run sampleCount'
  fi

  echo "$inference_json"
}

# assert_evaluation_run <api_base_url> <evaluation_run_id>
# Prints the fetched EvaluationRunRecord JSON on success.
assert_evaluation_run() {
  local api_base_url="$1"
  local evaluation_run_id="$2"

  local eval_json
  eval_json="$(curl -sS "$(api_url "$api_base_url" "/evaluations/runs/$evaluation_run_id")")"

  echo "  status=$(echo "$eval_json" | jq -r '.run.status')" >&2
  echo "  primaryMetricName=$(echo "$eval_json" | jq -r '.run.primaryMetricName // empty')  primaryMetricValue=$(echo "$eval_json" | jq -r '.run.primaryMetricValue // empty')" >&2

  assert_json_equals "$eval_json" '.run.status' 'succeeded' \
    'evaluation run should be succeeded'
  assert_json_not_empty "$eval_json" '.run.primaryMetricName' \
    'evaluation run primaryMetricName'
  assert_json_not_empty "$eval_json" '.run.primaryMetricValue' \
    'evaluation run primaryMetricValue'
  assert_json_not_empty "$eval_json" '.run.evaluationUnit' \
    'evaluation run evaluationUnit'
  assert_json_not_empty "$eval_json" '.run.evaluationManifestUri' \
    'evaluation run evaluationManifestUri'

  echo "$eval_json"
}

# assert_leaderboard_entry <api_base_url> <dataset_id> <dataset_version> <evaluation_run_id>
# Never falls back to entries[0] under a persistent stack's accumulated
# history -- that would silently validate a DIFFERENT, unrelated historical
# run instead of catching that THIS run's own leaderboard entry is missing.
assert_leaderboard_entry() {
  local api_base_url="$1"
  local dataset_id="$2"
  local dataset_version="$3"
  local evaluation_run_id="$4"

  local lb_json lb_entry
  lb_json="$(curl -sS "$(api_url "$api_base_url" "/leaderboards/evaluations?dataset_id=$dataset_id&dataset_version=$dataset_version")")"

  local lb_count
  lb_count="$(echo "$lb_json" | jq '.entries | length')"
  if [ "${lb_count:-0}" -lt 1 ]; then
    echo "❌ Expected at least 1 leaderboard entry" >&2
    exit 1
  fi

  lb_entry="$(echo "$lb_json" | jq --arg eid "$evaluation_run_id" '.entries[] | select(.evaluationRunId == $eid)')"
  if [ -z "$lb_entry" ]; then
    echo "❌ No leaderboard entry found for this run's evaluation_run_id=$evaluation_run_id" >&2
    echo "$lb_json" | jq '.entries[] | {evaluationRunId, primaryMetricName, primaryMetricValue}' >&2
    exit 1
  fi

  local lb_primary_name lb_primary_value
  lb_primary_name="$(echo "$lb_entry" | jq -r '.primaryMetricName // empty')"
  lb_primary_value="$(echo "$lb_entry" | jq -r '.primaryMetricValue // empty')"
  echo "  primaryMetricName=$lb_primary_name  primaryMetricValue=$lb_primary_value" >&2

  [ -n "$lb_primary_name" ] || { echo "❌ Leaderboard entry missing primaryMetricName" >&2; exit 1; }
  [ -n "$lb_primary_value" ] || { echo "❌ Leaderboard entry missing primaryMetricValue" >&2; exit 1; }

  echo "$lb_entry"
}
