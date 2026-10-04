#!/usr/bin/env bash
# e2e_recording_episode.sh — black-box vertical for recording -> canonical
# Episode (ADR-007 §29.19 step 8). Data-plane steps run as one-shot
# containers; every SceneOps platform operation goes through the FastAPI
# control plane.
#
#   nuScenes v1.0-mini scene (read-only mount)
#     -> dataset-acquisition container        finalized MCAP (camera, lidar, CAN, mission)
#     -> recording-publisher container        L1 conformance check + publication
#     -> POST /robot-runs:register            RobotRun
#     -> POST /pipelines/runs                 recording_episode_building:
#          build_recording_episodes           resolve_recording -> canonical EpisodeManifests
#                                             + OBSERVATION_PAYLOAD artifacts
#          register_episodes                  EpisodeRecords + DatasetVersion episode_count
#          validate_episode / profile_episode revision-pinned run records
#     -> GET /episodes, /episodes/{id}/manifest, /episodes/{id}/quality, /artifacts
#     -> retry (idempotent), changed build config (conflict, then replace)
#     -> recording_scene_building on the same RobotRun (sibling, shared payloads)
#
# The host needs Docker Compose, curl and jq, plus the API port. It never
# reads PostgreSQL or MinIO directly.
#
# Prerequisites:
#   make local-up               API + workers + Postgres + MinIO from current images
#   make acquisition-image      (make e2e-recording-episode builds it)
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
ROBOT_ID="${ROBOT_ID:-robot-recording-episode}"
ROBOT_RUN_ID="run-recording-episode-$SUFFIX"
DATASET_ID="${DATASET_ID:-test-e2e-recording-episode}"
DATASET_VERSION="v-$SUFFIX"
POLL_ATTEMPTS="${POLL_ATTEMPTS:-180}"
CLOCK="vehicle.source_time"

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

# Canonical Episode semantics of this recording, stated explicitly: which
# topics are observation / state / action streams, which fields they carry,
# which preserved timestamp is canonical, and which recorded task markers cut
# Episodes. The action schema below is this vehicle's configuration, not the
# Episode model. Nothing here names a source format.
build_config() {
  local segmentation="$1"
  jq -cn --arg clock "$CLOCK" --argjson segmentation "$segmentation" '
    def header: {source: "header_stamp", clock: $clock};
    def json_time: {source: "payload_field", clock: $clock, field: "source_timestamp_ns"};
    {
      streams: [
        {topic: "/camera/front/image/compressed", role: "observation", time: header,
         payload: "compressed_image"},
        {topic: "/vehicle/odom", role: "state", time: header,
         fields: [{name: "x", path: "pose.pose.position.x"},
                  {name: "y", path: "pose.pose.position.y"},
                  {name: "vx", path: "twist.twist.linear.x"}]},
        {topic: "/vehicle/status", role: "state", time: header,
         fields: [{name: "battery", path: "percentage"}]},
        {topic: "/vehicle/control", role: "action", decoding: "json_string", time: json_time,
         fields: [{name: "steering", path: "steering"},
                  {name: "throttle", path: "throttle"},
                  {name: "brake", path: "brake"}]}
      ],
      events: [
        {topic: "/mission/status", decoding: "json_string", time: json_time,
         fields: [{name: "mission_id", path: "mission_id"},
                  {name: "state", path: "operation_state"}]}
      ],
      segmentation: $segmentation
    }'
}

MARKERS='{"policy":"event_markers","event_topic":"/mission/status","key_field":"mission_id","state_field":"state","start_values":["running"],"end_values":["completed"]}'
FIXED_10S="{\"policy\":\"fixed_duration\",\"clock\":\"$CLOCK\",\"duration_ns\":10000000000}"

