#!/usr/bin/env bash
# e2e_episode_alignment.sh — black-box vertical for the derived Episode layer
# (ADR-007 §33.6, §33.7). Data-plane steps run as one-shot containers; every
# SceneOps platform operation goes through the FastAPI control plane.
#
#   nuScenes v1.0-mini scene -> dataset-acquisition container -> MCAP
#     -> recording-publisher + POST /robot-runs:register      RobotRun
#     -> recording_episode_building pipeline                  canonical Episode (asynchronous)
#     -> aligned_episode_building pipeline                    align (explicit timeline /
#                                                             association) -> validate -> profile
#     -> EXPORT_LEARNING_DATA                                 columnar export of the pinned
#                                                             aligned revision
#     -> retries converge on the same revisions and the same deterministic ids
#
# The canonical Episode is never rewritten: alignment is a derived revision.
#
# Prerequisites:
#   make local-up               API + workers + Postgres + MinIO from current images
#   make acquisition-image
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
ROBOT_ID="${ROBOT_ID:-robot-episode-alignment}"
ROBOT_RUN_ID="run-episode-alignment-$SUFFIX"
DATASET_ID="${DATASET_ID:-test-e2e-episode-alignment}"
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
check "RobotRun $ROBOT_RUN_ID registered" \
  curl -fsS -o /dev/null "$(api_url "$API_BASE_URL" "/robot-runs/$ROBOT_RUN_ID")"
TOPIC_COUNTS="$(echo "$SUMMARY" | jq -c '.topic_counts')"
echo ""

echo "=== [3/7] DatasetVersion + recording_episode_building pipeline ==="
upsert_dataset "$API_BASE_URL" "$DATASET_ID" "Episode Alignment E2E" >/dev/null
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

run_job() {
  local type="$1" params="$2" payload created job_id
  payload="$(jq -cn --arg t "$type" --arg ds "$DATASET_ID" --arg v "$DATASET_VERSION" --argjson p "$params" \
    '{type: $t, dataset_id: $ds, dataset_version: $v, params: $p, force: true}')"
  created="$(create_job "$API_BASE_URL" "$payload")"
  job_id="$(extract_job_id "$created")"
  execute_job "$API_BASE_URL" "$job_id" >/dev/null
  poll_job_terminal "$API_BASE_URL" "$job_id" "$POLL_ATTEMPTS" 2 2>/dev/null
}

# The alignment, stated explicitly: a 5 Hz timeline over the Episode window,
# nearest association within 200 ms (observations, states), the previous
# recorded action held. The Episode's own window clock is the alignment clock.
align_params() {
  jq -cn --arg id "$EPISODE_ID" '{
    align_episode: {episode_id: $id,
      alignment_config: {target_frequency_hz: 5.0, tolerance_us: 200000, max_gap_us: 1000000}},
    profile_aligned_episode: {triggered: true}}'
}

run_aligned_pipeline() {
  local payload run_id
  payload="$(jq -cn --arg ds "$DATASET_ID" --arg v "$DATASET_VERSION" --argjson p "$(align_params)" \
    '{type: "aligned_episode_building", dataset_id: $ds, dataset_version: $v, force: true, params: $p}')"
  run_id="$(extract_pipeline_run_id "$(create_pipeline_run "$API_BASE_URL" "$payload")")"
  dispatch_pipeline_run "$API_BASE_URL" "$run_id" >/dev/null
  poll_pipeline_terminal "$API_BASE_URL" "$run_id" "$POLL_ATTEMPTS" 5 >/dev/null
  echo "$run_id"
}

echo "=== [4/7] the canonical Episode (asynchronous L2) ==="
EPISODES="$(episodes_json)"
EPISODE="$(echo "$EPISODES" | jq -c '.episodes[0]')"
EPISODE_ID="$(echo "$EPISODE" | jq -r '.episodeId')"
EPISODE_REVISION="$(echo "$EPISODE" | jq -c '{manifestArtifactId, manifestChecksum}')"
echo "  episode_id=$EPISODE_ID"
check "the Episode scope is $DATASET_ID/$DATASET_VERSION" \
  [ "$(echo "$EPISODE" | jq -r '[.datasetId, .datasetVersion] | join("/")')" = "$DATASET_ID/$DATASET_VERSION" ]
echo ""

echo "=== [5/7] aligned_episode_building: derived, pinned, deterministic ==="
PIPE_A="$(run_aligned_pipeline)"
assert_pipeline_succeeded "$(fetch_pipeline_run "$API_BASE_URL" "$PIPE_A")" \
  "aligned_episode_building should succeed" "$API_BASE_URL" "$PIPE_A"
