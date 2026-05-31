#!/usr/bin/env bash

api_url() {
  local api_base_url="$1"
  local path="$2"
  local prefix="${API_PREFIX:-/api/v1}"

  echo "${api_base_url}${prefix}${path}"
}

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

json_field_or_empty() {
  local json="$1"
  local jq_expr="$2"

  echo "$json" | jq -r "$jq_expr // empty"
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
    echo "expected=$expected actual=$actual expr=$jq_expr" >&2
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

fetch_validation_run() {
  local api_base_url="$1"
  local validation_run_id="$2"

  curl -sS "$(api_url "$api_base_url" "/runs/validations/$validation_run_id")"
}

fetch_validation_report() {
  local api_base_url="$1"
  local validation_run_id="$2"

  curl -sS "$(api_url "$api_base_url" "/artifacts/runs/validations/$validation_run_id/report")"
}

extract_pipeline_run_id() {
  local json="$1"

  require_json_field \
    "$json" \
    '.pipelineRun.pipelineRunId // .pipelineRunId // .pipeline_run_id // .id' \
    'pipeline run id'
}

pipeline_status_expr() {
  echo '.pipelineRun.status // .status // empty'
}

pipeline_result_expr() {
  echo '.pipelineRun.result // .result'
}

poll_pipeline_terminal() {
  local api_base_url="$1"
  local pipeline_run_id="$2"
  local max_attempts="${3:-30}"
  local sleep_seconds="${4:-2}"

  local pipeline_json
  local status

  for i in $(seq 1 "$max_attempts"); do
    pipeline_json="$(fetch_pipeline_run "$api_base_url" "$pipeline_run_id")"
    status="$(echo "$pipeline_json" | jq -r "$(pipeline_status_expr)")"

    echo "[$i] pipeline status=$status" >&2

    if [ "$status" = "succeeded" ] || [ "$status" = "failed" ] || [ "$status" = "canceled" ]; then
      echo "$pipeline_json"
      return 0
    fi

    sleep "$sleep_seconds"
  done

  echo "❌ Pipeline did not reach terminal state: $pipeline_run_id" >&2
  exit 1
}

# -------------------------------------------------------------------
# Pipeline result helpers
# - Prefer structured result:
#   result.summary / result.lineage / result.outputs
# - Fallback to previous step result:
#   result.steps[].result.job_result
# -------------------------------------------------------------------

pipeline_summary_field_expr() {
  local field_name="$1"

  echo "(.pipelineRun.result.summary.$field_name // .result.summary.$field_name)"
}

pipeline_lineage_field_expr() {
  local field_name="$1"

  echo "(.pipelineRun.result.lineage.$field_name // .result.lineage.$field_name)"
}

pipeline_output_field_expr() {
  local output_name="$1"
  local field_name="$2"

  echo "(.pipelineRun.result.outputs.$output_name.$field_name // .result.outputs.$output_name.$field_name)"
}

pipeline_step_job_result_field_expr() {
  local step_name="$1"
  local field_name="$2"

  echo "(.pipelineRun.result.steps // .result.steps // [] | map(select(.step_name == \"$step_name\" or .stepName == \"$step_name\")) | .[0].result.job_result.$field_name)"
}

pipeline_step_compact_result_field_expr() {
  local step_name="$1"
  local field_name="$2"

  echo "(.pipelineRun.result.steps // .result.steps // [] | map(select(.step_name == \"$step_name\" or .stepName == \"$step_name\")) | .[0].result.$field_name)"
}

pipeline_validation_field_expr() {
  local field_name="$1"

  case "$field_name" in
    validation_status|status)
      echo "(
        .pipelineRun.result.summary.validation_status //
        .pipelineRun.result.summary.validationStatus //
        .result.summary.validation_status //
        .result.summary.validationStatus //
        .pipelineRun.result.outputs.validation.status //
        .result.outputs.validation.status
      )"
      ;;
    validation_run_id|run_id)
      echo "(
        .pipelineRun.result.lineage.validation_run_id //
        .pipelineRun.result.lineage.validationRunId //
        .result.lineage.validation_run_id //
        .result.lineage.validationRunId //
        .pipelineRun.result.outputs.validation.run_id //
        .result.outputs.validation.run_id
      )"
      ;;
    validation_report_uri|report_uri)
      echo "(
        .pipelineRun.result.lineage.validation_report_uri //
        .pipelineRun.result.lineage.validationReportUri //
        .result.lineage.validation_report_uri //
        .result.lineage.validationReportUri //
        .pipelineRun.result.outputs.validation.report_uri //
        .result.outputs.validation.report_uri
      )"
      ;;
    should_block_pipeline)
      echo "(
        .pipelineRun.result.summary.should_block_pipeline //
        .pipelineRun.result.summary.shouldBlockPipeline //
        .result.summary.should_block_pipeline //
        .result.summary.shouldBlockPipeline //
        .pipelineRun.result.outputs.validation.should_block_pipeline //
        .result.outputs.validation.should_block_pipeline //
        (.pipelineRun.result.steps // .result.steps // [] | map(select(.step_name == \"validate\" or .stepName == \"validate\")) | .[0].result.should_block_pipeline) //
        (.pipelineRun.result.steps // .result.steps // [] | map(select(.step_name == \"validate\" or .stepName == \"validate\")) | .[0].result.job_result.should_block_pipeline) //
        (.pipelineRun.result.steps // .result.steps // [] | map(select(.step_name == \"validate\" or .stepName == \"validate\")) | .[0].result.job_result.result_summary.should_block_pipeline)
      )"
      ;;
    *)
      echo "(
        .pipelineRun.result.outputs.validation.$field_name //
        .result.outputs.validation.$field_name //
        .pipelineRun.result.summary.$field_name //
        .result.summary.$field_name
      )"
      ;;
  esac
}

