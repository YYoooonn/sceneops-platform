#!/usr/bin/env bash
# e2e_perception.sh (merges
# e2e_detection_evaluation.sh and e2e_detection_evaluation_groundingdino.sh)
#
# The canonical perception-domain E2E, composing:
#   scenario curation -> scenario selection -> prediction -> evaluation
#   -> persisted metrics / lineage
#
# Previously these were two separately-run scripts sharing ~85% identical
# assertion code (copy-pasted, not shared) and requiring the caller to run
# `make e2e-scenario-curation` by hand and thread its printed
# SCENARIO_CURATION_PIPELINE_RUN_ID/SCENARIO_SET_ID (or its undocumented
# PIPELINE_RUN_ID alias) into a second command. Scenario curation is now
# always run internally and its scenario_set_id handed off automatically --
# no PIPELINE_RUN_ID/SCENARIO_SET_ID variables to manage.
#
# The shared inference-run/evaluation-run/leaderboard assertion logic now
# lives once in lib.sh (assert_inference_run/assert_evaluation_run/
# assert_leaderboard_entry) -- both BACKEND branches call the same
# functions; only backend-specific setup (real inference-server readiness
# polling, lifting-metric counters) stays here.
#
# Usage:
#   bash scripts/e2e/e2e_perception.sh
#   bash scripts/e2e/e2e_perception.sh BACKEND=grounding_dino
#
# Prereq: e2e-scene must have already run for DATASET_ID/DATASET_VERSION
# (registered + profiled scenes with ground truth) -- this script checks
# for a Scene manifest up front and fails clearly if it's missing, rather
# than failing deep inside the pipeline.
# BACKEND=grounding_dino additionally requires a real inference server
# already running (`make inference-local-up` for CPU, `make
# inference-gpu-up` for GPU).
#
# Env overrides (DATASET_ID/DATASET_VERSION default from the shared "core"
# E2E fixture, see scripts/e2e/lib.sh's resolve_e2e_fixture):
#   API_BASE_URL            (default: http://localhost:8000)
#   BACKEND                 mock | grounding_dino (default: mock)
#   DATASET_ID / DATASET_VERSION
#   MODEL_ID / MODEL_VERSION      derived from BACKEND if unset
#                                 (dummy-detector/v1 for mock,
#                                 grounding-dino/tiny for grounding_dino)
#   MAX_SCENES              scenes to select for inference (default: 1)
#   MAX_SAMPLES             samples per scene (default: 5)
#   INFERENCE_SERVER_URL    host-side URL for health checks (grounding_dino only,
#                           default: http://localhost:8001)
#   INFERENCE_ENDPOINT_URL  container-internal URL passed to the worker
#                           (grounding_dino only, default: http://sceneops-inference:8001)
#   POLL_TIMEOUT            pipeline poll attempts at 5s each (default: 60)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"
unavailable_until e2e-perception 10 \
  "recording-derived Scenes carry no ground truth or keyframe groups, so detection has no samples or labels until the label ingress (Q1) and the derived synchronized-sample view exist"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
BACKEND="${BACKEND:-mock}"
resolve_e2e_fixture core
MAX_SCENES="${MAX_SCENES:-1}"
MAX_SAMPLES="${MAX_SAMPLES:-5}"
POLL_TIMEOUT="${POLL_TIMEOUT:-60}"

case "$BACKEND" in
  mock)
    MODEL_ID="${MODEL_ID:-dummy-detector}"
    MODEL_VERSION="${MODEL_VERSION:-v1}"
    ;;
  grounding_dino)
    MODEL_ID="${MODEL_ID:-grounding-dino}"
    MODEL_VERSION="${MODEL_VERSION:-tiny}"
    INFERENCE_SERVER_URL="${INFERENCE_SERVER_URL:-http://localhost:8001}"
    INFERENCE_ENDPOINT_URL="${INFERENCE_ENDPOINT_URL:-http://sceneops-inference:8001}"
    ;;
  *)
    echo "❌ Unknown BACKEND='$BACKEND' (expected mock|grounding_dino)" >&2
    exit 1
    ;;
esac

echo "=== e2e-perception (BACKEND=$BACKEND) ==="
echo "  API_BASE_URL=$API_BASE_URL"
echo "  DATASET_ID=$DATASET_ID  DATASET_VERSION=$DATASET_VERSION"
echo "  MODEL_ID=$MODEL_ID  MODEL_VERSION=$MODEL_VERSION"
echo "  MAX_SCENES=$MAX_SCENES  MAX_SAMPLES=$MAX_SAMPLES"
echo ""

