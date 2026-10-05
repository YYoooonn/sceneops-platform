#!/usr/bin/env bash
# e2e_perception.sh — black-box vertical for the derived perception layer
# (ADR-007 §33). Data-plane steps run as one-shot containers; every SceneOps
# platform operation goes through the FastAPI control plane.
#
#   nuScenes v1.0-mini scene (read-only mount)
#     -> dataset-acquisition container        finalized sensor-bearing MCAP
#     -> dataset-acquisition nuscenes-labels  label set document (post-acquisition labels)
#     -> recording-publisher + POST /robot-runs:register     RobotRun
#     -> recording_scene_building pipeline    canonical Scenes (no labels, no keyframes)
#     -> IMPORT_LABELS                        independent, pinned label set revision
#     -> BUILD_SCENE_SAMPLE_VIEWS             policy-driven synchronized views of each Scene
#     -> scenario_curation pipeline           pinned ScenarioSet revision + readiness
#     -> detection_evaluation pipeline        prediction revision -> evaluation against
#                                             the pinned label revision
#     -> lineage: every revision pins what it consumed; retries converge
#     -> the real lidar payload (PointCloud2 CDR) decodes to the source points
#
# The host needs Docker Compose, curl and jq, plus the API port. It never
# reads PostgreSQL or MinIO directly; label documents reach the worker the way
# any external input does, through the bind-mounted ./data/raw area.
#
# BACKEND=mock (default) needs nothing beyond `make local-up`. BACKEND=
# grounding_dino additionally needs a running inference server
# (`make inference-local-up`) and lifts boxes through the real lidar payload.
#
# Prerequisites:
#   make local-up               API + workers + Postgres + MinIO from current images
#   make acquisition-image      (make e2e-perception builds it)
#   data/raw/nuscenes with v1.0-mini and can_bus (ACQUISITION_NUSCENES_ROOT overrides)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"
source "$SCRIPT_DIR/lib.sh"

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
BACKEND="${BACKEND:-mock}"
SOURCE_VERSION="${SOURCE_VERSION:-v1.0-mini}"
SOURCE_UNIT="${SOURCE_UNIT:-scene-0061}"
SUFFIX="$(date +%s)-$$"
ROBOT_ID="${ROBOT_ID:-robot-perception}"
ROBOT_RUN_ID="run-perception-$SUFFIX"
DATASET_ID="${DATASET_ID:-test-e2e-perception}"
DATASET_VERSION="v-$SUFFIX"
LABEL_SET_ID="nuscenes-$SOURCE_VERSION-$SOURCE_UNIT-$SUFFIX"
SEGMENT_NS="${SEGMENT_NS:-8000000000}"
POLL_ATTEMPTS="${POLL_ATTEMPTS:-180}"
MAX_SAMPLES="${MAX_SAMPLES:-}"

CAMERA="/camera/front/image/compressed"
LIDAR="/lidar/top/points"
CLOCK="sensor.header_stamp"

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

COMPOSE=(docker compose --env-file "${ENV_FILE:-.env.local}" --profile acquisition)
ACQUIRE=("${COMPOSE[@]}" run --rm -T dataset-acquisition)
PUBLISHER=("${COMPOSE[@]}" run --rm -T recording-publisher)
RECORDING="/recordings/$ROBOT_RUN_ID.mcap"
LABELS_IN_VOLUME="/recordings/$ROBOT_RUN_ID.labels.json"
LABELS_DIR="$REPO_ROOT/data/raw/labels"
LABELS_FILE="$LABELS_DIR/$ROBOT_RUN_ID.labels.json"
# The same file as the worker container sees it.
LABELS_URI="/data/raw/labels/$ROBOT_RUN_ID.labels.json"

cleanup() {
  "${COMPOSE[@]}" run --rm -T --entrypoint rm dataset-acquisition -f "$RECORDING" "$LABELS_IN_VOLUME" \
    >/dev/null 2>&1 || true
  rm -f "$LABELS_FILE"
}
trap cleanup EXIT

