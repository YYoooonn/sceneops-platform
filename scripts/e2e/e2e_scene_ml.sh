#!/usr/bin/env bash
# e2e_scene_ml.sh — the Scene ML journey (ADR-007 §33, §34): canonical Scenes
# -> labels -> sample views -> ScenarioSet -> prediction -> evaluation, every
# revision pinned to the revisions it consumed.
#
#   canonical-bootstrap                     RobotRun -> canonical Scenes (L1/L2)
#   reference render-labels                 the fixture's locked reference labels,
#                                           rendered for the RobotRun (no source dataset)
#   IMPORT_LABELS                           independent, pinned LabelSet revision
#   BUILD_SCENE_SAMPLE_VIEWS                policy-driven synchronized views
#   scene_ml_evaluation pipeline            build_scene_sample_views ->
#                                           mine_scenarios -> score_scenario_readiness
#                                           -> predict_detection -> evaluate_detection
#   lineage                                 every revision pins what it consumed;
#                                           a retried atomic job converges
#   the real lidar payload                  is a message of the locked recording and
#                                           decodes to that message's points
#
# The host needs Docker Compose, curl and jq, plus the API port. It never
# reads PostgreSQL or MinIO directly; label documents reach the worker the way
# any external input does, through the bind-mounted ./data/inputs area. The
# journey reads no source dataset: ground truth and the lidar reference both come
# from the prepared reference corpus.
#
# BACKEND=mock (default) needs nothing beyond `make local-up`: the mock backend
# perturbs the labels, so its metrics prove wiring and pinning, not model
# quality. BACKEND=grounding_dino (`make acceptance-grounding-dino`) is the
# opt-in model-backend acceptance of the same journey: it needs a running
# inference server (`make inference-local-up` / `inference-gpu-up`) and lifts
# boxes through the real lidar payload.
#
# Test-state class: REFERENCE_DERIVED (docs/development/test-matrix.md). The RobotRun is
# the golden reference contract's (BASELINE_ID, default ref-nuscenes-mini-full-10); no
# RobotRun is created, and nothing is written to the reference DatasetVersion. The Scenes,
# labels, views, ScenarioSet, predictions and evaluations of the journey live in a fixed,
# test-owned identity (DATASET_ID, default sceneops-test-scene-ml; the GroundingDINO
# acceptance uses sceneops-test-scene-ml-grounding-dino), and every derived record carries
# an id derived from it. A repeated run therefore reuses the DatasetVersion and converges on
# the same Scenes, LabelSet, views, ScenarioSet, InferenceRun and EvaluationRuns instead of
# adding new ones; a runtime reset (make local-reset) drops them.
#
# Prerequisites: `make local-up`, `make acquisition-image` and
# `make reference-data-bootstrap` (recordings and reference labels in data/reference).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"
source "$SCRIPT_DIR/lib.sh"

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
BACKEND="${BACKEND:-mock}"
SOURCE_UNIT="${SOURCE_UNIT:-scene-0061}"
# The source RobotRun is the golden reference contract's (BASELINE_ID defaults to it);
# everything the journey writes goes to a fixed DatasetVersion of its own. The backend is
# part of the identity: two detectors never share predictions.
case "$BACKEND" in
  mock) DEFAULT_DATASET_ID="sceneops-test-scene-ml" ;;
  grounding_dino) DEFAULT_DATASET_ID="sceneops-test-scene-ml-grounding-dino" ;;
  *) fail "Unknown BACKEND='$BACKEND' (expected mock|grounding_dino)" ;;
esac
export DATASET_ID="${DATASET_ID:-$DEFAULT_DATASET_ID}"
export FIXTURE="$SOURCE_UNIT"
source "$REPO_ROOT/scripts/canonical/baseline_lib.sh"
RUN_ID="$(baseline_run_id "$SOURCE_UNIT")"
MAX_SAMPLES="${MAX_SAMPLES:-}"
# Ids of the derived records, fixed by the identity above. Inference and evaluation runs
# are immutable per id, so a capped run (MAX_SAMPLES) is a different identity from a full one.
LABEL_SET_ID="labels-$DATASET_ID-$SOURCE_UNIT"
SCENARIO_SET_ID="scset-$DATASET_ID"
INFERENCE_RUN_ID="infer-$DATASET_ID${MAX_SAMPLES:+-max$MAX_SAMPLES}"
EVALUATION_RUN_ID="eval-$DATASET_ID${MAX_SAMPLES:+-max$MAX_SAMPLES}"
RECHECK_EVALUATION_RUN_ID="$EVALUATION_RUN_ID-recheck"