# ── 0. Explicit prerequisite: Scene manifest must already exist ─────────────

echo "--- 0. Verify Scene ingestion has already run ---"
DATASET_JSON="$(curl -sS "$(api_url "$API_BASE_URL" "/datasets/$DATASET_ID/versions/$DATASET_VERSION")")"
SCENE_MANIFEST_URI="$(echo "$DATASET_JSON" | jq -r '.version.scene.manifestUri // empty')"
echo "  $DATASET_ID/$DATASET_VERSION: scene.manifestUri=$SCENE_MANIFEST_URI"
if [ -z "$SCENE_MANIFEST_URI" ]; then
  echo "❌ Dataset version has no Scene manifest yet — run \`make e2e-scene\` first." >&2
  exit 1
fi
echo "  OK"
echo ""

# ── 1. Backend-specific readiness ────────────────────────────────────────────

if [ "$BACKEND" = "grounding_dino" ]; then
  echo "--- 1. Inference server liveness + readiness ---"
  require_service "Inference server" "${INFERENCE_SERVER_URL}/healthz" 1 1 \
    "Run: make inference-local-up  (CPU)  or  make inference-gpu-up  (GPU)"
  READYZ_JSON="$(poll_inference_ready "$INFERENCE_SERVER_URL" 90 2)"
  WARMUP_SUCCEEDED="$(echo "$READYZ_JSON" | jq -r '.warmup_succeeded // null')"
  WARMUP_ENABLED="$(echo "$READYZ_JSON" | jq -r '.warmup_enabled // false')"
  DEVICE="$(echo "$READYZ_JSON" | jq -r '.device // "unknown"')"
  echo "  device=$DEVICE  warmup_enabled=$WARMUP_ENABLED  warmup_succeeded=$WARMUP_SUCCEEDED"
  if [ "$WARMUP_ENABLED" = "true" ] && [ "$WARMUP_SUCCEEDED" = "false" ]; then
    echo "❌ Inference server warmup failed: $(echo "$READYZ_JSON" | jq -r '.warmup_error // unknown')" >&2
    exit 1
  fi
  echo "  OK"
  echo ""
fi

# ── 2. Upsert model ───────────────────────────────────────────────────────────

echo "--- 2. Upsert model ($MODEL_ID/$MODEL_VERSION backend=$BACKEND) ---"
if [ "$BACKEND" = "grounding_dino" ]; then
  upsert_model_with_backend "$API_BASE_URL" "$MODEL_ID" "$MODEL_VERSION" "$INFERENCE_ENDPOINT_URL" "GroundingDINO" "grounding_dino" \
    | jq '.version | {id, modelId, version, backend}' 2>/dev/null || true
else
  upsert_model "$API_BASE_URL" "$MODEL_ID" "$MODEL_VERSION" "dummy detector" \
    | jq '.version | {id, modelId}' 2>/dev/null || true
fi
echo ""

# ── 3. Scenario curation (composed in -- no manual hand-off) ────────────────

echo "--- 3. Scenario curation ---"
SCENARIO_PAYLOAD="$(cat <<JSON
{
  "type": "scenario_curation",
  "dataset_id": "$DATASET_ID",
  "dataset_version": "$DATASET_VERSION",
  "force": true,
  "params": {
    "mine_scenarios": {
      "candidate_profile": "detection_ready",
      "min_annotation_count": 0,
      "required_channels": ["CAM_FRONT", "LIDAR_TOP"],
      "max_candidates": 20,
      "sort_by": "annotation_count",
      "order": "desc"
    }
  }
}
JSON
)"
SCENARIO_CREATE_JSON="$(create_pipeline_run "$API_BASE_URL" "$SCENARIO_PAYLOAD")"
SCENARIO_PIPELINE_RUN_ID="$(extract_pipeline_run_id "$SCENARIO_CREATE_JSON")"
dispatch_pipeline_run "$API_BASE_URL" "$SCENARIO_PIPELINE_RUN_ID" >/dev/null
SCENARIO_PIPELINE_JSON="$(poll_pipeline_terminal "$API_BASE_URL" "$SCENARIO_PIPELINE_RUN_ID" "$POLL_TIMEOUT" 5)"
assert_pipeline_succeeded "$SCENARIO_PIPELINE_JSON" "scenario_curation pipeline should succeed" "$API_BASE_URL" "$SCENARIO_PIPELINE_RUN_ID"

