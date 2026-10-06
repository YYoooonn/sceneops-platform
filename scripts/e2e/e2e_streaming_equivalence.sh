#!/usr/bin/env bash
# e2e_streaming_equivalence.sh — streaming acquisition vertical and the
# transport-preservation proof (ADR-007 §29.12, §29.19 step 9).
#
# One logical source — the LOCKED REFERENCE MCAP of one corpus fixture — reaches
# canonical Scenes and Episodes two ways, with identical build configurations:
#
#   A  batch      the reference baseline (docs/development/canonical-baseline.md):
#                 locked MCAP -> publish -> RobotRun -> Scene / Episode
#                 Consumed as it is (create-or-verify, never rebuilt): the batch
#                 arm converts nothing and reads no source dataset.
#
#   B  streaming  locked MCAP -> `reference replay` (dataset-replay container)
#                   -> ROS 2 topics -> streaming_bridge_node -> Kafka
#                   -> capture (MCAP, finalized on RUN_END, capture receipt)
#                   -> publish-pending -> reconcile --apply -> REGISTER_ROBOT_RUN
#                   -> RobotRun B -> recording_scene_building / _episode_building
#
#   then: streaming_equivalence_verify.py (recording-publisher container):
#         acquisition equivalence (§29.12) over the locked MCAP and the captured
#         MCAP, canonical equivalence via semantic_scene_content /
#         semantic_episode_content (I-35), and in-process negative controls.
#
# Because both arms start from one acquisition fixture, equality of the results
# is a statement about the transport (ROS 2 -> bridge -> Kafka -> capture ->
# publication), not about two conversions of a source dataset.
#
# The replay service mounts no raw dataset, and the run probes that none is
# visible in the container: the replay can only have read the locked MCAP.
#
# Bulk data moves between containers (the reference cache, the
# acquisition-recordings volume, DDS, Kafka); platform operations go through
# FastAPI and the production recovery commands. The host needs only Docker
# Compose, curl, jq and the API port: no uv, no PostgreSQL or MinIO access.
#
# Prerequisites:
#   make local-up                  API + workers + Postgres + MinIO from current images
#   make reference-data-bootstrap  the fixture's locked recording is prepared
#   make streaming-up              Kafka (this script starts it if absent)
#   make acquisition-image         dataset-replay + ros2 images (the make target builds both)
#   no capture / bridge / recovery container running (checked below)
#
# Usage:
#   make e2e-streaming-equivalence                       # smoke-1 (scene-0061)
#   make e2e-streaming-equivalence SCENE=scene-0103      # one named fixture
#   REFERENCE_SCOPE=smoke-1 RATE=0 scripts/e2e/e2e_streaming_equivalence.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"
source "$SCRIPT_DIR/lib.sh"

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
export API_BASE_URL ENV_FILE

# Fixture selection and the persistent batch baseline identity are the canonical
# baseline's own (scripts/canonical/baseline_lib.sh): REFERENCE_SCOPE (default
# smoke-1) or FIXTURE/SOURCE_UNIT, and BASELINE_ID (default ref-<selection>).
export FIXTURE="${FIXTURE:-${SOURCE_UNIT:-}}"
export REFERENCE_SCOPE="${REFERENCE_SCOPE:-smoke-1}"
source "$REPO_ROOT/scripts/canonical/baseline_lib.sh"
export BASELINE_ID

# Streaming arm identity: unique per execution, never shared with the baseline.
SUFFIX="$(date +%s)-$$"
START_EPOCH="$(date +%s)"
STREAM_ROBOT_ID="robot-streaming-equivalence"
STREAM_DATASET_ID="${STREAM_DATASET_ID:-test-e2e-streaming-equivalence}"
STREAM_VERSION="stream-$SUFFIX"
POLL_ATTEMPTS="${POLL_ATTEMPTS:-180}"
CAPTURE_ROOT="/recordings/capture-$SUFFIX"

# The streaming acquisition path (replay -> ROS 2 -> bridge -> Kafka -> capture ->
# publish-pending -> reconcile) is the streaming baseline's own.
source "$REPO_ROOT/scripts/streaming/streaming_lib.sh"
BRIDGE="bridge-$SUFFIX"
CAPTURE="capture-$SUFFIX"