CAMERA="/camera/front/image/compressed"
LIDAR="/lidar/top/points"

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
    fail "Unknown BACKEND='$BACKEND' (expected mock|grounding_dino)"
    ;;
esac

# The runtime input area IMPORT_LABELS reads (SCENEOPS_WORKER_INPUT_SOURCE__ROOT_URI).
LABELS_DIR="$REPO_ROOT/data/inputs/labels"
LABELS_FILE="$LABELS_DIR/$LABEL_SET_ID.labels.json"
# The same file as the worker container sees it.
LABELS_URI="/data/inputs/labels/$LABEL_SET_ID.labels.json"

cleanup() {
  rm -f "$LABELS_FILE"
}
trap cleanup EXIT

# render_reference_labels <fixture> <run-id> <label-set-id>
# The fixture's locked reference label artifact, verified against the lock and
# rendered for the RobotRun into the runtime input area. The reference-labels
# service mounts no source dataset. Prints the tool's summary JSON.
render_reference_labels() {
  local output status=0
  mkdir -p "$LABELS_DIR"
  output="$(compose run --rm -T --no-deps --user "$(id -u):$(id -g)" -e HOME=/tmp \
    reference-labels reference render-labels --corpus "/config/reference/$REFERENCE_CORPUS" \
    --cache-root /reference --fixture "$1" --robot-run-id "$2" --label-set-id "$3" \
    --output "/inputs/labels/$3.labels.json" </dev/null)" || status=$?
  if [ "$status" -ne 0 ]; then
    echo "$output" | jq -r 'select(.status == "failed") | .problems[] | "    \(.)"' >&2 || echo "$output" >&2
    fail "no verified reference labels for $1; run \`make reference-data-bootstrap\` (UPDATE_LOCK=1 once to lock labels)"
  fi
  echo "$output"
}

scenes_json() {
  api_get "$API_BASE_URL" "/scenes?dataset_id=$DATASET_ID&dataset_version=$DATASET_VERSION&limit=500"
}

scene_revisions() {
  scenes_json | jq -cS '[.scenes[] | {sceneId, manifestArtifactId, manifestChecksum}]'
}

# The synchronization policy of this workflow, stated explicitly: camera
# frames are the sample instants; the nearest lidar observation within 25 ms
# and the ego pose within 5 ms are associated with each. Nothing is
# interpolated, and only observations on the same clock are compared.
view_policy() {
  jq -cn --arg cam "$CAMERA" --arg lidar "$LIDAR" '{
    anchor: {channel: $cam, stride: 1},
    members: [{channel: $lidar, association: "nearest", tolerance_ns: 25000000, required: true}],
    pose: {parent_frame_id: "map", child_frame_id: "base_link", association: "nearest",
           tolerance_ns: 5000000, required: true}
  }'
}

echo "=== [0/8] canonical-bootstrap: RobotRun -> canonical Scenes and Episodes ==="
require_api "$API_BASE_URL"
BASELINE="$("$REPO_ROOT/scripts/canonical/canonical_bootstrap.sh")"
echo "$BASELINE" | jq -c '{dataset_id, dataset_version, scene_count, episode_count}'
SCENE_COUNT="$(echo "$BASELINE" | jq -r '.scene_count')"
check "$SCENE_COUNT canonical Scene(s) are registered" [ "$SCENE_COUNT" -ge 1 ]
check "the canonical Scenes embed no annotation" \
  [ "$(scenes_json | jq '[.scenes[] | has("annotationCount")] | any')" = false ]
SCENE_REVISIONS="$(scene_revisions)"
echo ""

echo "=== [1/8] reference fixture -> label document for the RobotRun (no source dataset) ==="
LABELS_SUMMARY="$(render_reference_labels "$SOURCE_UNIT" "$RUN_ID" "$LABEL_SET_ID")"
echo "$LABELS_SUMMARY" | jq -c '{label_set_id, coverage_count, label_count, labels_sha256}'
SOURCE_LABELS="$(echo "$LABELS_SUMMARY" | jq -r '.label_count')"
SOURCE_COVERAGE="$(echo "$LABELS_SUMMARY" | jq -r '.coverage_count')"
[ -f "$LABELS_FILE" ] || fail "the rendered label document is not at $LABELS_FILE"
check "label document rendered for the RobotRun ($SOURCE_LABELS labels over $SOURCE_COVERAGE samples)" \
  [ "$SOURCE_LABELS" -gt 0 ]
