#!/usr/bin/env bash
# streaming_bootstrap.sh — developer/test orchestration (not a Pipeline) that builds
# the persistent STREAMING reference baseline of a reference-corpus selection:
#
#   locked reference MCAP (one per fixture)
#     -> replay -> ROS 2 -> bridge -> Kafka -> capture -> receipt
#     -> publish-pending -> reconcile --apply        (ADR-008 production commands)
#     -> one streamed RobotRun per fixture
#     -> recording_scene_building / recording_episode_building (config/baselines/)
#     -> validate / profile -> verify
#
# It is the streaming counterpart of canonical-bootstrap and shares its identity,
# build configurations and verification (scripts/canonical/baseline_lib.sh); only
# how the RobotRun's recording is acquired differs. The only replay input is the
# locked reference corpus: no raw dataset is read or mounted.
#
# Selection: REFERENCE_SCOPE (default smoke-1; nuscenes-mini-full-10) or FIXTURE (one
# fixture) chooses which fixtures are acted on. Identity: the golden reference contract's
# streaming_acquisition baseline (`stream-ref-nuscenes-mini-full-10`) whatever the
# selection (smoke-1 is scene-0061 of that baseline, not another one), so it never shares
# a robot, RobotRun or DatasetVersion with the Recording Import baseline. A different
# BASELINE_ID names non-contract streamed RobotRuns and needs a disposable runtime
# (DISPOSABLE_RUNTIME=1). One fixture is one streamed RobotRun
# (run-<BASELINE_ID>-<fixture>), one whole-recording Scene and one Episode.
# RATE overrides every fixture's replay rate.
#
# create-or-verify, per fixture, from the platform's durable state:
#   RobotRun registered, Scene/Episode built and ready   -> reused; nothing replayed
#   RobotRun registered, Scene/Episode missing or unready -> built (converges)
#   capture finalized / published / registration pending -> publish-pending and/or
#                                                           reconcile --apply: never replayed again
#   capture unfinished (interrupted)                     -> resumed by re-running the capture
#                                                           from Kafka's committed offsets
#   nothing                                              -> replayed (streamed)
#   any other reconciler state                           -> fails and names it
# A registered RobotRun is never replayed over. Once a run's recording is
# registered and byte-identical to its capture, the transient capture files
# are removed; the durable state is in the ArtifactStore and PostgreSQL.
#
# Disk: each fixture needs headroom for its capture, its Kafka records and its
# artifacts. The bootstrap stops before a fixture when the host or the Docker VM
# has less than MIN_FREE_GIB (default 6) free.
#
# stdout: one JSON summary (streaming_verify), deterministic for an unchanged
# baseline. Progress and per-fixture diagnostics (`streaming_fixture {json}`,
# `streaming_storage {json}`) go to stderr.
#
# Prerequisites: `make local-up`; the reference recordings prepared by
# `make reference-data-bootstrap REFERENCE_SCOPE=<scope>`; the dataset-acquisition,
# dataset-replay and ros2 images; no unrelated capture, bridge or recovery loop.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"
source "$REPO_ROOT/scripts/e2e/lib.sh"
BASELINE_MODE=streaming_acquisition
source "$REPO_ROOT/scripts/canonical/baseline_lib.sh"

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
export API_BASE_URL ENV_FILE
# A Scene build has been seen to take ~15 minutes: allow twice that before giving up.
POLL_ATTEMPTS="${POLL_ATTEMPTS:-360}"
MIN_FREE_GIB="${MIN_FREE_GIB:-6}"
STALL_REPORT_SECONDS="${STALL_REPORT_SECONDS:-240}"

source "$SCRIPT_DIR/streaming_lib.sh"

BRIDGE="bridge-$BASELINE_ID"
CAPTURE="capture-$BASELINE_ID"
cleanup() { docker rm -f "$BRIDGE" "$CAPTURE" >/dev/null 2>&1 || true; }
trap cleanup EXIT

fail() {
  echo "❌ $*" >&2
  for c in "$BRIDGE" "$CAPTURE"; do
    if docker inspect "$c" >/dev/null 2>&1; then
      echo "--- last log lines: $c" >&2
      docker logs --tail 15 "$c" 2>&1 | cut -c1-400 >&2 || true
    fi
  done
  exit 1
}

# stall_watch_start <label> / stall_watch_stop: if a pipeline takes longer than
# STALL_REPORT_SECONDS, record what the workers are doing while it is still running.
STALL_WATCH_PID=""
stall_watch_start() {
  (
    sleep "$STALL_REPORT_SECONDS"
    log "pipeline_slow $(jq -cn --arg l "$1" --arg t "$(date -u +%FT%TZ)" --argjson s "$STALL_REPORT_SECONDS" \
      '{label: $l, still_running_after_seconds: $s, at: $t}')"
    log "pipeline_slow_stats $(docker stats --no-stream --format '{{json .}}' | jq -sc '[.[] | {name: .Name, cpu: .CPUPerc, mem: .MemUsage}]')"
    for svc in worker-pipeline worker-jobs; do
      log "pipeline_slow_log $svc: $("${COMPOSE[@]}" logs --no-color --tail 15 "$svc" 2>&1 | cut -c1-300 | tr '\n' '|')"
    done
  ) &
  STALL_WATCH_PID=$!
}
stall_watch_stop() {
  [ -z "$STALL_WATCH_PID" ] || { kill "$STALL_WATCH_PID" 2>/dev/null || true; wait "$STALL_WATCH_PID" 2>/dev/null || true; }
  STALL_WATCH_PID=""
}