cleanup() {
  docker rm -f "$BRIDGE" "$CAPTURE" >/dev/null 2>&1 || true
  "${COMPOSE[@]}" run --rm -T --entrypoint rm ros2 -rf "$CAPTURE_ROOT" >/dev/null 2>&1 || true
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

artifact_uri() {
  api_get "$API_BASE_URL" "/artifacts/$1" | jq -r '.artifact.uri'
}

manifest_uris() { # <scenes|episodes> <dataset-id> <version> -> JSON array of manifest URIs
  local ids id
  ids="$(api_get "$API_BASE_URL" "/$1?dataset_id=$2&dataset_version=$3&limit=500" \
    | jq -r "[.$1[].manifestArtifactId] | sort | .[]")"
  for id in $ids; do artifact_uri "$id"; done | jq -R . | jq -sc .
}

# Scene and Episode records of a DatasetVersion without their bookkeeping
# timestamps: what must stay byte-identical for a baseline this journey only reads.
records_of() { # <dataset-id> <version>
  api_get "$API_BASE_URL" "/scenes?dataset_id=$1&dataset_version=$2&limit=500" | jq -cS '[.scenes[] | del(.updatedAt)]'
  api_get "$API_BASE_URL" "/episodes?dataset_id=$1&dataset_version=$2&limit=500" | jq -cS '[.episodes[] | del(.updatedAt)]'
}

# build_scope <pipeline-type> <build-task> <register-task> <profile-task> <config>
# One pipeline run over the streamed RobotRun: the same task parameters the
# reference baseline's bootstrap uses, with the same build configuration files.
build_scope() {
  local type="$1" build_task="$2" register_task="$3" profile_task="$4" config="$5" params pipeline
  params="$(jq -cn --arg b "$build_task" --arg r "$register_task" --arg p "$profile_task" \
    --arg run "$RUN_B" --argjson config "$config" '{
      ($b): {robot_run_id: $run, build_config: $config},
      ($r): {replace: false},
      ($p): {triggered: true}}')"
  pipeline="$(run_pipeline "$API_BASE_URL" "$type" "$STREAM_DATASET_ID" "$STREAM_VERSION" "$params")"
  assert_pipeline_succeeded "$(fetch_pipeline_run "$API_BASE_URL" "$pipeline")" \
    "$type for $RUN_B should succeed" "$API_BASE_URL" "$pipeline"
}

run_state() { # <reconciliation-report-json> -> the streamed run's observed state
  jq -r --arg r "$RUN_B" '.runs[] | select(.run_id == $r) | .state' <<<"$1"
}

echo "=== [0/9] control plane, Kafka, images; no unrelated capture active ==="
require_api "$API_BASE_URL"
ACTIVE="$(docker ps --no-trunc --format '{{.Names}} {{.Command}}' \
  | grep -E 'capture/cli\.py|streaming_bridge_node|publication-recovery|registration-recovery' || true)"
[ -z "$ACTIVE" ] || fail "an unrelated capture, bridge or recovery loop is running (stop it first, e.g. make recovery-down):
$ACTIVE"
"${COMPOSE[@]}" up -d --wait kafka >/dev/null 2>&1 || fail "Kafka did not become healthy"
echo "  ✅  API healthy; Kafka up; no capture, bridge or recovery container running"
echo ""

echo "=== [1/9] the fixture: resolved through the corpus lock (no source dataset) ==="
baseline_resolve full
[ "$(echo "$BASELINE_FIXTURES" | jq 'length')" = 1 ] \
  || fail "streaming equivalence replays one fixture per run; selection ${FIXTURE:-$REFERENCE_SCOPE} resolves to $(echo "$BASELINE_FIXTURES" | jq 'length') (pass SCENE=<fixture>)"