fail() {
  echo "❌ $*" >&2
  exit 1
}

check() {
  local label="$1"
  shift
  if "$@"; then
    echo "  ✅  $label"
  else
    fail "$label"
  fi
}

build_config() {
  jq -cn --argjson d "$SEGMENT_NS" '{
    channels: [
      {topic: "/camera/front/image/compressed", modality: "camera", sensor_id: "cam-front",
       time: {source: "header_stamp", clock: "sensor.header_stamp"},
       payload: "compressed_image", camera_info_topic: "/camera/front/camera_info"},
      {topic: "/lidar/top/points", modality: "lidar", sensor_id: "lidar-top",
       time: {source: "header_stamp", clock: "sensor.header_stamp"},
       payload: "ros2_message"}
    ],
    frames: {ego_frame_id: "base_link", world_frame_id: "map"},
    calibration: {static_transform_topics: ["/tf_static"]},
    poses: [{topic: "/tf", parent_frame_id: "map", child_frame_id: "base_link",
             time: {source: "header_stamp", clock: "sensor.header_stamp"}}],
    segmentation: {policy: "fixed_duration", clock: "sensor.header_stamp", duration_ns: $d}
  }'
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

run_job() {
  local type="$1" params="$2" force="${3:-true}" payload created job_id
  payload="$(jq -cn --arg t "$type" --arg ds "$DATASET_ID" --arg v "$DATASET_VERSION" \
    --argjson p "$params" --argjson force "$force" \
    '{type: $t, dataset_id: $ds, dataset_version: $v, params: $p, force: $force}')"
  created="$(create_job "$API_BASE_URL" "$payload")"
  job_id="$(extract_job_id "$created")"
  execute_job "$API_BASE_URL" "$job_id" >/dev/null
  poll_job_terminal "$API_BASE_URL" "$job_id" "$POLL_ATTEMPTS" 2 2>/dev/null
}

job_result() { echo "$1" | jq -c '.job.result'; }

run_pipeline() {
  local type="$1" params="$2" extra="${3:-}" payload run_id
  [ -n "$extra" ] || extra='{}'
  payload="$(jq -cn --arg t "$type" --arg ds "$DATASET_ID" --arg v "$DATASET_VERSION" \
    --argjson p "$params" --argjson extra "$extra" \
    '{type: $t, dataset_id: $ds, dataset_version: $v, force: true, params: $p} + $extra')"
  run_id="$(extract_pipeline_run_id "$(create_pipeline_run "$API_BASE_URL" "$payload")")"
  dispatch_pipeline_run "$API_BASE_URL" "$run_id" >/dev/null
  poll_pipeline_terminal "$API_BASE_URL" "$run_id" "$POLL_ATTEMPTS" 5 >/dev/null
  echo "$run_id"
}

task_json() {
  fetch_pipeline_tasks "$API_BASE_URL" "$1" | jq -c --arg t "$2" '.tasks[] | select(.pipelineTaskId == $t)'
}

echo "=== [0/9] control plane reachable ==="
curl -fsS "$API_BASE_URL/health" >/dev/null || fail "SceneOps API not reachable at $API_BASE_URL"
echo "  ✅  API healthy at $API_BASE_URL"
echo ""

echo "=== [1/9] nuScenes -> recording (MCAP) and label document ==="
SUMMARY="$("${ACQUIRE[@]}" nuscenes --dataroot /input/nuscenes --version "$SOURCE_VERSION" \
  --source-unit "$SOURCE_UNIT" --output "$RECORDING")"
echo "$SUMMARY" | jq -c '{sha256, size_bytes, message_count}'
"${PUBLISHER[@]}" check --mcap-path "$RECORDING" | jq -e '.conforms' >/dev/null \
  || fail "recording is not L1-conformant"
LABELS_SUMMARY="$("${ACQUIRE[@]}" nuscenes-labels --dataroot /input/nuscenes --version "$SOURCE_VERSION" \
  --source-unit "$SOURCE_UNIT" --robot-run-id "$ROBOT_RUN_ID" --label-set-id "$LABEL_SET_ID" \
  --output "$LABELS_IN_VOLUME")"