echo ""

echo "=== [2/8] IMPORT_LABELS: an independent, pinned label set revision ==="
IMPORT="$(run_job "$API_BASE_URL" import_labels "$DATASET_ID" "$DATASET_VERSION" \
  "$(jq -cn --arg u "$LABELS_URI" '{document_uri: $u}')")"
assert_job_succeeded "$IMPORT" "import_labels should succeed"
LABEL_RESULT="$(job_result "$IMPORT")"
LABEL_SET="$(echo "$LABEL_RESULT" | jq -c '.label_set')"
echo "  $(echo "$LABEL_RESULT" | jq -c '{label_count, covered_anchor_count, provenance_kind, created}')"
check "every source label was imported with its coverage" \
  [ "$(echo "$LABEL_RESULT" | jq -r '[.label_count, .covered_anchor_count] | join(",")')" = "$SOURCE_LABELS,$SOURCE_COVERAGE" ]
check "provenance is external (nuscenes), anchored on the registered RobotRun" \
  [ "$(echo "$LABEL_RESULT" | jq -r '[.provenance_kind, (.robot_run_ids | join(","))] | join(",")')" = "external,$RUN_ID" ]
LABEL_ARTIFACT="$(api_get "$API_BASE_URL" "/artifacts/$(echo "$LABEL_SET" | jq -r '.manifest_artifact_id')")"
check "the revision is a checksum-pinned label_set_manifest owned by the label set" \
  [ "$(echo "$LABEL_ARTIFACT" | jq -r --arg id "$LABEL_SET_ID" --arg c "$(echo "$LABEL_SET" | jq -r '.manifest_checksum')" '[.artifact.kind, .artifact.ownerType, .artifact.ownerId, .artifact.checksum] | join(",") == ("label_set_manifest,label_set," + $id + "," + $c)')" = true ]
REIMPORT="$(run_job "$API_BASE_URL" import_labels "$DATASET_ID" "$DATASET_VERSION" \
  "$(jq -cn --arg u "$LABELS_URI" '{document_uri: $u}')")"
check "re-importing the same document converges on the same revision" \
  [ "$(job_result "$REIMPORT" | jq -c '[.label_set, .created]')" = "$(echo "$LABEL_SET" | jq -c '[., false]')" ]
check "the Scenes are unchanged by label import" [ "$(scene_revisions)" = "$SCENE_REVISIONS" ]
echo ""

echo "=== [3/8] BUILD_SCENE_SAMPLE_VIEWS (atomic job): synchronization lives in the derived view ==="
VIEW_PARAMS="$(jq -cn --argjson policy "$(view_policy)" --argjson ls "$LABEL_SET" \
  '{policy: $policy, label_sets: [$ls]}')"
VIEWS="$(run_job "$API_BASE_URL" build_scene_sample_views "$DATASET_ID" "$DATASET_VERSION" "$VIEW_PARAMS")"
assert_job_succeeded "$VIEWS" "build_scene_sample_views should succeed"
VIEWS_RESULT="$(job_result "$VIEWS")"
echo "  $(echo "$VIEWS_RESULT" | jq -c '{scene_count, sample_count, dropped_anchor_count, created_count, reused_count, skipped}')"
SAMPLE_VIEWS="$(echo "$VIEWS_RESULT" | jq -c '.views')"
check "every Scene with camera frames has a view" \
  [ "$(echo "$VIEWS_RESULT" | jq -r '.scene_count + (.skipped | length)')" = "$SCENE_COUNT" ]
check "samples exist, and anchors without a lidar sweep in tolerance are reported, not hidden" \
  [ "$(echo "$VIEWS_RESULT" | jq -r '.sample_count > 0 and .dropped_anchor_count >= 0')" = true ]