FIXTURE_JSON="$(echo "$BASELINE_FIXTURES" | jq -c '.[0]')"
FIXTURE_ID="$(echo "$FIXTURE_JSON" | jq -r '.fixture_id')"
LOCKED_PATH="$(echo "$FIXTURE_JSON" | jq -r '.path')"
LOCKED_SHA="$(echo "$FIXTURE_JSON" | jq -r '.recording.sha256')"
LOCKED_SIZE="$(echo "$FIXTURE_JSON" | jq -r '.recording.size_bytes')"
LOCKED_COUNTS="$(echo "$FIXTURE_JSON" | jq -cS '.recording.topic_counts')"
LOCKED_MESSAGES="$(echo "$FIXTURE_JSON" | jq -r '.recording.message_count')"
RUN_A="$(baseline_run_id "$FIXTURE_ID")"
RUN_B="run-e2e-streaming-$SUFFIX-$FIXTURE_ID"
echo "$FIXTURE_JSON" | jq -c '{fixture_id, source_unit, path, sha256: .recording.sha256, message_count: .recording.message_count}'
echo "  baseline=$BASELINE_ID  A=$RUN_A  B=$RUN_B  stream dataset=$STREAM_DATASET_ID/$STREAM_VERSION"
echo ""

echo "=== [2/9] A — batch arm: the persistent reference baseline, read as it is ==="
# Reused, not rebuilt: a registered baseline RobotRun is verified read-only
# (canonical_verify); only a missing baseline is created, by the bootstrap that
# publishes this very locked MCAP. Either way the baseline RobotRun must pin
# exactly the locked recording.
if api_get "$API_BASE_URL" "/robot-runs/$RUN_A" >/dev/null 2>&1; then
  BASELINE_ACTION=reused
  BASELINE_SUMMARY="$("$REPO_ROOT/scripts/canonical/canonical_verify.sh")"
else
  BASELINE_ACTION=created
  BASELINE_SUMMARY="$("$REPO_ROOT/scripts/canonical/canonical_bootstrap.sh")"
fi
baseline_assert_recording "$RUN_A" "$LOCKED_SHA" "$LOCKED_SIZE"
RECORDS_BEFORE="$(records_of "$DATASET_ID" "$DATASET_VERSION")"
echo "$BASELINE_SUMMARY" | jq -c '{baseline_id, dataset_id, scene_count, episode_count}'
echo "  ✅  baseline $BASELINE_ID $BASELINE_ACTION; RobotRun $RUN_A pins the locked recording ($LOCKED_SHA)"
SCENES_A="$(manifest_uris scenes "$DATASET_ID" "$DATASET_VERSION")"
EPISODES_A="$(manifest_uris episodes "$DATASET_ID" "$DATASET_VERSION")"
echo ""

echo "=== [3/9] B — streaming arm: locked MCAP -> replay -> ROS 2 -> bridge -> Kafka -> capture ==="
streaming_acquire "$FIXTURE_JSON" "$RUN_B" "$STREAM_ROBOT_ID" "$CAPTURE_ROOT" "$BRIDGE" "$CAPTURE"
echo "  ✅  raw nuScenes is unavailable to the replay container (no /input/nuscenes, no /data/raw); the reference cache and corpus are mounted"
echo "$REPLAY_SOURCE" | jq -c '{fixture_id, path, sha256: .recording.sha256}'
echo "$REPLAY_SUMMARY" | jq -c '{message_count, rate, elapsed_seconds, max_lag_seconds, largest_payload_bytes}'
echo "$CAPTURE_SUMMARY" | jq -c '{path, message_count, sha256}'
echo ""

echo "=== [4/9] the locked MCAP is the replay source; nothing lost; Kafka control records ==="
check "the replay source is the locked recording (path under the read-only reference cache, locked sha256)" \
  [ "$(echo "$REPLAY_SOURCE" | jq -r --arg sha "$LOCKED_SHA" --arg f "$FIXTURE_ID" '[(.path | startswith("/reference/")), .recording.sha256 == $sha, .fixture_id == $f] | all')" = true ]
check "replay published exactly the locked recording's messages, per topic" \
  [ "$(echo "$REPLAY_SUMMARY" | jq -cS '.topic_counts')" = "$LOCKED_COUNTS" ]
check "bridge forwarded every replayed message, none failed" \
  [ "$(echo "$BRIDGE_SUMMARY" | jq -cS '[.published_by_channel, .failed]')" \
    = "$(jq -cS --argjson c "$LOCKED_COUNTS" -n '[$c, 0]')" ]