echo "$LABELS_SUMMARY" | jq -c '{label_set_id, coverage_count, label_count}'
SOURCE_LABELS="$(echo "$LABELS_SUMMARY" | jq -r '.label_count')"
SOURCE_COVERAGE="$(echo "$LABELS_SUMMARY" | jq -r '.coverage_count')"
mkdir -p "$LABELS_DIR"
"${COMPOSE[@]}" run --rm -T --entrypoint cat dataset-acquisition "$LABELS_IN_VOLUME" > "$LABELS_FILE"
check "the recording carries no annotation" \
  [ "$("${COMPOSE[@]}" run --rm -T --entrypoint python dataset-acquisition -c "import sys; d=open('$RECORDING','rb').read(); print(b'vehicle.car' in d or b'sample_annotation' in d)")" = False ]
check "label document written next to the recording ($SOURCE_LABELS labels over $SOURCE_COVERAGE samples)" \
  [ "$SOURCE_LABELS" -gt 0 ]
echo ""

echo "=== [2/9] publish + register the RobotRun, build and register canonical Scenes ==="
PUBLICATION="$("${PUBLISHER[@]}" publish --mcap-path "$RECORDING" \
  --run-id "$ROBOT_RUN_ID" --robot-id "$ROBOT_ID" --source-kind file)"
REG_JSON="$(register_robot_run "$API_BASE_URL" "$(echo "$PUBLICATION" | jq -r '.manifest_uri')")"
assert_job_succeeded "$REG_JSON" "REGISTER_ROBOT_RUN should succeed"
upsert_dataset "$API_BASE_URL" "$DATASET_ID" "Perception E2E" >/dev/null
upsert_dataset_version "$API_BASE_URL" "$DATASET_ID" "$DATASET_VERSION" >/dev/null
SCENE_PARAMS="$(jq -cn --arg run "$ROBOT_RUN_ID" --argjson config "$(build_config)" '{
  build_recording_scenes: {robot_run_id: $run, build_config: $config},
  register_scenes: {replace: false}, profile_scene: {triggered: true}}')"
PIPE_SCENES="$(run_pipeline recording_scene_building "$SCENE_PARAMS")"
assert_pipeline_succeeded "$(fetch_pipeline_run "$API_BASE_URL" "$PIPE_SCENES")" \
  "recording_scene_building should succeed" "$API_BASE_URL" "$PIPE_SCENES"
SCENES="$(curl -fsS "$(api_url "$API_BASE_URL" "/scenes?dataset_id=$DATASET_ID&dataset_version=$DATASET_VERSION&limit=500")")"
SCENE_COUNT="$(echo "$SCENES" | jq '.scenes | length')"
check "$SCENE_COUNT canonical Scenes registered" [ "$SCENE_COUNT" -gt 1 ]
check "the canonical Scenes embed no annotation" \
  [ "$(echo "$SCENES" | jq '[.scenes[].annotationCount] | add')" = 0 ]
SCENE_REVISIONS="$(echo "$SCENES" | jq -cS '[.scenes[] | {sceneId, manifestArtifactId, manifestChecksum}]')"
echo ""

echo "=== [3/9] IMPORT_LABELS: an independent, pinned label set revision ==="
IMPORT="$(run_job import_labels "$(jq -cn --arg u "$LABELS_URI" '{document_uri: $u}')")"
assert_job_succeeded "$IMPORT" "import_labels should succeed"
LABEL_RESULT="$(job_result "$IMPORT")"
LABEL_SET="$(echo "$LABEL_RESULT" | jq -c '.label_set')"
echo "  $(echo "$LABEL_RESULT" | jq -c '{label_count, covered_anchor_count, provenance_kind, created}')"
check "every source label was imported with its coverage" \
  [ "$(echo "$LABEL_RESULT" | jq -r '[.label_count, .covered_anchor_count] | join(",")')" = "$SOURCE_LABELS,$SOURCE_COVERAGE" ]