VIEW_ARTIFACT="$(api_get "$API_BASE_URL" "/artifacts/$(echo "$SAMPLE_VIEWS" | jq -r '.[0].manifest_artifact_id')")"
check "a view is a checksum-pinned scene_sample_view_manifest owned by its Scene" \
  [ "$(echo "$VIEW_ARTIFACT" | jq -r --arg s "$(echo "$SAMPLE_VIEWS" | jq -r '.[0].scene_id')" '[.artifact.kind, .artifact.ownerType, .artifact.ownerId] | join(",") == ("scene_sample_view_manifest,scene," + $s)')" = true ]
echo ""

echo "=== [4/8] scene_ml_evaluation ($BACKEND): views -> ScenarioSet -> prediction -> evaluation ==="
if [ "$BACKEND" = "mock" ]; then
  upsert_model "$API_BASE_URL" "$MODEL_ID" "$MODEL_VERSION" "Scene ML E2E detector"
else
  poll_inference_ready "$INFERENCE_SERVER_URL" 60 5 >/dev/null
  upsert_model "$API_BASE_URL" "$MODEL_ID" "$MODEL_VERSION" "Scene ML E2E detector" \
    grounding_dino "$INFERENCE_ENDPOINT_URL"
fi
PIPELINE_PARAMS="$(jq -cn --argjson views "$VIEW_PARAMS" --argjson ls "$LABEL_SET" --arg lsid "$LABEL_SET_ID" \
  --arg backend "$BACKEND" --arg cam "$CAMERA" --arg lidar "$LIDAR" --arg max "$MAX_SAMPLES" \
  --arg scset "$SCENARIO_SET_ID" --arg infer "$INFERENCE_RUN_ID" --arg eval "$EVALUATION_RUN_ID" '{
  build_scene_sample_views: $views,
  mine_scenarios: {label_set_id: $lsid, require_labels: true, required_channels: [$cam, $lidar], max_candidates: 50,
    output_scenario_set_id: $scset},
  score_scenario_readiness: {},
  predict_detection: ({inference_backend: $backend, camera_channel: $cam, inference_run_id: $infer}
    + (if $backend == "grounding_dino" then {lidar_channel: $lidar} else {} end)
    + (if $max == "" then {} else {max_samples: ($max | tonumber)} end)),
  evaluate_detection: {label_set: $ls, match_distance_m: 2.0, evaluation_run_id: $eval}}')"
# Not forced: the scenario-mining and readiness stages record their runs under the id of the
# Job that executed them, so a forced re-execution would append a new pair of reports on
# every run. The identical request returns the PipelineRun that already holds this result
# (a failed or interrupted one is redispatched); the stages' retry behaviour is proven by the
# atomic re-runs below and in tests/infrastructure.
PIPELINE="$(run_pipeline "$API_BASE_URL" scene_ml_evaluation "$DATASET_ID" "$DATASET_VERSION" "$PIPELINE_PARAMS" \
  "$(jq -cn --arg m "$MODEL_ID" --arg v "$MODEL_VERSION" '{model_id: $m, model_version: $v, force: false}')")"
assert_pipeline_succeeded "$(fetch_pipeline_run "$API_BASE_URL" "$PIPELINE")" \
  "scene_ml_evaluation should succeed" "$API_BASE_URL" "$PIPELINE"
fetch_pipeline_tasks "$API_BASE_URL" "$PIPELINE" | jq -r '.tasks[] | "  \(.pipelineTaskId): \(.status)"'
check "every stage of the pipeline succeeded" \
  [ "$(fetch_pipeline_tasks "$API_BASE_URL" "$PIPELINE" | jq '[.tasks[] | select(.status != "succeeded")] | length')" = 0 ]

PIPELINE_VIEWS="$(task_json "$API_BASE_URL" "$PIPELINE" build_scene_sample_views)"
check "the pipeline's view stage reproduced exactly the views of the atomic job (all reused)" \
  [ "$(echo "$PIPELINE_VIEWS" | jq -c '[.result.refs.views, .result.summary.created_count]')" = "$(echo "$SAMPLE_VIEWS" | jq -c '[., 0]')" ]
MINE="$(task_json "$API_BASE_URL" "$PIPELINE" mine_scenarios)"
check "the ScenarioSet carries its fixed id" \
  [ "$(echo "$MINE" | jq -r '.result.refs.scenario_set_id')" = "$SCENARIO_SET_ID" ]
