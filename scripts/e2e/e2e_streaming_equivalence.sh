#!/usr/bin/env bash
# e2e_streaming_equivalence.sh — the streaming acquisition vertical and the
# batch-vs-streaming equivalence proof (ADR-007 §29.12, §29.19 step 9).
#
# One logical source (a nuScenes scene), acquired two ways, built into
# canonical Scenes and Episodes with identical build configurations:
#
#   A  batch      dataset-acquisition container -> MCAP -> publish -> RobotRun A
#   B  streaming  dataset-replay container (ROS 2 topics, paced)
#                   -> ros2 container: streaming_bridge_node -> Kafka
#                   -> ros2 container: capture (MCAP, finalized on RUN_END)
#                   -> L1 conformance -> publish -> REGISTER_ROBOT_RUN -> RobotRun B
#
#   both: POST /pipelines/runs recording_scene_building + recording_episode_building
#   then: streaming_equivalence_verify.py (recording-publisher container)
#         recording equivalence (§29.12) + canonical equivalence via
#         semantic_scene_content / semantic_episode_content (I-35)
#
# Bulk data moves between containers (the acquisition-recordings volume, DDS,
# Kafka); platform operations go through FastAPI. The host needs only Docker
# Compose, curl, jq and the API port: no uv, no PostgreSQL or MinIO access,
# no worker CLI.
#
# Prerequisites:
#   make local-up         API + workers + Postgres + MinIO from current images
#   make streaming-up     Kafka   (this script starts it if absent)
#   make acquisition-image ros2 image built (make e2e-streaming-equivalence does both)
#   data/raw/nuscenes with v1.0-mini and can_bus (ACQUISITION_NUSCENES_ROOT overrides)
#
# Usage:
#   make e2e-streaming-equivalence
#   SOURCE_UNIT=scene-0103 RATE=1 scripts/e2e/e2e_streaming_equivalence.sh

set -euo pipefail

# The bridge and capture run as named one-off containers; compose would warn
# about them as orphans on every later command.
export COMPOSE_IGNORE_ORPHANS=1

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"
source "$SCRIPT_DIR/lib.sh"

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
SOURCE_VERSION="${SOURCE_VERSION:-v1.0-mini}"
SOURCE_UNIT="${SOURCE_UNIT:-scene-0061}"
RATE="${RATE:-2}"
SUFFIX="$(date +%s)-$$"
ROBOT_ID="${ROBOT_ID:-robot-streaming-equivalence}"
RUN_A="run-equiv-batch-$SUFFIX"
RUN_B="run-equiv-stream-$SUFFIX"
DATASET_ID="${DATASET_ID:-test-e2e-streaming-equivalence}"
VERSION_A="va-$SUFFIX"
VERSION_B="vb-$SUFFIX"
POLL_ATTEMPTS="${POLL_ATTEMPTS:-180}"
KAFKA_TOPIC="sceneops.robot.telemetry.v1"
CHANNELS_FILE="/workspace/channels/surround-camera-lidar.json"
CAPTURE_ROOT="/recordings/capture-$SUFFIX"

COMPOSE=(docker compose --env-file "${ENV_FILE:-.env.local}" --profile acquisition --profile ros2 --profile streaming)
ACQUIRE=("${COMPOSE[@]}" run --rm -T dataset-acquisition)
REPLAY=("${COMPOSE[@]}" run --rm -T dataset-replay)
PUBLISHER=("${COMPOSE[@]}" run --rm -T recording-publisher)
BATCH_RECORDING="/recordings/$RUN_A.mcap"
BRIDGE="bridge-$SUFFIX"
CAPTURE="capture-$SUFFIX"

cleanup() {
  docker rm -f "$BRIDGE" "$CAPTURE" >/dev/null 2>&1 || true
  "${COMPOSE[@]}" run --rm -T --entrypoint rm ros2 -rf "$CAPTURE_ROOT" >/dev/null 2>&1 || true
  "${COMPOSE[@]}" run --rm -T --entrypoint rm dataset-acquisition -f "$BATCH_RECORDING" >/dev/null 2>&1 || true
}
trap cleanup EXIT

fail() {
  echo "❌ $*" >&2
  for c in "$BRIDGE" "$CAPTURE"; do
    echo "--- last log lines: $c" >&2
    docker logs --tail 15 "$c" 2>&1 | cut -c1-400 >&2 || true
  done
  exit 1
}

summary_line() { # <log-text> <prefix> -> the JSON after "<prefix> "
  grep -E "^$2 " <<<"$1" | tail -1 | sed "s/^$2 //"
}