run_episode_pipeline() {
  local segmentation="$1" replace="$2"
  local payload run_id
  payload="$(jq -cn \
    --arg ds "$DATASET_ID" --arg v "$DATASET_VERSION" --arg run "$ROBOT_RUN_ID" \
    --argjson config "$(build_config "$segmentation")" --argjson replace "$replace" '{
      type: "recording_episode_building", dataset_id: $ds, dataset_version: $v, force: true,
      params: {
        build_recording_episodes: {robot_run_id: $run, build_config: $config},
        register_episodes: {replace: $replace},
        profile_episode: {triggered: true}
      }}')"
  run_id="$(extract_pipeline_run_id "$(create_pipeline_run "$API_BASE_URL" "$payload")")"
  dispatch_pipeline_run "$API_BASE_URL" "$run_id" >/dev/null
  poll_pipeline_terminal "$API_BASE_URL" "$run_id" "$POLL_ATTEMPTS" 5 >/dev/null
  echo "$run_id"
}

task_json() {
  fetch_pipeline_tasks "$API_BASE_URL" "$1" | jq -c --arg t "$2" '.tasks[] | select(.pipelineTaskId == $t)'
}

episodes_json() {
  curl -fsS "$(api_url "$API_BASE_URL" "/episodes?dataset_id=$DATASET_ID&dataset_version=$DATASET_VERSION&limit=500")"
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

echo "=== [0/8] control plane reachable ==="
curl -fsS "$API_BASE_URL/health" >/dev/null || fail "SceneOps API not reachable at $API_BASE_URL"
echo "  ✅  API healthy at $API_BASE_URL"
echo ""

echo "=== [1/8] nuScenes -> acquisition container -> MCAP -> L1 conformance ==="
SUMMARY="$("${ACQUIRE[@]}" nuscenes --dataroot /input/nuscenes --version "$SOURCE_VERSION" \
  --source-unit "$SOURCE_UNIT" --output "$RECORDING")"
echo "$SUMMARY" | jq -c '{sha256, size_bytes, message_count}'
"${PUBLISHER[@]}" check --mcap-path "$RECORDING" | jq -e '.conforms' >/dev/null \
  || fail "recording is not L1-conformant"
echo "  ✅  L1-conformant"
echo ""

echo "=== [2/8] publish + POST /robot-runs:register -> RobotRun ==="
PUBLICATION="$("${PUBLISHER[@]}" publish --mcap-path "$RECORDING" \
  --run-id "$ROBOT_RUN_ID" --robot-id "$ROBOT_ID" --source-kind file)"
REG_JSON="$(register_robot_run "$API_BASE_URL" "$(echo "$PUBLICATION" | jq -r '.manifest_uri')")"
assert_job_succeeded "$REG_JSON" "REGISTER_ROBOT_RUN should succeed"
check "RobotRun $ROBOT_RUN_ID registered" \
  curl -fsS -o /dev/null "$(api_url "$API_BASE_URL" "/robot-runs/$ROBOT_RUN_ID")"
TOPIC_COUNTS="$(echo "$SUMMARY" | jq -c '.topic_counts')"
echo ""

echo "=== [3/8] DatasetVersion + recording_episode_building pipeline ==="
upsert_dataset "$API_BASE_URL" "$DATASET_ID" "Recording Episode E2E" >/dev/null
upsert_dataset_version "$API_BASE_URL" "$DATASET_ID" "$DATASET_VERSION" >/dev/null
START=$(date +%s)
PIPE1="$(run_episode_pipeline "$MARKERS" false)"
echo "  pipeline_run_id=$PIPE1 ($(( $(date +%s) - START ))s)"
assert_pipeline_succeeded "$(fetch_pipeline_run "$API_BASE_URL" "$PIPE1")" \
  "recording_episode_building should succeed" "$API_BASE_URL" "$PIPE1"
fetch_pipeline_tasks "$API_BASE_URL" "$PIPE1" | jq -r '.tasks[] | "  \(.pipelineTaskId): \(.status)"'
BUILD="$(task_json "$PIPE1" build_recording_episodes)"
echo "  build: $(echo "$BUILD" | jq -c '.result.summary')"
PAYLOAD_COUNT="$(echo "$BUILD" | jq -r '.result.summary.payload_artifact_count')"
MANIFEST_IDS="$(echo "$BUILD" | jq -c '.result.refs.manifest_artifact_ids | sort')"
check "one Episode per recorded task (mission-$SOURCE_UNIT)" \
  [ "$(echo "$BUILD" | jq -r '.result.summary.unit_keys | join(",")')" = "task-mission-$SOURCE_UNIT-000" ]
check "every task of the pipeline succeeded" \
  [ "$(fetch_pipeline_tasks "$API_BASE_URL" "$PIPE1" | jq '[.tasks[] | select(.status != "succeeded")] | length')" = 0 ]
echo ""

echo "=== [4/8] canonical Episode through the API ==="
EPISODES="$(episodes_json)"
EPISODE="$(echo "$EPISODES" | jq -c '.episodes[0]')"
EPISODE_ID="$(echo "$EPISODE" | jq -r '.episodeId')"
check "EpisodeRecord points at the source RobotRun and the published revision" \
  [ "$(echo "$EPISODE" | jq -r '.robotRunId')$(echo "$EPISODES" | jq -c '[.episodes[].manifestArtifactId] | sort')" = "$ROBOT_RUN_ID$MANIFEST_IDS" ]
check "window is on the declared source clock" [ "$(echo "$EPISODE" | jq -r '.windowClock')" = "$CLOCK" ]
MANIFEST="$(curl -fsS "$(api_url "$API_BASE_URL" "/episodes/$EPISODE_ID/manifest")" | jq -c '.manifest')"
check "the window is [running marker, completed marker + 1)" \
  [ "$(echo "$MANIFEST" | jq '[.events[].timestamp_ns] as $e | [.lineage.source.start_timestamp_ns == ($e | min), .lineage.source.end_timestamp_ns == ($e | max) + 1] | all')" = true ]
check "every recorded action, state and observation is kept (no resampling)" \
  [ "$(echo "$MANIFEST" | jq -c --argjson c "$TOPIC_COUNTS" '[([.actions[] | select(.topic == "/vehicle/control")] | length) == $c["/vehicle/control"], ([.states[] | select(.topic == "/vehicle/odom")] | length) == $c["/vehicle/odom"], ([.observations[]] | length) == $c["/camera/front/image/compressed"]] | all')" = true ]
check "streams stay asynchronous (action and odometry timestamps differ)" \
  [ "$(echo "$MANIFEST" | jq '([.actions[].timestamp_ns] - [.states[] | select(.topic == "/vehicle/odom") | .timestamp_ns] | length) > 0')" = true ]
check "action values are the configured fields, verbatim" \
  [ "$(echo "$MANIFEST" | jq -r '.actions[0].values | keys | join(",")')" = "brake,steering,throttle" ]
check "no label, outcome or alignment field exists in the manifest" \
  [ "$(echo "$MANIFEST" | jq '[has("outcome"), has("task"), has("control_frequency_hz")] | any')" = false ]
check "OBSERVATION_PAYLOAD ArtifactRecords exist for every planned payload ($PAYLOAD_COUNT)" \
  [ "$(count_payload_artifacts)" = "$PAYLOAD_COUNT" ]
check "DatasetVersion episode_count written by the registrar" \
  [ "$(curl -fsS "$(api_url "$API_BASE_URL" "/datasets/$DATASET_ID/versions/$DATASET_VERSION")" | jq -r '.version.episode.episodeCount')" = 1 ]
QUALITY="$(curl -fsS "$(api_url "$API_BASE_URL" "/episodes/$EPISODE_ID/quality")")"
check "validated and profiled at its current revision" \
  [ "$(echo "$QUALITY" | jq -r '[.validation != null, .profile != null, .readiness == "ready"] | all')" = true ]
echo ""

echo "=== [5/8] retry: identical rebuild converges ==="
PIPE2="$(run_episode_pipeline "$MARKERS" false)"
assert_pipeline_succeeded "$(fetch_pipeline_run "$API_BASE_URL" "$PIPE2")" "retry should succeed" "$API_BASE_URL" "$PIPE2"
BUILD2="$(task_json "$PIPE2" build_recording_episodes)"
check "rebuild publishes the same manifest artifacts and no new payloads" \
  [ "$(echo "$BUILD2" | jq -c '.result.refs.manifest_artifact_ids | sort')$(echo "$BUILD2" | jq -r '.result.summary.created_payload_count')" = "${MANIFEST_IDS}0" ]
check "registration is a no-op for the unchanged scope" \
  [ "$(task_json "$PIPE2" register_episodes | jq -r '.result.summary.unchanged_episode_ids | length')" = 1 ]
check "EpisodeRecords unchanged" [ "$(episodes_json | jq -cS '[.episodes[] | del(.updatedAt)]')" = "$(echo "$EPISODES" | jq -cS '[.episodes[] | del(.updatedAt)]')" ]
echo ""

echo "=== [6/8] changed build config: conflict without replace ==="
PIPE3="$(run_episode_pipeline "$FIXED_10S" false)"
check "pipeline fails at register_episodes" \
  [ "$(task_json "$PIPE3" register_episodes | jq -r '.status')" = "failed" ]
check "canonical membership unchanged" \
  [ "$(episodes_json | jq -c '[.episodes[].manifestArtifactId] | sort')" = "$MANIFEST_IDS" ]
echo ""

echo "=== [7/8] changed build config: explicit replacement ==="
PIPE4="$(run_episode_pipeline "$FIXED_10S" true)"
assert_pipeline_succeeded "$(fetch_pipeline_run "$API_BASE_URL" "$PIPE4")" "replacement should succeed" "$API_BASE_URL" "$PIPE4"
AFTER="$(episodes_json)"
NEW_COUNT="$(task_json "$PIPE4" build_recording_episodes | jq -r '.result.summary.episode_count')"
check "the scope now holds exactly the new build ($NEW_COUNT Episodes, one fingerprint)" \
  [ "$(echo "$AFTER" | jq -r '[(.episodes | length), ([.episodes[].producerFingerprint] | unique | length)] | join(",")')" = "$NEW_COUNT,1" ]
check "DatasetVersion episode_count follows the replacement" \
  [ "$(curl -fsS "$(api_url "$API_BASE_URL" "/datasets/$DATASET_ID/versions/$DATASET_VERSION")" | jq -r '.version.episode.episodeCount')" = "$NEW_COUNT" ]
echo ""

echo "=== [8/8] sibling Scene build on the same RobotRun ==="
SCENE_PAYLOAD="$(jq -cn --arg ds "$DATASET_ID" --arg v "$DATASET_VERSION" --arg run "$ROBOT_RUN_ID" '{
  type: "recording_scene_building", dataset_id: $ds, dataset_version: $v, force: true,
  params: {build_recording_scenes: {robot_run_id: $run, build_config: {
    channels: [{topic: "/camera/front/image/compressed", modality: "camera", sensor_id: "cam-front",
                time: {source: "header_stamp", clock: "vehicle.source_time"},
                payload: "compressed_image", camera_info_topic: "/camera/front/camera_info"}],
    frames: {ego_frame_id: "base_link", world_frame_id: "map"},
    segmentation: {policy: "fixed_duration", clock: "vehicle.source_time", duration_ns: 8000000000}}},
    profile_scene: {triggered: true}}}')"
