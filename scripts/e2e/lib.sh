#!/usr/bin/env bash
# lib.sh — shared helpers for SceneOps E2E scripts

# ── Default E2E resource identity (SceneOps V2 Request 3.2A) ──────────────────
#
# Every E2E workflow that auto-creates a dataset (the caller supplied no
# DATASET_ID/DATASET_VERSION) must default to an identity that is
# unambiguously test-owned -- never something a real developer might
# independently choose for genuine local-dev data. The old "nuscenes"/
# "v1.0-mini" default was exactly that collision: `make register-nuscenes-
# dataset` (scripts/fixtures/register_nuscenes_dataset.sh, unrelated to this
# convention and deliberately left as "nuscenes") registers a real,
# intentionally-named local fixture under that same identity for manual
# UI/API exploration, which every E2E script's identical default silently
# shared/mutated.
#
# An explicit DATASET_ID/DATASET_VERSION from the environment always wins;
# these are only the fallback when the caller supplies neither. This is
# also the naming rule any future automated cleanup must key off (safe to
# target "test-e2e-*"/"test-v1"; never safe to touch a caller-supplied
# identity).
DEFAULT_E2E_DATASET_PREFIX="${DEFAULT_E2E_DATASET_PREFIX:-test-e2e}"
DEFAULT_E2E_DATASET_VERSION="${DEFAULT_E2E_DATASET_VERSION:-test-v1}"

# ── E2E fixture catalog: sceneops-e2e-v1 (SceneOps V2 Request 3.2B) ────────────
#
# Request 3.2A gave each workflow its OWN derived identity
# ("${DEFAULT_E2E_DATASET_PREFIX}-scene", "-episode", "-raw-log", ...) --
# workable, but it meant one canonical Dataset/DatasetVersion per workflow
# even where nothing about the data actually required that. This catalog
# replaces that per-script derivation with two shared logical fixtures:
#
#   core     Scene ingestion, analytics export, detection evaluation,
#            scenario/episode curation, episode building. Canonical
#            test-e2e-core/test-v1. External source: the real nuScenes mini
#            fixture (format=nuscenes, format_version=v1.0-mini -- see
#            ExternalDatasetRef, sceneops_core.datasets.ExternalDatasetRef)
#            for scene-family workflows; the MCAP fixture recorded by
#            `make e2e-robot-can-replay` for the episode family. Both
#            families coexist on one DatasetVersion by design -- Scene and
#            Episode each own an independent summary sub-object on
#            DatasetVersionRecord that never overwrites the other's (see
#            packages/sceneops-core/tests/test_dataset_version_summaries.py).
#   interop  External-adapter/interoperability round-trip tests. Canonical
#            test-e2e-interop/test-v1. Source: the deterministic golden
#            learning fixture built by
#            sceneops_analytics.testing.interop_dataset (Request 3.2) --
#            no shell ingestion path exists for it yet (Python-only today).
#
# raw-log-scene-building deliberately stays its OWN identity, OUTSIDE
# `core`: it produces non-ground-truth scenes that measurably drag down
# `core`'s aggregate /quality readiness if they share one DatasetVersion
# (see makefiles/e2e.mk's comment on e2e-raw-log-scene-building) -- a real,
# previously-discovered data-requirement conflict, not an oversight. It
# still uses this same resolver for consistency.
#
# CANONICAL vs. SOURCE identity (Request 3.2B §2): DATASET_ID/
# DATASET_VERSION below are SceneOps' own canonical identity ONLY -- never
# constrained by what an external format's SDK happens to require.
# SOURCE_FORMAT/SOURCE_FORMAT_VERSION/SOURCE_ROOT_URI describe the external
# nuScenes source separately. This is what closes the identity ambiguity
# Request 3.2A left open (DATASET_VERSION had to stay "v1.0-mini" there
# because dataset_scene_ingestion/raw_log_scene_building passed it straight
# into `nuscenes-devkit`'s `NuScenes(version=..., ...)`); the job handlers
# now read the `source_format_version` param instead
# (apps/worker/sceneops_worker/jobs/dataset/ingest_scenes.py,
# datasets/ingestion/nuscenes_raw_log.py) -- Request 3.2B.1 removed the
# short-lived fallback to dataset_version entirely: source_format_version
# is required whenever the source format needs one (nuScenes), enforced by
# IngestScenesJobParams/BuildScenesJobParams at job-creation time, so a
# caller that omits it fails clearly instead of silently reusing
# dataset_version.