SCENARIO_SET_ID="$(echo "$SCENARIO_PIPELINE_JSON" | jq -r '.pipelineRun.result.outputs.scenario_set_id // empty')"
CANDIDATE_COUNT="$(echo "$SCENARIO_PIPELINE_JSON" | jq -r '.pipelineRun.result.metrics.candidate_count // 0')"
[ -n "$SCENARIO_SET_ID" ] || { echo "❌ scenario_curation produced no scenario_set_id" >&2; exit 1; }
echo "  scenario_set_id=$SCENARIO_SET_ID  candidate_count=$CANDIDATE_COUNT"
if [ "${CANDIDATE_COUNT:-0}" -lt 1 ]; then
  echo "❌ scenario_curation produced candidate_count=0 -- detection evaluation" >&2
  echo "   needs a non-empty ScenarioSet. Run \`make e2e-scene\` with a larger" >&2
  echo "   MAX_SCENES first (too few registered scenes is the usual cause)." >&2
  exit 1
fi
echo "  OK"
echo ""

# ── 4. predict_detection -> evaluate_detection ───────────────────────────────

echo "--- 4. Create detection_evaluation pipeline run ---"
DETECTION_PAYLOAD="$(cat <<JSON
{
  "type": "detection_evaluation",
  "dataset_id": "$DATASET_ID",
  "dataset_version": "$DATASET_VERSION",
  "model_id": "$MODEL_ID",
  "model_version": "$MODEL_VERSION",
  "force": true,
  "params": {
    "predict_detection": {
      "scenario_set_id": "$SCENARIO_SET_ID",
      "model_id": "$MODEL_ID",
      "model_version": "$MODEL_VERSION",
      "inference_backend": "$BACKEND",
      "scene_selection": {
        "mode": "ground_truth_only",
        "max_scenes": $MAX_SCENES,
        "max_samples": $MAX_SAMPLES
      },
      "camera_channel": "CAM_FRONT"
    },
    "evaluate_detection": {
      "scenario_set_id": "$SCENARIO_SET_ID",
      "evaluator_id": "center-distance",
      "match_distance_m": 2.0
    }
  }
}
JSON
)"
CREATE_RESP="$(create_pipeline_run "$API_BASE_URL" "$DETECTION_PAYLOAD")"
PIPELINE_RUN_ID="$(extract_pipeline_run_id "$CREATE_RESP")"
echo "  pipeline_run_id=$PIPELINE_RUN_ID"
echo ""

echo "--- 5. Dispatch ---"
EXEC_RESP="$(dispatch_pipeline_run "$API_BASE_URL" "$PIPELINE_RUN_ID")"
EXEC_STATUS="$(echo "$EXEC_RESP" | jq -r '.execution.status // "error"')"
echo "  execution status=$EXEC_STATUS"
if [ "$EXEC_STATUS" = "error" ]; then
  echo "$EXEC_RESP" | jq . >&2
  exit 1
fi
echo ""

POLL_TIMEOUT_SUM=$((POLL_TIMEOUT * MAX_SCENES))
echo "--- 6. Polling (up to $((POLL_TIMEOUT_SUM * 5))s) ---"
PIPELINE_JSON="$(poll_pipeline_terminal "$API_BASE_URL" "$PIPELINE_RUN_ID" "$POLL_TIMEOUT_SUM" 5)"
echo ""

echo "--- 7. Assert pipeline ---"
assert_pipeline_succeeded "$PIPELINE_JSON" "detection_evaluation (BACKEND=$BACKEND) pipeline should succeed" "$API_BASE_URL" "$PIPELINE_RUN_ID"
echo "  OK"
echo ""

echo "--- 8. Assert task statuses ---"
TASKS_JSON="$(fetch_pipeline_tasks "$API_BASE_URL" "$PIPELINE_RUN_ID")"
FAILED_TASKS="$(echo "$TASKS_JSON" | jq -r '[.tasks[] | select(.status != "succeeded")] | map("\(.pipelineTaskId)=\(.status)") | join(", ")')"
echo "$TASKS_JSON" | jq -r '.tasks[] | "  \(.pipelineTaskId): \(.status)"'
[ -z "$FAILED_TASKS" ] || { echo "❌ Non-succeeded tasks: $FAILED_TASKS" >&2; exit 1; }
echo "  OK"
echo ""