check "provenance is external (nuscenes), anchored on the registered RobotRun" \
  [ "$(echo "$LABEL_RESULT" | jq -r '[.provenance_kind, (.robot_run_ids | join(","))] | join(",")')" = "external,$ROBOT_RUN_ID" ]
LABEL_ARTIFACT="$(curl -fsS "$(api_url "$API_BASE_URL" "/artifacts/$(echo "$LABEL_SET" | jq -r '.manifest_artifact_id')")")"
check "the revision is a checksum-pinned label_set_manifest owned by the label set" \
  [ "$(echo "$LABEL_ARTIFACT" | jq -r --arg id "$LABEL_SET_ID" --arg c "$(echo "$LABEL_SET" | jq -r '.manifest_checksum')" '[.artifact.kind, .artifact.ownerType, .artifact.ownerId, .artifact.checksum] | join(",") == ("label_set_manifest,label_set," + $id + "," + $c)')" = true ]
REIMPORT="$(run_job import_labels "$(jq -cn --arg u "$LABELS_URI" '{document_uri: $u}')")"
check "re-importing the same document converges on the same revision" \
  [ "$(job_result "$REIMPORT" | jq -c '[.label_set, .created]')" = "$(echo "$LABEL_SET" | jq -c '[., false]')" ]
check "the Scenes are unchanged by label import" \
  [ "$(curl -fsS "$(api_url "$API_BASE_URL" "/scenes?dataset_id=$DATASET_ID&dataset_version=$DATASET_VERSION&limit=500")" | jq -cS '[.scenes[] | {sceneId, manifestArtifactId, manifestChecksum}]')" = "$SCENE_REVISIONS" ]
echo ""

echo "=== [4/9] BUILD_SCENE_SAMPLE_VIEWS: synchronization lives in the derived view ==="
VIEW_PARAMS="$(jq -cn --argjson policy "$(view_policy)" --argjson ls "$LABEL_SET" \
  '{policy: $policy, label_sets: [$ls]}')"
VIEWS="$(run_job build_scene_sample_views "$VIEW_PARAMS")"
assert_job_succeeded "$VIEWS" "build_scene_sample_views should succeed"
VIEWS_RESULT="$(job_result "$VIEWS")"
echo "  $(echo "$VIEWS_RESULT" | jq -c '{scene_count, sample_count, dropped_anchor_count, created_count, reused_count, skipped}')"
SAMPLE_VIEWS="$(echo "$VIEWS_RESULT" | jq -c '.views')"
check "every Scene with camera frames has a view" \
  [ "$(echo "$VIEWS_RESULT" | jq -r '.scene_count + (.skipped | length)')" = "$SCENE_COUNT" ]
check "samples exist, and anchors without a lidar sweep in tolerance are reported, not hidden" \
  [ "$(echo "$VIEWS_RESULT" | jq -r '.sample_count > 0 and .dropped_anchor_count >= 0')" = true ]
VIEW_ARTIFACT="$(curl -fsS "$(api_url "$API_BASE_URL" "/artifacts/$(echo "$SAMPLE_VIEWS" | jq -r '.[0].manifest_artifact_id')")")"
check "a view is a checksum-pinned scene_sample_view_manifest owned by its Scene" \
  [ "$(echo "$VIEW_ARTIFACT" | jq -r --arg s "$(echo "$SAMPLE_VIEWS" | jq -r '.[0].scene_id')" '[.artifact.kind, .artifact.ownerType, .artifact.ownerId] | join(",") == ("scene_sample_view_manifest,scene," + $s)')" = true ]
VIEWS_AGAIN="$(run_job build_scene_sample_views "$VIEW_PARAMS")"
check "rebuilding with the same policy and label revision reproduces the same views" \
  [ "$(job_result "$VIEWS_AGAIN" | jq -c '[.views, .created_count]')" = "$(echo "$SAMPLE_VIEWS" | jq -c '[., 0]')" ]
echo ""