pipeline_inference_field_expr() {
  local field_name="$1"

  case "$field_name" in
    inference_run_id|run_id)
      echo "(
        .pipelineRun.result.lineage.inference_run_id //
        .pipelineRun.result.lineage.inferenceRunId //
        .result.lineage.inference_run_id //
        .result.lineage.inferenceRunId //
        .pipelineRun.result.outputs.inference.run_id //
        .result.outputs.inference.run_id //
        .pipelineRun.result.inference_run_id //
        .result.inference_run_id
      )"
      ;;
    prediction_manifest_uri)
      echo "(
        .pipelineRun.result.lineage.prediction_manifest_uri //
        .pipelineRun.result.lineage.predictionManifestUri //
        .result.lineage.prediction_manifest_uri //
        .result.lineage.preditionManifestUri //
        .pipelineRun.result.outputs.inference.prediction_manifest_uri //
        .result.outputs.inference.prediction_manifest_uri //
        .pipelineRun.result.prediction_manifest_uri //
        .result.prediction_manifest_uri
      )"
      ;;
    *)
      echo "(.pipelineRun.result.outputs.inference.$field_name // .result.outputs.inference.$field_name)"
      ;;
  esac
}

pipeline_evaluation_field_expr() {
  local field_name="$1"

  case "$field_name" in
    evaluation_run_id|run_id)
      echo "(
        .pipelineRun.result.lineage.evaluation_run_id //
        .pipelineRun.result.lineage.evaluationRunId //
        .result.lineage.evaluation_run_id //
        .result.lineage.evaluationRunId //
        .pipelineRun.result.outputs.evaluation.run_id //
        .result.outputs.evaluation.run_id //
        .pipelineRun.result.evaluation_run_id //
        .result.evaluation_run_id
      )"
      ;;
    evaluation_manifest_uri)
      echo "(
        .pipelineRun.result.lineage.evaluation_manifest_uri //
        .pipelineRun.result.lineage.evaluationManifestUri //
        .result.lineage.evaluation_manifest_uri //
        .result.lineage.evaluationManifestUri //
        .pipelineRun.result.outputs.evaluation.evaluation_manifest_uri //
        .result.outputs.evaluation.evaluation_manifest_uri //
        .pipelineRun.result.evaluation_manifest_uri //
        .result.evaluation_manifest_uri
      )"
      ;;
    *)
      echo "(.pipelineRun.result.outputs.evaluation.$field_name // .result.outputs.evaluation.$field_name)"
      ;;
  esac
}

assert_pipeline_succeeded() {
  local pipeline_json="$1"
  local message="${2:-pipeline should succeed}"

  assert_json_equals "$pipeline_json" "$(pipeline_status_expr)" 'succeeded' "$message"
}

assert_pipeline_failed() {
  local pipeline_json="$1"
  local message="${2:-pipeline should fail}"

  assert_json_equals "$pipeline_json" "$(pipeline_status_expr)" 'failed' "$message"
}

assert_validation_ready_from_pipeline() {
  local pipeline_json="$1"

  assert_json_equals "$pipeline_json" "$(pipeline_validation_field_expr "status")" 'ready' \
    'pipeline validation status should be ready'

  assert_json_not_empty "$pipeline_json" "$(pipeline_validation_field_expr "validation_run_id")" \
    'pipeline validation_run_id'

  assert_json_not_empty "$pipeline_json" "$(pipeline_validation_field_expr "validation_report_uri")" \
    'pipeline validation_report_uri'
}

assert_validation_run_ready() {
  local validation_run_json="$1"

  assert_json_equals "$validation_run_json" '(.run.status // .status)' 'succeeded' \
    'validation run status should be succeeded'

  assert_json_equals "$validation_run_json" '(.run.validationStatus // .run.validation_status // .validationStatus // .validation_status)' 'ready' \
    'validation run validation_status should be ready'

  assert_json_equals "$validation_run_json" '(.run.shouldBlockPipeline | tostring)' 'false' \
    'validation run should_block_pipeline should be false'

  assert_json_not_empty "$validation_run_json" '(.run.validationReportUri // .run.validation_report_uri // .validationReportUri // .validation_report_uri)' \
    'validation run validation_report_uri'
}

assert_validation_run_failed_but_executed() {
  local validation_run_json="$1"

  assert_json_equals "$validation_run_json" '(.run.status // .status)' 'succeeded' \
    'validation run execution status should be succeeded'

  assert_json_equals "$validation_run_json" '(.run.validationStatus // .run.validation_status // .validationStatus // .validation_status)' 'failed' \
    'validation result status should be failed'

  assert_json_equals "$validation_run_json" '(.run.shouldBlockPipeline | tostring)' 'true' \
    'validation run should_block_pipeline should be true'

  assert_json_not_empty "$validation_run_json" '(.run.validationReportUri // .run.validation_report_uri // .validationReportUri // .validation_report_uri)' \
    'validation run validation_report_uri'
}