echo "--- 9. Extract run IDs ---"
CONTEXT_JSON="$(fetch_pipeline_run "$API_BASE_URL" "$PIPELINE_RUN_ID")"
INFERENCE_RUN_ID="$(echo "$CONTEXT_JSON" | jq -r '.pipelineRun.result.outputs.inference_run_id // empty')"
EVALUATION_RUN_ID="$(echo "$CONTEXT_JSON" | jq -r '.pipelineRun.result.outputs.evaluation_run_id // empty')"
echo "  inference_run_id=$INFERENCE_RUN_ID"
echo "  evaluation_run_id=$EVALUATION_RUN_ID"
[ -n "$INFERENCE_RUN_ID" ] || { echo "❌ inference_run_id missing from pipeline context" >&2; exit 1; }
[ -n "$EVALUATION_RUN_ID" ] || { echo "❌ evaluation_run_id missing from pipeline context" >&2; exit 1; }
echo "  OK"
echo ""

# ── 10. Shared assertions (lib.sh) ───────────────────────────────────────────

echo "--- 10. Assert inference run ---"
if [ "$BACKEND" = "grounding_dino" ]; then
  INFERENCE_JSON="$(assert_inference_run "$API_BASE_URL" "$INFERENCE_RUN_ID" "$MAX_SAMPLES")"
  INFER_PRED_COUNT="$(echo "$INFERENCE_JSON" | jq -r '.run.predictionCount // 0')"
  if [ "$INFER_PRED_COUNT" -eq 0 ]; then
    echo "  ⚠ predictionCount=0 (GroundingDINO found no detections — check thresholds/image quality)"
  fi
else
  INFERENCE_JSON="$(assert_inference_run "$API_BASE_URL" "$INFERENCE_RUN_ID" "")"
fi
echo "  OK"
echo ""

echo "--- 11. Assert evaluation run ---"
EVAL_JSON="$(assert_evaluation_run "$API_BASE_URL" "$EVALUATION_RUN_ID")"
EVAL_PRIMARY_NAME="$(echo "$EVAL_JSON" | jq -r '.run.primaryMetricName')"
EVAL_PRIMARY_VALUE="$(echo "$EVAL_JSON" | jq -r '.run.primaryMetricValue')"
echo "  OK"
echo ""

echo "--- 12. Assert leaderboard entry ---"
assert_leaderboard_entry "$API_BASE_URL" "$DATASET_ID" "$DATASET_VERSION" "$EVALUATION_RUN_ID" >/dev/null
echo "  OK"
echo ""

# ── 13. ScenarioSet lineage verification (both backends now use ScenarioSet) ─

echo "--- 13. ScenarioSet lineage verification ---"
INFER_SS_ID="$(echo "$INFERENCE_JSON" | jq -r '.run.metadata.scenario_set_id // empty')"
EVAL_SS_ID="$(echo "$EVAL_JSON" | jq -r '.run.metadata.scenario_set_id // empty')"
[ "$INFER_SS_ID" = "$SCENARIO_SET_ID" ] || { echo "❌ inference_run scenario_set_id mismatch: expected=$SCENARIO_SET_ID actual=$INFER_SS_ID" >&2; exit 1; }
[ "$EVAL_SS_ID" = "$SCENARIO_SET_ID" ] || { echo "❌ evaluation_run scenario_set_id mismatch: expected=$SCENARIO_SET_ID actual=$EVAL_SS_ID" >&2; exit 1; }
echo "  ✓ scenario_set_id=$SCENARIO_SET_ID confirmed on both inference_run and evaluation_run"
echo ""

echo "--- 14. Assert prediction/evaluation artifacts ---"
INFER_ARTIFACTS_JSON="$(fetch_inference_run_artifacts "$API_BASE_URL" "$INFERENCE_RUN_ID")"
assert_artifact_kind_present "$INFER_ARTIFACTS_JSON" "prediction_manifest" "inference run should register prediction_manifest artifact"
EVAL_ARTIFACTS_JSON="$(fetch_evaluation_run_artifacts "$API_BASE_URL" "$EVALUATION_RUN_ID")"
assert_artifact_kind_present "$EVAL_ARTIFACTS_JSON" "evaluation_manifest" "evaluation run should register evaluation_manifest artifact"
assert_artifact_kind_present "$EVAL_ARTIFACTS_JSON" "metrics" "evaluation run should register metrics artifact"
echo "  OK"
echo ""

# ── Summary ───────────────────────────────────────────────────────────────────

echo "=== PASSED: e2e-perception (BACKEND=$BACKEND) ==="
echo "  scenario_set_id      = $SCENARIO_SET_ID"
echo "  pipeline_run_id      = $PIPELINE_RUN_ID"
echo "  inference_run_id     = $INFERENCE_RUN_ID"
echo "  evaluation_run_id    = $EVALUATION_RUN_ID"
echo "  primary_metric       = $EVAL_PRIMARY_NAME=$EVAL_PRIMARY_VALUE"