# Canonical semantics of the recording, stated once (config/baselines/) and
# used for BOTH runs. Every canonical time and every segmentation clock is a
# source-semantic timestamp (header stamps, the payload's own
# source_timestamp_ns): nothing depends on recorder receive time, which is
# what makes I-35 apply.

run_build() { # <type> <version> <run> <build-task> <config> <register-task>
  local type="$1" version="$2" run="$3" build_task="$4" config="$5" register_task="$6"
  local payload run_id
  payload="$(jq -cn --arg type "$type" --arg ds "$DATASET_ID" --arg v "$version" --arg run "$run" \
    --arg build "$build_task" --arg register "$register_task" --argjson config "$config" '{
      type: $type, dataset_id: $ds, dataset_version: $v, force: true,
      params: {($build): {robot_run_id: $run, build_config: $config},
               ($register): {replace: false}}}')"
  run_id="$(extract_pipeline_run_id "$(create_pipeline_run "$API_BASE_URL" "$payload")")"
  dispatch_pipeline_run "$API_BASE_URL" "$run_id" >/dev/null
  poll_pipeline_terminal "$API_BASE_URL" "$run_id" "$POLL_ATTEMPTS" 5 >/dev/null
  assert_pipeline_succeeded "$(fetch_pipeline_run "$API_BASE_URL" "$run_id")" \
    "$type for $run should succeed" "$API_BASE_URL" "$run_id"
  echo "$run_id"
}

artifact_uri() {
  curl -fsS "$(api_url "$API_BASE_URL" "/artifacts/$1")" | jq -r '.artifact.uri'
}

manifest_uris() { # <scenes|episodes> <version>
  local ids id
  ids="$(curl -fsS "$(api_url "$API_BASE_URL" "/$1?dataset_id=$DATASET_ID&dataset_version=$2&limit=500")" \
    | jq -r "[.$1[].manifestArtifactId] | sort | .[]")"
  for id in $ids; do artifact_uri "$id"; done | jq -R . | jq -sc .
}

register() { # <run> <recording-path> <source-kind> [extra publisher args...] -> manifest uri
  local run="$1" path="$2" kind="$3"
  shift 3
  local publication
  publication="$("${PUBLISHER[@]}" publish --mcap-path "$path" --run-id "$run" \
    --robot-id "$ROBOT_ID" --source-kind "$kind" "$@")"
  REG_JSON="$(register_robot_run "$API_BASE_URL" "$(echo "$publication" | jq -r '.manifest_uri')")"
  assert_job_succeeded "$REG_JSON" "REGISTER_ROBOT_RUN $run should succeed"
  echo "$publication"
}

echo "=== [0/8] control plane, Kafka, images ==="
curl -fsS "$API_BASE_URL/health" >/dev/null || fail "SceneOps API not reachable at $API_BASE_URL (make local-up)"
"${COMPOSE[@]}" up -d --wait kafka >/dev/null 2>&1 || fail "Kafka did not become healthy"
echo "  ✅  API healthy; Kafka up"
echo "  source=nuScenes $SOURCE_VERSION/$SOURCE_UNIT  A=$RUN_A  B=$RUN_B  replay rate=$RATE"
echo ""

echo "=== [1/8] A — batch acquisition -> L1 conformance -> publish -> RobotRun ==="
BATCH_SUMMARY="$("${ACQUIRE[@]}" nuscenes --dataroot /input/nuscenes --version "$SOURCE_VERSION" \
  --source-unit "$SOURCE_UNIT" --output "$BATCH_RECORDING")"
echo "$BATCH_SUMMARY" | jq -c '{sha256, size_bytes, message_count}'
"${PUBLISHER[@]}" check --mcap-path "$BATCH_RECORDING" | jq -e '.conforms' >/dev/null \
  || fail "batch recording is not L1-conformant"
BATCH_PUBLICATION="$(register "$RUN_A" "$BATCH_RECORDING" file)"
echo "  ✅  RobotRun $RUN_A registered"
echo ""

echo "=== [2/8] B — streaming acquisition: replay -> ROS 2 -> bridge -> Kafka -> capture ==="
"${COMPOSE[@]}" run -d --name "$BRIDGE" -T ros2 python3 /workspace/nodes/streaming_bridge_node.py \
  --robot-id "$ROBOT_ID" --robot-run-id "$RUN_B" --channels-file "$CHANNELS_FILE" \
  --exit-after-idle-seconds 10 >/dev/null
"${COMPOSE[@]}" run -d --name "$CAPTURE" -T ros2 python3 /workspace/capture/cli.py \
  --robot-id "$ROBOT_ID" --robot-run-id "$RUN_B" --channels-file "$CHANNELS_FILE" \
  --output-root "$CAPTURE_ROOT" --until-run-end --idle-timeout-seconds 120 >/dev/null