# resolve_e2e_fixture <fixture-name>
# Sets DATASET_ID/DATASET_VERSION (and, for fixtures with an external
# nuScenes source, SOURCE_FORMAT/SOURCE_FORMAT_VERSION/SOURCE_ROOT_URI, or
# RAW_SOURCE_ROOT_URI for raw-log) to this fixture's defaults -- but ONLY
# for whichever of those the caller's environment left unset, via bash's
# `: "${VAR:=default}"` assign-if-unset idiom, so an explicit
# `DATASET_ID=... make e2e-...` always wins untouched. Call once, right
# after sourcing lib.sh; no further "${DATASET_ID:-...}" line is needed
# afterward.
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
    raw-log)
      # Isolated on purpose -- see the catalog note above.
      : "${DATASET_ID:=test-e2e-raw-log}"
      : "${DATASET_VERSION:=test-v1}"
      : "${SOURCE_FORMAT:=nuscenes}"
      : "${SOURCE_FORMAT_VERSION:=v1.0-mini}"
      : "${RAW_SOURCE_ROOT_URI:=/data/raw/nuscenes}"
      ;;
    *)
      echo "❌ unknown E2E fixture: '$fixture_name' (expected core|interop|raw-log)" >&2
      return 1
      ;;
  esac
}

# ── Service readiness ───────────────────────────────────────────────────────────

# require_service <name> <health_url> [max_attempts=1] [sleep_seconds=1] [hint]
# Polls <health_url> with `curl -sf` until it responds successfully, or exits
# with a clear, actionable error. Centralizes what e2e_api_smoke.sh (a
# polling wait_for_health loop) and e2e_detection_evaluation_groundingdino.sh
# (a one-shot check) each implemented independently (SceneOps V2 Request
# 3.2A) -- one call site covers both by varying max_attempts.
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

upsert_robot_run() {
  local api_base_url="$1"
  local run_id="$2"
  local robot_id="$3"
  local mcap_uri="$4"

  local existing
  existing="$(curl -sS "$(api_url "$api_base_url" "/robot-runs/$run_id")")"

  if echo "$existing" | jq -e '.robotRun' >/dev/null 2>&1; then
    echo "$existing"
    return 0
  fi

  curl -sS -X POST "$(api_url "$api_base_url" "/robot-runs")" \
    -H "Content-Type: application/json" \
    -d "{\"run_id\": \"$run_id\", \"robot_id\": \"$robot_id\", \"mcap_uri\": \"$mcap_uri\"}"
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
  # raw_source_root_uri is Scene-owned (SceneOps V2 Request 04) — Episode
  # dataset versions have no use for it (their source is RobotRun.mcap_uri),
  # so it's optional here. Omit it to create/patch a version with no Scene
  # raw-source config at all.
  local raw_source_root_uri="${4:-}"

  local existing
  existing="$(curl -sS "$(api_url "$api_base_url" "/datasets/$dataset_id/versions/$version")")"

  if [ -n "$raw_source_root_uri" ]; then
    patch_body="{\"raw_source_root_uri\": \"$raw_source_root_uri\", \"required_channels\": [\"CAM_FRONT\", \"LIDAR_TOP\"]}"
    create_body="{\"version\": \"$version\", \"raw_source_root_uri\": \"$raw_source_root_uri\", \"metadata\": {}}"
  else
    patch_body=""
    create_body="{\"version\": \"$version\", \"metadata\": {}}"
  fi

  if echo "$existing" | jq -e '.version' >/dev/null 2>&1; then
    if [ -z "$patch_body" ]; then
      # Nothing Scene-specific to patch — the version already exists as-is.
      echo "$existing"
      return 0
    fi
    curl -sS -X PATCH "$(api_url "$api_base_url" "/datasets/$dataset_id/versions/$version")" \
      -H "Content-Type: application/json" \
      -d "$patch_body"
    return 0
  fi

  curl -sS -X POST "$(api_url "$api_base_url" "/datasets/$dataset_id/versions")" \
    -H "Content-Type: application/json" \
    -d "$create_body"
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
# Generic GET /artifacts?owner_type=...&owner_id=... fetch -- centralizes
# what e2e_dataset_scene_ingestion.sh, e2e_analytics_export.sh, and
# e2e_episode_curation.sh each wrote as an inline curl call (SceneOps V2
# Request 3.2A). Pair with assert_artifact_kind_present below rather than
# re-deriving a count with ad hoc jq.
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