check "capture recorded every forwarded message, per channel" \
  [ "$(echo "$CAPTURE_SUMMARY" | jq -cS '.per_channel_counts')" = "$LOCKED_COUNTS" ]
RECEIPT="$("${COMPOSE[@]}" run --rm -T --entrypoint cat ros2 "$RECEIPT_PATH" </dev/null)"
check "the capture receipt: finalized by the explicit RUN_END, from Kafka, covering every locked message" \
  [ "$(echo "$RECEIPT" | jq -r --arg t "$KAFKA_TOPIC" --argjson n "$LOCKED_MESSAGES" \
      '[.finalization.reason == "explicit_run_end", .capture.source.kind == "kafka", .capture.source.topics == [$t], .message_count == $n] | all')" = true ]
# The receipt's Kafka offset range spans message_count + 2 offsets: the run's
# records on its partition are RUN_START, message_count telemetry records and
# RUN_END. The two lifecycle control records share the run's key and topic, are
# validated by capture in their own sequence space and never written to the MCAP.
KAFKA_AUDIT="$("${ROS2[@]}" python3 /workspace/scripts/e2e/streaming_kafka_audit.py \
  --robot-run-id "$RUN_B" --receipt "$RECEIPT_PATH" </dev/null)" || {
  echo "$KAFKA_AUDIT" | jq '.problems' >&2 || true
  fail "the run's Kafka records are not RUN_START + message_count telemetry + RUN_END"
}
echo "$KAFKA_AUDIT" | jq -c '{partition, records, telemetry_records, control_records, receipt_offset_span, run_alone_in_offset_span}'
check "Kafka holds exactly RUN_START, $LOCKED_MESSAGES telemetry records and RUN_END for the run (message_count + 2)" \
  [ "$(echo "$KAFKA_AUDIT" | jq -r --argjson n "$LOCKED_MESSAGES" '[.records == $n + 2, .telemetry_records == $n, ([.control_records[].control] == ["RUN_START", "RUN_END"])] | all')" = true ]
check "the receipt's offset range is RUN_START .. RUN_END" \
  [ "$(echo "$KAFKA_AUDIT" | jq -r '.problems | length')" = 0 ]
echo ""

echo "=== [5/9] receipt -> publish-pending -> reconcile --apply -> RobotRun B ==="
SCAN="$("${PUBLISHER[@]}" scan-capture --capture-root "$CAPTURE_ROOT" </dev/null)"
OBSERVED="$("${API_EXEC[@]}" "${RECONCILE[@]}" --capture-report - <<<"$SCAN")"
check "the reconciler observes the finalized capture as publish_pending (nothing in the store yet)" \
  [ "$(run_state "$OBSERVED")" = publish_pending ]
PENDING="$("${PUBLISHER[@]}" publish-pending --capture-root "$CAPTURE_ROOT" </dev/null)" || {
  echo "$PENDING" | jq . >&2 || true
  fail "publish-pending failed"
}
echo "$PENDING" | jq -c '{counts, results: [.results[] | {run_id, outcome, state_before, state_after}]}'
check "publish-pending published the run from its receipt, and verified both objects" \
  [ "$(echo "$PENDING" | jq -r --arg r "$RUN_B" '.results[] | select(.run_id == $r) | [.outcome == "published", .state_before == "publish_pending", .state_after == "published"] | all')" = true ]
SCAN="$("${PUBLISHER[@]}" scan-capture --capture-root "$CAPTURE_ROOT" </dev/null)"
RECONCILED="$("${API_EXEC[@]}" "${RECONCILE[@]}" --apply --capture-report - <<<"$SCAN")"
check "the reconciler observes registration_pending and submits REGISTER_ROBOT_RUN" \
  [ "$(echo "$RECONCILED" | jq -r --arg r "$RUN_B" '[(.runs[] | select(.run_id == $r) | .state == "registration_pending"), ([.actions[] | select(.run_id == $r and .kind == "submit_registration" and .outcome == "submitted")] | length == 1)] | all')" = true ]
for _ in $(seq 1 60); do
  api_get "$API_BASE_URL" "/robot-runs/$RUN_B" >/dev/null 2>&1 && break
  sleep 2