# disk_guard — stop before a fixture if the host or the Docker VM is short of space.
disk_guard() {
  local storage host vm
  storage="$(streaming_storage)"
  log "streaming_storage $storage"
  host="$(echo "$storage" | jq -r '.host_free_gib')"
  vm="$(echo "$storage" | jq -r '.docker_vm_free_gib')"
  awk -v h="$host" -v v="$vm" -v m="$MIN_FREE_GIB" 'BEGIN { exit !(h >= m && v >= m) }' \
    || fail "stopping before the next fixture: ${host} GiB free on the host, ${vm} GiB in the Docker VM; MIN_FREE_GIB=$MIN_FREE_GIB (the baseline is resumable: re-run once space is freed)"
}

# transport_checks <resolved-fixture-json> — the streamed capture carries exactly the
# locked recording's messages, per channel, and finalized on the run's RUN_END.
transport_checks() {
  local fixture="$1" locked_counts locked_messages receipt
  locked_counts="$(echo "$fixture" | jq -cS '.recording.topic_counts')"
  locked_messages="$(echo "$fixture" | jq -r '.recording.message_count')"
  streaming_check "replay source is the locked recording (read-only reference cache, locked sha256)" \
    [ "$(echo "$REPLAY_SOURCE" | jq -r --argjson f "$fixture" \
        '[(.path | startswith("/reference/")), .recording.sha256 == $f.recording.sha256, .fixture_id == $f.fixture_id] | all')" = true ]
  streaming_check "replay published $locked_messages messages, per topic as locked" \
    [ "$(echo "$REPLAY_SUMMARY" | jq -cS '[.message_count, .topic_counts]')" = "$(jq -cSn --argjson c "$locked_counts" --argjson n "$locked_messages" '[$n, $c]')" ]
  streaming_check "bridge forwarded every message, none failed" \
    [ "$(echo "$BRIDGE_SUMMARY" | jq -cS '[.published_by_channel, .failed]')" = "$(jq -cSn --argjson c "$locked_counts" '[$c, 0]')" ]
  streaming_check "capture recorded every forwarded message, per channel" \
    [ "$(echo "$CAPTURE_SUMMARY" | jq -cS '[.message_count, .per_channel_counts]')" = "$(jq -cSn --argjson c "$locked_counts" --argjson n "$locked_messages" '[$n, $c]')" ]
  receipt="$(streaming_capture_receipt "$RECEIPT_PATH")"
  streaming_check "capture receipt: finalized by RUN_END, from Kafka, per-channel counts as locked, offsets RUN_START..RUN_END" \
    [ "$(echo "$receipt" | jq -r --arg t "$KAFKA_TOPIC" --argjson n "$locked_messages" --argjson c "$locked_counts" \
        '[.finalization.reason == "explicit_run_end", .capture.source.kind == "kafka", .capture.source.topics == [$t],
          .message_count == $n, (.per_channel_counts | to_entries | sort_by(.key) | from_entries) == $c,
          (.kafka.last_offset - .kafka.first_offset + 1) == $n + 2] | all')" = true ]
}

# acquire_fixture <resolved-fixture-json> <run-id> <capture-root> — streamed from nothing.
acquire_fixture() {
  streaming_acquire "$1" "$2" "$ROBOT_ID" "$3" "$BRIDGE" "$CAPTURE"
  transport_checks "$1"
}

# resume_capture <run-id> <capture-root> — an interrupted capture: Kafka is the source
# of truth, and a capture resumes from its group's committed offsets. Nothing is
# replayed: it finalizes only if the run's RUN_END is already in the topic, and
# otherwise aborts (a recording that merely stopped arriving is never finalized).
resume_capture() {
  local run_id="$1" capture_root="$2" summary
  docker rm -f "$CAPTURE" >/dev/null 2>&1 || true
  "${COMPOSE[@]}" run -d --name "$CAPTURE" -T ros2 python3 /workspace/capture/cli.py \
    --robot-id "$ROBOT_ID" --robot-run-id "$run_id" --channels-file "$CHANNELS_FILE" \
    --output-root "$capture_root" --idle-timeout-seconds 60 >/dev/null
  [ "$(docker wait "$CAPTURE")" = 0 ] || fail "the resumed capture of $run_id failed (its RUN_END may never have reached Kafka: remove the capture and the run's partial state by hand)"
  summary="$(summary_line "$(docker logs "$CAPTURE" 2>&1)" capture_summary)"
  log "  ✅  capture of $run_id resumed from Kafka: $(echo "$summary" | jq -c '{message_count, sha256}')"
}

