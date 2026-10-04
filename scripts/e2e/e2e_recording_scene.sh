#!/usr/bin/env bash
# e2e_recording_scene.sh — black-box E2E for recording -> canonical Scene
# (ADR-007 §29.19 step 7). Data-plane steps run as one-shot containers;
# every SceneOps platform operation goes through the FastAPI control plane.
#
#   nuScenes v1.0-mini scene (read-only mount)
#     -> dataset-acquisition container        finalized sensor-bearing MCAP (Docker volume)
#     -> recording-publisher container        L1 conformance check + publication
#     -> POST /robot-runs:register            RobotRun
#     -> POST /pipelines/runs                 recording_scene_building:
#          build_recording_scenes             resolve_recording -> canonical SceneManifests
#                                             + OBSERVATION_PAYLOAD artifacts
#          register_scenes                    SceneRecords + DatasetVersion summary
#          validate_scene / profile_scene     revision-pinned run records
#     -> GET /scenes, /scenes/{id}/quality, /artifacts, /datasets/...
#     -> retry (idempotent), changed build config (conflict, then replace)
#
# The host needs Docker Compose, curl and jq, plus the API port. It never
# reads PostgreSQL or MinIO directly.
#
# Prerequisites:
#   make local-up               API + workers + Postgres + MinIO from current images
#   make acquisition-image      (make e2e-recording-scene builds it)
#   data/raw/nuscenes with v1.0-mini and can_bus (ACQUISITION_NUSCENES_ROOT overrides)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"
source "$SCRIPT_DIR/lib.sh"

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
SOURCE_VERSION="${SOURCE_VERSION:-v1.0-mini}"
SOURCE_UNIT="${SOURCE_UNIT:-scene-0061}"
SUFFIX="$(date +%s)-$$"
ROBOT_ID="${ROBOT_ID:-robot-recording-scene}"
ROBOT_RUN_ID="run-recording-scene-$SUFFIX"
DATASET_ID="${DATASET_ID:-test-e2e-recording-scene}"
DATASET_VERSION="v-$SUFFIX"
SEGMENT_NS="${SEGMENT_NS:-8000000000}"
POLL_ATTEMPTS="${POLL_ATTEMPTS:-180}"

COMPOSE=(docker compose --env-file "${ENV_FILE:-.env.local}" --profile acquisition)
ACQUIRE=("${COMPOSE[@]}" run --rm -T dataset-acquisition)
PUBLISHER=("${COMPOSE[@]}" run --rm -T recording-publisher)
RECORDING="/recordings/$ROBOT_RUN_ID.mcap"