echo "=== [5/9] scenario_curation: a pinned ScenarioSet revision ==="
CURATION_PARAMS="$(jq -cn --argjson views "$SAMPLE_VIEWS" --arg ls "$LABEL_SET_ID" '{
  mine_scenarios: {sample_views: $views, label_set_id: $ls, require_labels: true,
                   required_channels: ["/camera/front/image/compressed", "/lidar/top/points"],
                   max_candidates: 50},
  score_scenario_readiness: {}}')"
PIPE_CURATION="$(run_pipeline scenario_curation "$CURATION_PARAMS")"
assert_pipeline_succeeded "$(fetch_pipeline_run "$API_BASE_URL" "$PIPE_CURATION")" \
  "scenario_curation should succeed" "$API_BASE_URL" "$PIPE_CURATION"
MINE="$(task_json "$PIPE_CURATION" mine_scenarios)"
SCENARIO_SET_ID="$(echo "$MINE" | jq -r '.result.refs.scenario_set_id')"
SCENARIO_SET_CHECKSUM="$(echo "$MINE" | jq -r '.result.refs.scenario_set_checksum')"
SELECTED="$(echo "$MINE" | jq -r '.result.summary.selected_count_summary // .result.summary.selected_count // 0')"
echo "  scenario_set_id=$SCENARIO_SET_ID selected=$SELECTED"
check "labelled Scenes were selected" [ "$SELECTED" -gt 0 ]
SET_RECORD="$(curl -fsS "$(api_url "$API_BASE_URL" "/scenarios/$SCENARIO_SET_ID")")"
check "the ScenarioSet record pins exactly the mined revision" \
  [ "$(echo "$SET_RECORD" | jq -r '.scenarioSet.manifestChecksum')" = "$SCENARIO_SET_CHECKSUM" ]
echo ""

echo "=== [6/9] detection_evaluation ($BACKEND): predictions pin their inputs, evaluation pins both revisions ==="
if [ "$BACKEND" = "mock" ]; then
  upsert_model "$API_BASE_URL" "$MODEL_ID" "$MODEL_VERSION" "Perception E2E detector" >/dev/null
else
  poll_inference_ready "$INFERENCE_SERVER_URL" 60 5
  upsert_model_with_backend "$API_BASE_URL" "$MODEL_ID" "$MODEL_VERSION" "$INFERENCE_ENDPOINT_URL" \
    "Perception E2E detector" grounding_dino >/dev/null
fi
PREDICT_PARAMS="$(jq -cn --arg set "$SCENARIO_SET_ID" --arg backend "$BACKEND" --arg cam "$CAMERA" \
  --arg lidar "$LIDAR" --arg max "$MAX_SAMPLES" '{
  inference_backend: $backend, scenario_set_id: $set, camera_channel: $cam}
  + (if $backend == "grounding_dino" then {lidar_channel: $lidar} else {} end)
  + (if $max == "" then {} else {max_samples: ($max | tonumber)} end)')"
EVAL_PARAMS="$(jq -cn --argjson ls "$LABEL_SET" '{label_set: $ls, match_distance_m: 2.0}')"
DETECTION_PARAMS="$(jq -cn --argjson p "$PREDICT_PARAMS" --argjson e "$EVAL_PARAMS" \
  '{predict_detection: $p, evaluate_detection: $e}')"
PIPE_DETECT="$(run_pipeline detection_evaluation "$DETECTION_PARAMS" \
  "$(jq -cn --arg m "$MODEL_ID" --arg v "$MODEL_VERSION" '{model_id: $m, model_version: $v}')")"
assert_pipeline_succeeded "$(fetch_pipeline_run "$API_BASE_URL" "$PIPE_DETECT")" \
  "detection_evaluation should succeed" "$API_BASE_URL" "$PIPE_DETECT"