process_fixture() {
  local fixture="$1" id run_id capture_root action t0 t1 t2 t3 t4 t5 state scan diag
  local replay_s=null capture_s=null
  id="$(echo "$fixture" | jq -r '.fixture_id')"
  run_id="$(baseline_run_id "$id")"
  capture_root="/recordings/capture-$BASELINE_ID-$id"
  t0="$(now)"
  action=reused
  if ! api_get "$API_BASE_URL" "/robot-runs/$run_id" >/dev/null 2>&1; then
    disk_guard
    "${COMPOSE[@]}" run --rm -T --entrypoint mkdir ros2 -p "$capture_root" </dev/null
    scan="$(streaming_scan "$capture_root")"
    state="$(streaming_run_state "$run_id" "$scan")"
    log "--- $id: $run_id is $state"
    case "$state" in
      absent)
        action=streamed
        acquire_fixture "$fixture" "$run_id" "$capture_root"
        replay_s="$ACQ_REPLAY_S"
        capture_s="$ACQ_CAPTURE_S"
        ;;
      capture_unfinished)
        action=capture_resumed
        resume_capture "$run_id" "$capture_root"
        ;;
      *) action=recovered ;;
    esac
    t1="$(now)"
    streaming_publish_and_register "$run_id" "$capture_root"
    t2="$(now)"
    streaming_release_capture "$run_id" "$capture_root"
    log "  ✅  RobotRun $run_id registered ($action)"
  else
    log "--- $id: RobotRun $run_id is registered: reused, not replayed"
    t1="$t0"
    t2="$t0"
  fi

  t3="$(now)"
  if baseline_fixture_complete "$run_id"; then
    log "  Scene and Episode of $run_id are built and ready: reused"
    t4="$t3"
    t5="$t3"
  else
    stall_watch_start "recording_scene_building $run_id"
    baseline_build_scope recording_scene_building build_recording_scenes register_scenes profile_scene \
      "$run_id" "$(scene_build_config)"
    stall_watch_stop
    t4="$(now)"
    stall_watch_start "recording_episode_building $run_id"
    baseline_build_scope recording_episode_building build_recording_episodes register_episodes profile_episode \
      "$run_id" "$(episode_build_config)"
    stall_watch_stop
    t5="$(now)"
  fi
  diag="$(jq -cn --arg id "$id" --arg run "$run_id" --arg action "$action" \
    --argjson replay "$replay_s" --argjson capture "$capture_s" \
    --argjson reg "$(seconds_between "$t1" "$t2")" \
    --argjson scene "$(seconds_between "$t3" "$t4")" --argjson episode "$(seconds_between "$t4" "$t5")" \
    '{fixture_id: $id, robot_run_id: $run, action: $action, replay_s: $replay, capture_s: $capture,
      publish_register_s: $reg, scene_build_s: $scene, episode_build_s: $episode}')"
  log "streaming_fixture $diag"
}

log "=== streaming baseline '$BASELINE_ID': $DATASET_ID/$DATASET_VERSION from $REFERENCE_CORPUS (${FIXTURE:-$REFERENCE_SCOPE}) ==="
require_api "$API_BASE_URL"
baseline_guard_identity
log "--- reference corpus: locked facts of the selection"
baseline_resolve lock-only
# Kafka, the capture containers and the cached recordings matter only while a
# RobotRun has still to be streamed; a baseline whose RobotRuns are all registered
# is checked and, if needed, built into, without touching any of them.
ACQUIRING=0
if ! baseline_all_registered; then
  ACQUIRING=1
  ACTIVE="$(docker ps --no-trunc --format '{{.Names}} {{.Command}}' \
    | grep -E 'capture/cli\.py|streaming_bridge_node|publication-recovery|registration-recovery' || true)"
  [ -z "$ACTIVE" ] || fail "an unrelated capture, bridge or recovery loop is running (stop it first, e.g. make recovery-down):
$ACTIVE"
  "${COMPOSE[@]}" up -d --wait kafka >/dev/null 2>&1 || fail "Kafka did not become healthy"
  log "  Kafka up; no capture, bridge or recovery container running"
  log "streaming_storage $(streaming_storage)"
  log "--- a RobotRun has to be streamed: cached recordings verified against the lock"
  baseline_resolve full
fi
log "  $(echo "$BASELINE_FIXTURES" | jq -c '[.[].fixture_id]')"

baseline_ensure_dataset "Streaming baseline $BASELINE_ID"

# Collected into an array first: a docker container inside a `while read` loop would
# consume the remaining fixtures from stdin. (No mapfile: the host bash may be 3.2.)
fixtures=()
while IFS= read -r fixture; do fixtures+=("$fixture"); done < <(echo "$BASELINE_FIXTURES" | jq -c '.[]')
for fixture in "${fixtures[@]}"; do
  process_fixture "$fixture"
  [ "$ACQUIRING" = 0 ] || log "streaming_storage $(streaming_storage)"
done

log "--- verify"
streaming_verify "$API_BASE_URL"
log "=== streaming baseline '$BASELINE_ID' ready ==="