cleanup() {
  "${COMPOSE[@]}" run --rm -T --entrypoint rm dataset-acquisition -f "$RECORDING" \
    >/dev/null 2>&1 || true
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

# Canonical semantics of this recording, stated explicitly: which topics are
# Scene channels, what they mean, which preserved timestamp is canonical and
# on which clock Scenes are cut. Nothing here names a source format.
build_config() {
  local duration_ns="$1"
  jq -cn --argjson d "$duration_ns" '{
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

run_scene_pipeline() {
  local duration_ns="$1" replace="$2"
  local payload run_id
  payload="$(jq -cn \
    --arg ds "$DATASET_ID" --arg v "$DATASET_VERSION" --arg run "$ROBOT_RUN_ID" \
    --argjson config "$(build_config "$duration_ns")" --argjson replace "$replace" '{
      type: "recording_scene_building", dataset_id: $ds, dataset_version: $v, force: true,
      params: {
        build_recording_scenes: {robot_run_id: $run, build_config: $config},
        register_scenes: {replace: $replace},
        profile_scene: {triggered: true}
      }}')"
  run_id="$(extract_pipeline_run_id "$(create_pipeline_run "$API_BASE_URL" "$payload")")"
  dispatch_pipeline_run "$API_BASE_URL" "$run_id" >/dev/null
  poll_pipeline_terminal "$API_BASE_URL" "$run_id" "$POLL_ATTEMPTS" 5 >/dev/null
  echo "$run_id"
}

task_json() {
  fetch_pipeline_tasks "$API_BASE_URL" "$1" | jq -c --arg t "$2" '.tasks[] | select(.pipelineTaskId == $t)'
}

scenes_json() {
  curl -fsS "$(api_url "$API_BASE_URL" "/scenes?dataset_id=$DATASET_ID&dataset_version=$DATASET_VERSION&limit=500")"
}

count_payload_artifacts() {
  local offset=0 total=0 page
  while :; do
    page="$(curl -fsS "$(api_url "$API_BASE_URL" "/artifacts?kind=observation_payload&owner_type=robot_run&owner_id=$ROBOT_RUN_ID&limit=500&offset=$offset")" | jq '.artifacts | length')"
    total=$((total + page))
    [ "$page" -lt 500 ] && break
    offset=$((offset + 500))
  done
  echo "$total"
}

echo "=== [0/7] control plane reachable ==="
curl -fsS "$API_BASE_URL/health" >/dev/null || fail "SceneOps API not reachable at $API_BASE_URL"
echo "  ✅  API healthy at $API_BASE_URL"
echo ""

echo "=== [1/7] nuScenes -> acquisition container -> MCAP -> L1 conformance ==="
SUMMARY="$("${ACQUIRE[@]}" nuscenes --dataroot /input/nuscenes --version "$SOURCE_VERSION" \
  --source-unit "$SOURCE_UNIT" --output "$RECORDING")"
echo "$SUMMARY" | jq -c '{sha256, size_bytes, message_count}'
"${PUBLISHER[@]}" check --mcap-path "$RECORDING" | jq -e '.conforms' >/dev/null \
  || fail "recording is not L1-conformant"
echo "  ✅  L1-conformant"
echo ""

echo "=== [2/7] publish + POST /robot-runs:register -> RobotRun ==="
PUBLICATION="$("${PUBLISHER[@]}" publish --mcap-path "$RECORDING" \
  --run-id "$ROBOT_RUN_ID" --robot-id "$ROBOT_ID" --source-kind file)"
REG_JSON="$(register_robot_run "$API_BASE_URL" "$(echo "$PUBLICATION" | jq -r '.manifest_uri')")"
assert_job_succeeded "$REG_JSON" "REGISTER_ROBOT_RUN should succeed"
RUN="$(curl -fsS "$(api_url "$API_BASE_URL" "/robot-runs/$ROBOT_RUN_ID")" | jq -c '.robotRun')"
RECORDING_CHECKSUM="$(echo "$SUMMARY" | jq -r '.sha256')"
check "RobotRun $ROBOT_RUN_ID registered with the acquired recording" \
  [ "$(curl -fsS "$(api_url "$API_BASE_URL" "/artifacts/$(echo "$RUN" | jq -r '.recordingArtifactId')")" | jq -r '.artifact.checksum')" = "$RECORDING_CHECKSUM" ]
echo ""

echo "=== [3/7] DatasetVersion + recording_scene_building pipeline ==="
upsert_dataset "$API_BASE_URL" "$DATASET_ID" "Recording Scene E2E" >/dev/null
upsert_dataset_version "$API_BASE_URL" "$DATASET_ID" "$DATASET_VERSION" >/dev/null
START=$(date +%s)
PIPE1="$(run_scene_pipeline "$SEGMENT_NS" false)"
echo "  pipeline_run_id=$PIPE1 ($(( $(date +%s) - START ))s)"
assert_pipeline_succeeded "$(fetch_pipeline_run "$API_BASE_URL" "$PIPE1")" \
  "recording_scene_building should succeed" "$API_BASE_URL" "$PIPE1"
fetch_pipeline_tasks "$API_BASE_URL" "$PIPE1" | jq -r '.tasks[] | "  \(.pipelineTaskId): \(.status)"'
BUILD="$(task_json "$PIPE1" build_recording_scenes)"
REGISTER="$(task_json "$PIPE1" register_scenes)"
echo "  build: $(echo "$BUILD" | jq -c '.result.summary')"
SCENE_COUNT="$(echo "$BUILD" | jq -r '.result.summary.scene_count')"
PAYLOAD_COUNT="$(echo "$BUILD" | jq -r '.result.summary.payload_artifact_count')"
MANIFEST_IDS="$(echo "$BUILD" | jq -c '.result.refs.manifest_artifact_ids | sort')"
check "more than one Scene built from the RobotRun" [ "$SCENE_COUNT" -gt 1 ]
check "every task of the pipeline succeeded" \
  [ "$(fetch_pipeline_tasks "$API_BASE_URL" "$PIPE1" | jq '[.tasks[] | select(.status != "succeeded")] | length')" = 0 ]
echo ""

echo "=== [4/7] canonical state through the API ==="
SCENES="$(scenes_json)"
check "SceneRecords == registered set, each pointing at the source RobotRun" \
  [ "$(echo "$SCENES" | jq -r --arg run "$ROBOT_RUN_ID" '[.scenes[] | select(.robotRunId == $run)] | length')" = "$SCENE_COUNT" ]
check "each SceneRecord pins exactly a manifest revision the build published" \
  [ "$(echo "$SCENES" | jq -c '[.scenes[].manifestArtifactId] | sort')" = "$MANIFEST_IDS" ]
check "segment windows are on the declared source clock" \
  [ "$(echo "$SCENES" | jq -r '[.scenes[].windowClock] | unique | join(",")')" = "sensor.header_stamp" ]
check "observed channels are the configured topics, verbatim" \
  [ "$(echo "$SCENES" | jq -r '[.scenes[].observedChannels[]] | unique | join(",")')" = "/camera/front/image/compressed,/lidar/top/points" ]
for id in $(echo "$SCENES" | jq -r '.scenes[].manifestArtifactId'); do
  curl -fsS "$(api_url "$API_BASE_URL" "/artifacts/$id")" | jq -e '.artifact.kind == "scene_manifest" and (.artifact.checksum | startswith("sha256:"))' >/dev/null \
    || fail "manifest artifact $id is not a checksum-pinned scene_manifest"
done
echo "  ✅  manifest ArtifactRecords exist with pinned checksums"
check "OBSERVATION_PAYLOAD ArtifactRecords exist for every planned payload ($PAYLOAD_COUNT)" \
  [ "$(count_payload_artifacts)" = "$PAYLOAD_COUNT" ]
DV="$(curl -fsS "$(api_url "$API_BASE_URL" "/datasets/$DATASET_ID/versions/$DATASET_VERSION")" | jq -c '.version')"
check "DatasetVersion summary written by the registrar" \
  [ "$(echo "$DV" | jq -r '.scene.sceneCount')" = "$SCENE_COUNT" ]
for scene_id in $(echo "$SCENES" | jq -r '.scenes[].sceneId'); do
  QUALITY="$(curl -fsS "$(api_url "$API_BASE_URL" "/scenes/$scene_id/quality")")"
  echo "$QUALITY" | jq -e '.validation != null and .profile != null' >/dev/null \
    || fail "scene $scene_id has no validation/profile run for its current revision"
done
echo "  ✅  every Scene validated and profiled at its current revision"
echo ""

echo "=== [5/7] retry: identical rebuild converges ==="
PIPE2="$(run_scene_pipeline "$SEGMENT_NS" false)"
assert_pipeline_succeeded "$(fetch_pipeline_run "$API_BASE_URL" "$PIPE2")" "retry should succeed" "$API_BASE_URL" "$PIPE2"
BUILD2="$(task_json "$PIPE2" build_recording_scenes)"
REGISTER2="$(task_json "$PIPE2" register_scenes)"
check "rebuild publishes the same manifest artifacts and no new payloads" \
  [ "$(echo "$BUILD2" | jq -c '.result.refs.manifest_artifact_ids | sort')$(echo "$BUILD2" | jq -r '.result.summary.created_payload_count')" = "${MANIFEST_IDS}0" ]
check "registration is a no-op for the unchanged scope" \
  [ "$(echo "$REGISTER2" | jq -r '.result.summary.unchanged_scene_ids | length')" = "$SCENE_COUNT" ]
check "SceneRecords unchanged" [ "$(scenes_json | jq -cS '[.scenes[] | del(.updatedAt)]')" = "$(echo "$SCENES" | jq -cS '[.scenes[] | del(.updatedAt)]')" ]
echo ""

echo "=== [6/7] changed build config: conflict without replace ==="
PIPE3="$(run_scene_pipeline 60000000000 false)"
check "pipeline fails at register_scenes" \
  [ "$(task_json "$PIPE3" register_scenes | jq -r '.status')" = "failed" ]
check "canonical membership unchanged" \
  [ "$(scenes_json | jq -c '[.scenes[].manifestArtifactId] | sort')" = "$MANIFEST_IDS" ]
echo ""

echo "=== [7/7] changed build config: explicit replacement ==="
PIPE4="$(run_scene_pipeline 60000000000 true)"
assert_pipeline_succeeded "$(fetch_pipeline_run "$API_BASE_URL" "$PIPE4")" "replacement should succeed" "$API_BASE_URL" "$PIPE4"
AFTER="$(scenes_json)"
check "the scope now holds exactly the new build (one Scene, one fingerprint)" \
  [ "$(echo "$AFTER" | jq -r '[(.scenes | length), ([.scenes[].producerFingerprint] | unique | length)] | join(",")')" = "1,1" ]
check "DatasetVersion summary follows the replacement" \
  [ "$(curl -fsS "$(api_url "$API_BASE_URL" "/datasets/$DATASET_ID/versions/$DATASET_VERSION")" | jq -r '.version.scene.sceneCount')" = "1" ]
echo ""
echo "=== recording scene E2E complete: robot_run_id=$ROBOT_RUN_ID dataset=$DATASET_ID/$DATASET_VERSION ==="