done
RUN_B_JSON="$(api_get "$API_BASE_URL" "/robot-runs/$RUN_B" | jq -c '.robotRun')" || fail "RobotRun $RUN_B was not registered"
RUN_A_JSON="$(api_get "$API_BASE_URL" "/robot-runs/$RUN_A" | jq -c '.robotRun')"
RUN_B_RECORDING="$(api_get "$API_BASE_URL" "/artifacts/$(echo "$RUN_B_JSON" | jq -r '.recordingArtifactId')" | jq -c '.artifact')"
check "RobotRun B: an mcap recording on the recording clock, registered from the captured bytes' receipt" \
  [ "$(echo "$RUN_B_JSON" | jq -r '[.robotId, .recordingFormat, .sourceClock] | @tsv')" = "$(printf '%s\tmcap\tmcap_log_time' "$STREAM_ROBOT_ID")" ]
check "RobotRun B pins exactly the captured recording (the receipt's checksum)" \
  [ "$(echo "$RUN_B_RECORDING" | jq -r '.checksum')" = "$(echo "$RECEIPT" | jq -r '.recording.checksum')" ]
check "RobotRun B's extent is this execution's wall-clock receive time; RobotRun A's is the source timeline, long before" \
  [ "$(jq -rn --argjson a "$RUN_A_JSON" --argjson b "$RUN_B_JSON" --argjson now "$START_EPOCH" \
      'def t: sub("\\.[0-9]+"; "") | fromdateiso8601; [($a.startedAt | t) < $now, ($b.startedAt | t) >= $now - 1] | all')" = true ]
echo ""

echo "=== [6/9] canonical Scenes + Episodes of RobotRun B, the baseline's build configurations ==="
upsert_dataset "$API_BASE_URL" "$STREAM_DATASET_ID" "Streaming Equivalence E2E" >/dev/null
upsert_dataset_version "$API_BASE_URL" "$STREAM_DATASET_ID" "$STREAM_VERSION" >/dev/null
build_scope recording_scene_building build_recording_scenes register_scenes profile_scene "$(scene_build_config)"
build_scope recording_episode_building build_recording_episodes register_episodes profile_episode "$(episode_build_config)"
SCENES_B="$(manifest_uris scenes "$STREAM_DATASET_ID" "$STREAM_VERSION")"
EPISODES_B="$(manifest_uris episodes "$STREAM_DATASET_ID" "$STREAM_VERSION")"
EXPECTED_SCENES="$(jq -r '.scenes_per_robot_run' "$BASELINE_CONFIG_DIR/baseline_shape.json")"
EXPECTED_EPISODES="$(jq -r '.episodes_per_robot_run' "$BASELINE_CONFIG_DIR/baseline_shape.json")"
check "both arms yield the baseline's shape: $EXPECTED_SCENES Scene(s) and $EXPECTED_EPISODES Episode(s) per RobotRun" \
  [ "$(jq -rn --argjson sa "$SCENES_A" --argjson sb "$SCENES_B" --argjson ea "$EPISODES_A" --argjson eb "$EPISODES_B" \
      --argjson s "$EXPECTED_SCENES" --argjson e "$EXPECTED_EPISODES" \
      '[($sa | length) == $s, ($sb | length) == $s, ($ea | length) == $e, ($eb | length) == $e] | all')" = true ]
for scene_id in $(api_get "$API_BASE_URL" "/scenes?dataset_id=$STREAM_DATASET_ID&dataset_version=$STREAM_VERSION&limit=500" | jq -r '.scenes[].sceneId'); do
  api_get "$API_BASE_URL" "/scenes/$scene_id/quality" | jq -e '.validation != null and .profile != null and .readiness == "ready"' >/dev/null \
    || fail "streamed Scene $scene_id is not validated, profiled and ready"
done
for episode_id in $(api_get "$API_BASE_URL" "/episodes?dataset_id=$STREAM_DATASET_ID&dataset_version=$STREAM_VERSION&limit=500" | jq -r '.episodes[].episodeId'); do
  api_get "$API_BASE_URL" "/episodes/$episode_id/quality" | jq -e '.validation != null and .profile != null and .readiness == "ready"' >/dev/null \
    || fail "streamed Episode $episode_id is not validated, profiled and ready"