fetch_pipeline_tasks "$API_BASE_URL" "$PIPE_A" | jq -r '.tasks[] | "  \(.pipelineTaskId): \(.status)"'
ALIGN="$(task_json "$PIPE_A" align_episode)"
ALIGNED_ID="$(echo "$ALIGN" | jq -r '.result.refs.aligned_artifact_id')"
ALIGNED_CHECKSUM="$(echo "$ALIGN" | jq -r '.result.refs.aligned_artifact_checksum')"
echo "  aligned_artifact_id=$ALIGNED_ID steps=$(echo "$ALIGN" | jq -r '.result.summary.step_count')"
check "the aligned artifact id is derived from the Episode and the artifact checksum, not random" \
  [ "$(echo "$ALIGNED_ID" | grep -c '^aligned-')" = 1 ]
ALIGNED_RECORD="$(curl -fsS "$(api_url "$API_BASE_URL" "/artifacts/$ALIGNED_ID")")"
check "it is a checksum-pinned aligned_episode_manifest scoped to the Episode's DatasetVersion" \
  [ "$(echo "$ALIGNED_RECORD" | jq -r --arg c "$ALIGNED_CHECKSUM" --arg e "$EPISODE_ID" --arg ds "$DATASET_ID" --arg v "$DATASET_VERSION" \
      '[.artifact.kind, .artifact.checksum, .artifact.ownerId, .artifact.datasetId, .artifact.datasetVersion] | join(",") == ("aligned_episode_manifest," + $c + "," + $e + "," + $ds + "," + $v)')" = true ]
check "the record carries the alignment clock and the exact source revision it consumed" \
  [ "$(echo "$ALIGNED_RECORD" | jq -r --arg m "$(echo "$EPISODE_REVISION" | jq -r '.manifestArtifactId')" '.artifact.metadata.source_artifact_id == $m and (.artifact.metadata.source_clock | length) > 0')" = true ]
check "validation passed and the profile ran on exactly that revision" \
  [ "$(task_json "$PIPE_A" validate_aligned_episode | jq -r '.result.summary.valid')" = true ] \
  && [ "$(task_json "$PIPE_A" profile_aligned_episode | jq -r '.status')" = succeeded ]
check "the canonical Episode is unchanged by alignment" \
  [ "$(episodes_json | jq -c '.episodes[0] | {manifestArtifactId, manifestChecksum}')" = "$EPISODE_REVISION" ]
echo ""

echo "=== [6/7] retry: the same recipe on the same revision converges ==="
PIPE_B="$(run_aligned_pipeline)"
assert_pipeline_succeeded "$(fetch_pipeline_run "$API_BASE_URL" "$PIPE_B")" "retry should succeed" "$API_BASE_URL" "$PIPE_B"
ALIGN_B="$(task_json "$PIPE_B" align_episode)"
check "the same aligned artifact id and checksum" \
  [ "$(echo "$ALIGN_B" | jq -r '[.result.refs.aligned_artifact_id, .result.refs.aligned_artifact_checksum] | join(",")')" = "$ALIGNED_ID,$ALIGNED_CHECKSUM" ]
echo ""

echo "=== [7/7] EXPORT_LEARNING_DATA: an explicit, pinned, reproducible export ==="
EXPORT_PARAMS="$(jq -cn --arg e "$EPISODE_ID" --arg a "$ALIGNED_ID" --arg c "$ALIGNED_CHECKSUM" \
  '{inputs: [{episode_id: $e, aligned_artifact_id: $a, aligned_artifact_checksum: $c}]}')"
EXPORT_1="$(run_job export_learning_data "$EXPORT_PARAMS")"
assert_job_succeeded "$EXPORT_1" "export_learning_data should succeed"
R1="$(echo "$EXPORT_1" | jq -c '.job.result')"
echo "  $(echo "$R1" | jq -c '{export_id, episode_count, row_counts}')"
check "one Episode exported with rows" \
  [ "$(echo "$R1" | jq -r '.episode_count == 1 and (.row_counts.learning_episodes == 1)')" = true ]
EXPORT_2="$(run_job export_learning_data "$EXPORT_PARAMS")"
assert_job_succeeded "$EXPORT_2" "re-running the export should succeed"
R2="$(echo "$EXPORT_2" | jq -c '.job.result')"
check "the same pinned input and config reproduce the same export id and manifest record" \
  [ "$(echo "$R2" | jq -r '[.export_id, .manifest_artifact_id] | join(",")')" = "$(echo "$R1" | jq -r '[.export_id, .manifest_artifact_id] | join(",")')" ]
check "the manifest ArtifactRecord id is deterministic" \
  [ "$(echo "$R1" | jq -r '.manifest_artifact_id' | grep -c '^lexport-')" = 1 ]
echo ""
echo "=== episode alignment E2E complete: robot_run_id=$ROBOT_RUN_ID dataset=$DATASET_ID/$DATASET_VERSION ==="