SCENE_RUN="$(extract_pipeline_run_id "$(create_pipeline_run "$API_BASE_URL" "$SCENE_PAYLOAD")")"
dispatch_pipeline_run "$API_BASE_URL" "$SCENE_RUN" >/dev/null
poll_pipeline_terminal "$API_BASE_URL" "$SCENE_RUN" "$POLL_ATTEMPTS" 5 >/dev/null
assert_pipeline_succeeded "$(fetch_pipeline_run "$API_BASE_URL" "$SCENE_RUN")" "recording_scene_building should succeed" "$API_BASE_URL" "$SCENE_RUN"
SCENE_BUILD="$(task_json "$SCENE_RUN" build_recording_scenes)"
check "the Scene build reuses every camera payload the Episode build extracted" \
  [ "$(echo "$SCENE_BUILD" | jq -r '.result.summary.created_payload_count')" = 0 ]
check "Episodes are untouched by the sibling Scene build" \
  [ "$(episodes_json | jq -cS '[.episodes[] | del(.updatedAt)]')" = "$(echo "$AFTER" | jq -cS '[.episodes[] | del(.updatedAt)]')" ]
echo ""
echo "=== recording episode E2E complete: robot_run_id=$ROBOT_RUN_ID dataset=$DATASET_ID/$DATASET_VERSION ==="