SCENARIO_SET_CHECKSUM="$(echo "$MINE" | jq -r '.result.refs.scenario_set_checksum')"
SELECTED="$(echo "$MINE" | jq -r '.result.summary.selected_count_summary // .result.summary.selected_count // 0')"
echo "  scenario_set_id=$SCENARIO_SET_ID selected=$SELECTED"
check "labelled Scenes were selected" [ "$SELECTED" -gt 0 ]
check "the ScenarioSet record pins exactly the mined revision" \
  [ "$(api_get "$API_BASE_URL" "/scenarios/$SCENARIO_SET_ID" | jq -r '.scenarioSet.manifestChecksum')" = "$SCENARIO_SET_CHECKSUM" ]

PREDICT="$(task_json "$API_BASE_URL" "$PIPELINE" predict_detection)"
check "the inference run carries its fixed id" \
  [ "$(echo "$PREDICT" | jq -r '.result.refs.inference_run_id')" = "$INFERENCE_RUN_ID" ]
PREDICTION_CHECKSUM="$(echo "$PREDICT" | jq -r '.result.refs.prediction_manifest_checksum')"
check "the evaluation run carries its fixed id" \
  [ "$(task_json "$API_BASE_URL" "$PIPELINE" evaluate_detection | jq -r '.result.refs.evaluation_run_id')" = "$EVALUATION_RUN_ID" ]
INFERENCE="$(api_get "$API_BASE_URL" "/inference/runs/$INFERENCE_RUN_ID")"
EVALUATION="$(api_get "$API_BASE_URL" "/evaluations/runs/$EVALUATION_RUN_ID")"
echo "  inference sampleCount=$(echo "$INFERENCE" | jq -r '.run.sampleCount') predictionCount=$(echo "$INFERENCE" | jq -r '.run.predictionCount')"
echo "  evaluation $(echo "$EVALUATION" | jq -c '.run.metrics | {tp, fp, fn, precision, recall}')"
check "the inference run pins its prediction manifest revision" \
  [ "$(echo "$INFERENCE" | jq -r '.run.predictionManifestChecksum')" = "$PREDICTION_CHECKSUM" ]
check "the inference run recorded the ScenarioSet and the views it ran" \
  [ "$(echo "$INFERENCE" | jq -r --arg c "$SCENARIO_SET_CHECKSUM" '.run.metadata.inputs.scenario_set.manifest_checksum == $c and (.run.metadata.inputs.sample_views | length) > 0')" = true ]
check "the evaluation scored the pinned prediction against the pinned label revision" \
  [ "$(echo "$EVALUATION" | jq -r --arg p "$PREDICTION_CHECKSUM" --arg l "$(echo "$LABEL_SET" | jq -r '.manifest_checksum')" '.run.summary.inputs.prediction.manifest_checksum == $p and .run.summary.inputs.label_set.manifest_checksum == $l')" = true ]
check "ground truth is the imported labels (evaluation unit: label)" \
  [ "$(echo "$EVALUATION" | jq -r '[.run.evaluationUnit, (.run.groundTruthCount > 0)] | join(",")')" = "label,true" ]
INFERENCE_ARTIFACTS="$(api_get "$API_BASE_URL" "/inference/runs/$INFERENCE_RUN_ID/artifacts")"
assert_artifact_kind_present "$INFERENCE_ARTIFACTS" prediction_manifest "inference run should register its prediction manifest"
check "the prediction manifest ArtifactRecord id is derived from run and checksum, not random" \
  [ "$(echo "$INFERENCE_ARTIFACTS" | jq -r '[.artifacts[] | select(.kind == "prediction_manifest")][0].artifactId' | grep -c '^predmanifest-')" = 1 ]
echo ""

echo "=== [5/8] atomic PREDICT: the same run id and inputs converge on the same prediction revision ==="
RERUN="$(run_job "$API_BASE_URL" predict_detection "$DATASET_ID" "$DATASET_VERSION" \
  "$(jq -cn --arg set "$SCENARIO_SET_ID" --arg backend "$BACKEND" --arg cam "$CAMERA" --arg lidar "$LIDAR" \
    --arg max "$MAX_SAMPLES" --arg id "$INFERENCE_RUN_ID" --arg m "$MODEL_ID" --arg v "$MODEL_VERSION" '{
    inference_backend: $backend, scenario_set_id: $set, camera_channel: $cam, model_id: $m,
    model_version: $v, inference_run_id: $id}
    + (if $backend == "grounding_dino" then {lidar_channel: $lidar} else {} end)
    + (if $max == "" then {} else {max_samples: ($max | tonumber)} end)')")"