fetch_pipeline_tasks "$API_BASE_URL" "$PIPE_DETECT" | jq -r '.tasks[] | "  \(.pipelineTaskId): \(.status)"'
PREDICT_TASK="$(task_json "$PIPE_DETECT" predict_detection)"
INFERENCE_RUN_ID="$(echo "$PREDICT_TASK" | jq -r '.result.refs.inference_run_id')"
PREDICTION_CHECKSUM="$(echo "$PREDICT_TASK" | jq -r '.result.refs.prediction_manifest_checksum')"
EVALUATION_RUN_ID="$(task_json "$PIPE_DETECT" evaluate_detection | jq -r '.result.refs.evaluation_run_id')"
INFERENCE="$(curl -fsS "$(api_url "$API_BASE_URL" "/inference/runs/$INFERENCE_RUN_ID")")"
EVALUATION="$(curl -fsS "$(api_url "$API_BASE_URL" "/evaluations/runs/$EVALUATION_RUN_ID")")"
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
INFERENCE_ARTIFACTS="$(fetch_inference_run_artifacts "$API_BASE_URL" "$INFERENCE_RUN_ID")"
assert_artifact_kind_present "$INFERENCE_ARTIFACTS" prediction_manifest "inference run should register its prediction manifest"
PM_ARTIFACT_ID="$(echo "$INFERENCE_ARTIFACTS" | jq -r '[.artifacts[] | select(.kind == "prediction_manifest")][0].artifactId')"
check "the prediction manifest ArtifactRecord id is derived from run and checksum, not random" \
  [ "$(echo "$PM_ARTIFACT_ID" | grep -c '^predmanifest-')" = 1 ]
echo ""

echo "=== [7/9] retry: identical inputs converge on identical revisions ==="
RERUN="$(run_job predict_detection "$(jq -cn --argjson p "$PREDICT_PARAMS" --arg id "$INFERENCE_RUN_ID" \
  --arg m "$MODEL_ID" --arg v "$MODEL_VERSION" '$p + {model_id: $m, model_version: $v, inference_run_id: $id}')")"
assert_job_succeeded "$RERUN" "re-running predict_detection under the same run id should succeed"
check "same run id, same inputs: the same prediction revision" \
  [ "$(job_result "$RERUN" | jq -r '.prediction_manifest_checksum')" = "$PREDICTION_CHECKSUM" ]
echo ""

echo "=== [8/9] the real lidar payload decodes as PointCloud2 (CDR) ==="
PAYLOAD_JSON="$(curl -fsS "$(api_url "$API_BASE_URL" "/artifacts?kind=observation_payload&owner_type=robot_run&owner_id=$ROBOT_RUN_ID&limit=500")")"
LIDAR_PAYLOAD="$(echo "$PAYLOAD_JSON" | jq -c '[.artifacts[] | select(.mediaType == "application/x.ros2-cdr.sensor_msgs.msg.pointcloud2")][0]')"
check "lidar payloads are canonical PointCloud2 CDR messages" [ "$LIDAR_PAYLOAD" != "null" ]
DECODE="$(docker compose --env-file "${ENV_FILE:-.env.local}" --profile debug --profile worker run --rm -T worker-cli \
  python /workspace/scripts/e2e/verify_lidar_payload_decode.py \
  --uri "$(echo "$LIDAR_PAYLOAD" | jq -r '.uri')" --source-root /data/raw/nuscenes)"
echo "  $DECODE"
check "the decoded cloud equals the source .pcd.bin points" \
  [ "$(echo "$DECODE" | jq -r '.matches_source')" = true ]
echo ""

echo "=== [9/9] canonical state untouched by every derived step ==="
check "Scene revisions are exactly those registered before any label or view existed" \
  [ "$(curl -fsS "$(api_url "$API_BASE_URL" "/scenes?dataset_id=$DATASET_ID&dataset_version=$DATASET_VERSION&limit=500")" | jq -cS '[.scenes[] | {sceneId, manifestArtifactId, manifestChecksum}]')" = "$SCENE_REVISIONS" ]
echo ""
echo "=== perception E2E complete: robot_run_id=$ROBOT_RUN_ID dataset=$DATASET_ID/$DATASET_VERSION label_set=$LABEL_SET_ID ==="