done
echo "  ✅  every streamed Scene and Episode is validated, profiled and ready"
echo ""

echo "=== [7/9] equivalence: acquisition (§29.12) and canonical Scene / Episode content (I-35) ==="
REQUEST="$(jq -cn --arg a "$LOCKED_PATH" --arg b "$STREAM_RECORDING" --argjson counts "$LOCKED_COUNTS" \
  --argjson sa "$SCENES_A" --argjson sb "$SCENES_B" --argjson ea "$EPISODES_A" --argjson eb "$EPISODES_B" '{
    locked_topic_counts: $counts,
    batch:  {recording: $a, scene_manifest_uris: $sa, episode_manifest_uris: $ea},
    stream: {recording: $b, scene_manifest_uris: $sb, episode_manifest_uris: $eb}}')"
VERIFY="$("${COMPOSE[@]}" run --rm -T -v "$REPO_ROOT/scripts/e2e:/workspace/e2e:ro" \
  --entrypoint python recording-publisher /workspace/e2e/streaming_equivalence_verify.py <<<"$REQUEST")" || {
  echo "$VERIFY" | jq '.failures' >&2 || true
  fail "the locked recording and its streamed acquisition are not semantically equivalent"
}
echo "$VERIFY" | jq -c '{equivalent, recording_equivalence, tf_static_messages, scene, episode}'
check "acquisition: same channels, message types and encodings, per-channel payload sequences and counts" \
  [ "$(echo "$VERIFY" | jq -r '.recording_equivalence.equivalent')" = true ]
check "acquisition: same source observation times (every Header.stamp and the mission event times)" \
  [ "$(echo "$VERIFY" | jq -r '(.failures | map(select(startswith("source observation times") or startswith("mission event"))) | length) == 0 and (.source_stamp_channels | length) > 0')" = true ]
check "acquisition: /tf_static is preserved in both recordings" \
  [ "$(echo "$VERIFY" | jq -r '(.tf_static_messages | to_entries | map(.value >= 1) | all)')" = true ]
check "canonical: every Scene's and Episode's semantic content is equal across the two arms" \
  [ "$(echo "$VERIFY" | jq -r '.equivalent')" = true ]
echo ""

echo "=== [8/9] negative control: the verifier detects real, minimal semantic differences ==="
echo "$VERIFY" | jq -c '.negative_controls'
check "every control is detected (a dropped message, a 1 ns source-time shift, a changed payload checksum)" \
  [ "$(echo "$VERIFY" | jq -r '(.negative_controls | length) >= 6 and (.negative_controls | to_entries | map(.value) | all)')" = true ]
echo ""

echo "=== [9/9] the persistent batch baseline is unchanged; diagnostics ==="
BASELINE_AFTER="$("$REPO_ROOT/scripts/canonical/canonical_verify.sh")"
check "canonical-verify of $BASELINE_ID prints the same summary as before this journey" \
  [ "$BASELINE_AFTER" = "$BASELINE_SUMMARY" ]
check "the baseline's SceneRecords and EpisodeRecords are unchanged" \
  [ "$(records_of "$DATASET_ID" "$DATASET_VERSION")" = "$RECORDS_BEFORE" ]
echo "  diagnostics (not benchmarks): $(jq -cn --argjson r "$REPLAY_SUMMARY" --argjson t "$(echo "$VERIFY" | jq '.stream_recording')" \
  --arg total "$(($(date +%s) - START_EPOCH))" --arg size "$LOCKED_SIZE" --argjson cap "$CAPTURE_SUMMARY" '{
    replay_elapsed_s: $r.elapsed_seconds, replay_max_lag_s: $r.max_lag_seconds,
    locked_recording_bytes: ($size | tonumber), streamed_messages: $cap.message_count,
    stream_log_span_s: ((($t.last_log_time_ns - $t.first_log_time_ns) / 1e8 | round) / 10),
    journey_wall_s: ($total | tonumber)}')"
echo ""
echo "=== streaming equivalence E2E complete: fixture=$FIXTURE_ID A=$RUN_A ($BASELINE_ACTION) B=$RUN_B dataset=$STREAM_DATASET_ID/$STREAM_VERSION ==="