assert_job_succeeded "$RERUN" "re-running predict_detection under the same run id should succeed"
check "same run id, same inputs: the same prediction revision" \
  [ "$(job_result "$RERUN" | jq -r '.prediction_manifest_checksum')" = "$PREDICTION_CHECKSUM" ]
echo ""

echo "=== [6/8] atomic EVALUATE: the same pinned inputs reproduce the same metrics ==="
# A second evaluation of the same pinned inputs under its own fixed run id. evaluate_detection
# registers ArtifactRecords under fresh ids on every execution, so re-executing an existing
# evaluation run would add duplicate records: the evaluation runs once, and a repeated journey
# reads the run it recorded.
if RECHECK_RUN="$(api_get "$API_BASE_URL" "/evaluations/runs/$RECHECK_EVALUATION_RUN_ID" 2>/dev/null)" \
  && [ "$(echo "$RECHECK_RUN" | jq -r '.run.status')" = succeeded ]; then
  echo "  evaluation run $RECHECK_EVALUATION_RUN_ID exists: read, not re-executed"
  RECHECK_METRIC="$(echo "$RECHECK_RUN" | jq -c '[.run.primaryMetricName, .run.primaryMetricValue]')"
else
  REEVAL="$(run_job "$API_BASE_URL" evaluate_detection "$DATASET_ID" "$DATASET_VERSION" \
    "$(jq -cn --arg id "$INFERENCE_RUN_ID" --arg c "$PREDICTION_CHECKSUM" --argjson ls "$LABEL_SET" \
      --arg eval "$RECHECK_EVALUATION_RUN_ID" '{
      inference_run_id: $id, prediction_manifest_checksum: $c, label_set: $ls, match_distance_m: 2.0,
      evaluation_run_id: $eval}')")"
  assert_job_succeeded "$REEVAL" "re-evaluating the pinned prediction should succeed"
  echo "  $(job_result "$REEVAL" | jq -c '{primary_metric_name, primary_metric_value, ground_truth_count}')"
  RECHECK_METRIC="$(job_result "$REEVAL" | jq -c '[.primary_metric_name, .primary_metric_value]')"
fi
check "the same predictions and labels give the same primary metric" \
  [ "$RECHECK_METRIC" = "$(echo "$EVALUATION" | jq -c '[.run.primaryMetricName, .run.primaryMetricValue]')" ]
echo ""

echo "=== [7/8] the real lidar payload is the locked recording's PointCloud2 message ==="
LIDAR_PAYLOAD="$(api_get "$API_BASE_URL" "/artifacts?kind=observation_payload&owner_type=robot_run&owner_id=$RUN_ID&limit=500" \
  | jq -c '[.artifacts[] | select(.mediaType == "application/x.ros2-cdr.sensor_msgs.msg.pointcloud2")][0]')"
check "lidar payloads are canonical PointCloud2 CDR messages" [ "$LIDAR_PAYLOAD" != "null" ]
# The payload is compared with the locked reference recording it was built
# from, read in place from the read-only reference mount.
baseline_resolve full
RECORDING_PATH="$(echo "$BASELINE_FIXTURES" | jq -r --arg f "$SOURCE_UNIT" '.[] | select(.fixture_id == $f) | .path')"
DECODE="$(compose run --rm -T -v "$REPO_ROOT/scripts/e2e:/workspace/e2e:ro" --entrypoint python \
  recording-publisher /workspace/e2e/verify_lidar_payload_decode.py \
  --uri "$(echo "$LIDAR_PAYLOAD" | jq -r '.uri')" --recording "$RECORDING_PATH" </dev/null)"
echo "  $DECODE"
check "the payload is one message of the locked recording and decodes to that message's points" \
  [ "$(echo "$DECODE" | jq -r '.matches_recording')" = true ]
echo ""

echo "=== [8/8] canonical state untouched by every derived step ==="
check "Scene revisions are exactly those registered before any label or view existed" \
  [ "$(scene_revisions)" = "$SCENE_REVISIONS" ]
echo ""
echo "=== scene ML E2E complete: robot_run_id=$RUN_ID dataset=$DATASET_ID/$DATASET_VERSION label_set=$LABEL_SET_ID ==="