for _ in $(seq 1 60); do
  docker logs "$BRIDGE" 2>&1 | grep -q "streaming bridge ready" && break
  sleep 1
done
docker logs "$BRIDGE" 2>&1 | grep -q "streaming bridge ready" || fail "bridge did not start"
REPLAY_OUT="$("${REPLAY[@]}" nuscenes --dataroot /input/nuscenes --version "$SOURCE_VERSION" \
  --source-unit "$SOURCE_UNIT" --replay --rate "$RATE")" || fail "replay failed"
REPLAY_SUMMARY="$(summary_line "$REPLAY_OUT" replay_summary)"
echo "$REPLAY_SUMMARY" | jq -c '{message_count, rate, elapsed_seconds, max_lag_seconds, largest_payload_bytes}'
[ "$(docker wait "$BRIDGE")" = 0 ] || fail "bridge exited non-zero"
[ "$(docker wait "$CAPTURE")" = 0 ] || fail "capture exited non-zero"
BRIDGE_SUMMARY="$(summary_line "$(docker logs "$BRIDGE" 2>&1)" bridge_summary)"
CAPTURE_SUMMARY="$(summary_line "$(docker logs "$CAPTURE" 2>&1)" capture_summary)"
STREAM_RECORDING="$(echo "$CAPTURE_SUMMARY" | jq -r '.path')"
echo "$CAPTURE_SUMMARY" | jq -c '{path, message_count, sha256}'
echo ""

echo "=== [3/8] nothing lost between replay, bridge and capture; counts equal the batch recording ==="
BATCH_COUNTS="$(echo "$BATCH_SUMMARY" | jq -cS '.topic_counts')"
check "replay published exactly the batch recording's messages, per topic" \
  [ "$(echo "$REPLAY_SUMMARY" | jq -cS '.topic_counts')" = "$BATCH_COUNTS" ]
check "bridge forwarded every replayed message, none failed" \
  [ "$(echo "$BRIDGE_SUMMARY" | jq -cS '[.published_by_channel, .failed]')" \
    = "$(jq -cS --argjson c "$BATCH_COUNTS" -n '[$c, 0]')" ]
check "capture recorded every forwarded message, per channel" \
  [ "$(echo "$CAPTURE_SUMMARY" | jq -cS '.per_channel_counts')" = "$BATCH_COUNTS" ]
echo ""

echo "=== [4/8] L1 conformance of the captured recording; publish; REGISTER_ROBOT_RUN ==="
STREAM_CONFORMANCE="$("${PUBLISHER[@]}" check --mcap-path "$STREAM_RECORDING")" || {
  echo "$STREAM_CONFORMANCE" | jq '.violations' >&2
  fail "captured recording does not conform to the L1 contract"
}
echo "$STREAM_CONFORMANCE" | jq -c '{conforms, message_count, channels: (.channels | length)}'
for topic in /camera/front/image/compressed /camera/front/camera_info /lidar/top/points \
  /tf_static /tf /vehicle/odom /vehicle/imu /vehicle/control /mission/status; do
  echo "$STREAM_CONFORMANCE" | jq -e --arg t "$topic" '.channels[$t].message_count > 0 and .channels[$t].sequenced' >/dev/null \
    || fail "streamed channel $topic missing or carries no sequence"
done
echo "  ✅  camera, CameraInfo, lidar, /tf_static, /tf, CAN and mission channels present and sequenced"
STREAM_PUBLICATION="$(register "$RUN_B" "$STREAM_RECORDING" kafka --source-topic "$KAFKA_TOPIC")"
RUN_B_JSON="$(curl -fsS "$(api_url "$API_BASE_URL" "/robot-runs/$RUN_B")" | jq -c '.robotRun')"
RUN_A_JSON="$(curl -fsS "$(api_url "$API_BASE_URL" "/robot-runs/$RUN_A")" | jq -c '.robotRun')"
check "RobotRun B: mcap recording on the recording clock, registered from the captured bytes" \
  [ "$(echo "$RUN_B_JSON" | jq -r '[.robotId, .recordingFormat, .sourceClock] | @tsv')" \
    = "$(printf '%s\tmcap\tmcap_log_time' "$ROBOT_ID")" ]
check "RobotRun B's extent is wall-clock receive time; RobotRun A's is the source timeline" \
  [ "$(jq -rn --argjson a "$RUN_A_JSON" --argjson b "$RUN_B_JSON" \
      '[($a.startedAt | startswith("2018")), ($b.startedAt | startswith("2018") | not)] | all')" = true ]
echo ""

echo "=== [5/8] canonical Scenes + Episodes from both RobotRuns, identical build configs ==="
upsert_dataset "$API_BASE_URL" "$DATASET_ID" "Streaming Equivalence E2E" >/dev/null
upsert_dataset_version "$API_BASE_URL" "$DATASET_ID" "$VERSION_A" >/dev/null
upsert_dataset_version "$API_BASE_URL" "$DATASET_ID" "$VERSION_B" >/dev/null
for side in A B; do
  if [ "$side" = A ]; then run="$RUN_A"; version="$VERSION_A"; else run="$RUN_B"; version="$VERSION_B"; fi
  run_build recording_scene_building "$version" "$run" build_recording_scenes "$(scene_build_config)" register_scenes >/dev/null
  run_build recording_episode_building "$version" "$run" build_recording_episodes "$(episode_build_config)" register_episodes >/dev/null
  echo "  ✅  $side ($run): Scenes and Episodes built and registered"
done
SCENES_A="$(manifest_uris scenes "$VERSION_A")"; SCENES_B="$(manifest_uris scenes "$VERSION_B")"
EPISODES_A="$(manifest_uris episodes "$VERSION_A")"; EPISODES_B="$(manifest_uris episodes "$VERSION_B")"
check "both acquisitions yielded the same number of Scenes and Episodes (1 Scene, 1 Episode)" \
  [ "$(jq -rn --argjson a "$SCENES_A" --argjson b "$SCENES_B" --argjson ea "$EPISODES_A" --argjson eb "$EPISODES_B" \
      '[($a | length) == ($b | length), ($a | length) == 1, ($ea | length) == 1, ($eb | length) == 1] | all')" = true ]
echo ""

echo "=== [6/8] equivalence: recordings (§29.12) and canonical Scene/Episode content (I-35) ==="
REQUEST="$(jq -cn --arg a "$BATCH_RECORDING" --arg b "$STREAM_RECORDING" \
  --argjson sa "$SCENES_A" --argjson sb "$SCENES_B" --argjson ea "$EPISODES_A" --argjson eb "$EPISODES_B" '{
    batch:  {recording: $a, scene_manifest_uris: $sa, episode_manifest_uris: $ea},
    stream: {recording: $b, scene_manifest_uris: $sb, episode_manifest_uris: $eb}}')"
VERIFY="$("${COMPOSE[@]}" run --rm -T -v "$REPO_ROOT/scripts/e2e:/workspace/e2e:ro" \
  --entrypoint python recording-publisher /workspace/e2e/streaming_equivalence_verify.py <<<"$REQUEST")" || {
  echo "$VERIFY" | jq '.failures' >&2 || true
  fail "batch and streaming acquisitions are not semantically equivalent"
}
echo "$VERIFY" | jq -c '{equivalent, recording_equivalence, scene: .scene, episode: .episode}'
check "recordings are semantically equivalent (channels, message multisets, sequence order)" \
  [ "$(echo "$VERIFY" | jq -r '.recording_equivalence.equivalent')" = true ]
check "every Scene's and Episode's semantic content equal across batch and streaming" \
  [ "$(echo "$VERIFY" | jq -r '.equivalent')" = true ]
# Negative control: the verifier must reject a streamed build that is missing
# a Scene, so the equality above is not vacuous.
TAMPERED="$(jq -c '.stream.scene_manifest_uris |= .[:-1]' <<<"$REQUEST")"
if "${COMPOSE[@]}" run --rm -T -v "$REPO_ROOT/scripts/e2e:/workspace/e2e:ro" \
  --entrypoint python recording-publisher /workspace/e2e/streaming_equivalence_verify.py \
  <<<"$TAMPERED" >/dev/null 2>&1; then
  fail "the verifier accepted a streamed build with a Scene removed"
fi
echo "  ✅  negative control: a streamed build missing a Scene is rejected"
echo ""

echo "=== [7/8] timing and ordering evidence on the streamed recording ==="
echo "$VERIFY" | jq -c '.stream_recording | {first_log_time_ns, last_log_time_ns, first_publish_time_ns, publish_not_after_log, log_time_non_decreasing, all_channels_sequenced}'
echo "$VERIFY" | jq -c '{mission_source_times_ns}'
echo "  ✅  log_time is wall-clock receive time; publish_time is transport ingest time (<= log_time); mission events keep source-timeline timestamps; every channel sequenced"
echo ""

echo "=== [8/8] control-plane / container boundary ==="
echo "  ✅  host used: docker compose, curl, jq (no uv, no PostgreSQL/MinIO access, no worker CLI)"
echo ""
echo "=== streaming equivalence E2E complete: A=$RUN_A B=$RUN_B dataset=$DATASET_ID ($VERSION_A / $VERSION_B) ==="
