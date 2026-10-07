#!/usr/bin/env bash
# streaming_lib.sh — the streaming acquisition path of a locked reference
# fixture, used by the streaming baseline (streaming_bootstrap.sh /
# streaming_verify.sh):
#
#   locked reference MCAP
#     -> `reference replay` (dataset-replay container, no raw dataset mounted)
#     -> ROS 2 topics -> streaming_bridge_node -> Kafka
#     -> capture (finalizes only on RUN_END; MCAP + capture receipt on the recordings volume)
#     -> publish-pending -> reconcile --apply -> REGISTER_ROBOT_RUN -> RobotRun
#
# Publication and registration are the production operational commands of ADR-008
# (the publisher CLI and the API's reconciler); nothing here writes PostgreSQL or
# MinIO. The host needs Docker Compose, curl, jq and the API port.
#
# Sourced after scripts/e2e/lib.sh (and, for the baseline, after
# scripts/canonical/baseline_lib.sh) by a script that has set REPO_ROOT, ENV_FILE
# and API_BASE_URL. The sourcing script owns `fail` (and may extend it to print
# the logs of its containers).

# The bridge and capture run as named one-off containers; compose would warn
# about them as orphans on every later command.
export COMPOSE_IGNORE_ORPHANS=1

KAFKA_TOPIC="${KAFKA_TOPIC:-sceneops.robot.telemetry.v1}"
CHANNELS_FILE="${CHANNELS_FILE:-/workspace/channels/surround-camera-lidar.json}"
RATE="${RATE:-}" # empty: the fixture's replay definition

COMPOSE=(docker compose --env-file "$ENV_FILE" --profile acquisition --profile ros2 --profile streaming)
# The replay service mounts no source dataset: it can read only the locked recording.
REPLAY=("${COMPOSE[@]}" run --rm -T dataset-replay)
REPLAY_PROBE=("${COMPOSE[@]}" run --rm -T --entrypoint sh dataset-replay)
PUBLISHER=("${COMPOSE[@]}" run --rm -T recording-publisher)
ROS2=("${COMPOSE[@]}" run --rm -T ros2)
API_EXEC=("${COMPOSE[@]}" exec -T api)
RECONCILE=(python -m app.domains.robots.reconciliation --once)

# ── Small helpers ────────────────────────────────────────────────────────────

now() { perl -MTime::HiRes=time -e 'printf "%.3f\n", time'; }

seconds_between() { awk -v a="$1" -v b="$2" 'BEGIN { printf "%.1f", b - a }'; }

log() { echo "$@" >&2; }

# streaming_check <label> <command...> — run the command; a ✅ line on stderr
# (stdout is reserved for a script's JSON) or fail.
streaming_check() {
  local label="$1"
  shift
  if "$@"; then
    log "  ✅  $label"
  else
    fail "$label"
  fi
}

summary_line() { # <log-text> <prefix> -> the JSON after "<prefix> "
  grep -E "^$2 " <<<"$1" | tail -1 | sed "s/^$2 //"
}

# container_seconds <container> -> how long the container ran, from Docker's own clock
container_seconds() {
  docker inspect "$1" | jq -r '.[0].State | [.StartedAt, .FinishedAt]
    | map((sub("\\.[0-9]+Z$"; "Z") | fromdateiso8601)
          + ((capture("\\.(?<f>[0-9]+)Z$").f // "0") | ("0." + .) | tonumber))
    | (.[1] - .[0] | . * 10 | round / 10)'
}

# ── Streaming acquisition ────────────────────────────────────────────────────

# The replay container must see the locked recording and no raw dataset.
streaming_assert_replay_isolated() {
  local probe
  probe="$("${REPLAY_PROBE[@]}" -c 'for p in /input/nuscenes /data/raw; do [ -e "$p" ] && echo "visible:$p"; done; [ -r /reference ] && [ -r /config/reference ] && echo ok' </dev/null)"
  [ "$(echo "$probe" | tr -d '[:space:]')" = ok ] \
    || fail "the replay container sees a raw dataset or lacks the reference mounts: $probe"
}

# streaming_acquire <resolved-fixture-json> <run-id> <robot-id> <capture-root> <bridge-container> <capture-container>
#
# Replays the fixture's locked MCAP into ROS 2 -> bridge -> Kafka -> capture and
# waits for the capture to finalize on the run's explicit RUN_END. Sets:
#   REPLAY_SOURCE REPLAY_SUMMARY BRIDGE_SUMMARY CAPTURE_SUMMARY  the tools' own summaries
#   STREAM_RECORDING RECEIPT_PATH                                 the finalized capture (container paths)
#   ACQ_REPLAY_S ACQ_CAPTURE_S                                    wall seconds of the replay / capture container
streaming_acquire() {
  local fixture="$1" run_id="$2" robot_id="$3" capture_root="$4" bridge="$5" capture="$6"
  local fixture_id
  fixture_id="$(echo "$fixture" | jq -r '.fixture_id')"
  docker rm -f "$bridge" "$capture" >/dev/null 2>&1 || true
  streaming_assert_replay_isolated
  "${COMPOSE[@]}" run -d --name "$bridge" -T ros2 python3 /workspace/nodes/streaming_bridge_node.py \
    --robot-id "$robot_id" --robot-run-id "$run_id" --channels-file "$CHANNELS_FILE" \
    --exit-after-idle-seconds 10 >/dev/null
  "${COMPOSE[@]}" run -d --name "$capture" -T ros2 python3 /workspace/capture/cli.py \
    --robot-id "$robot_id" --robot-run-id "$run_id" --channels-file "$CHANNELS_FILE" \
    --output-root "$capture_root" --idle-timeout-seconds 120 >/dev/null
  for _ in $(seq 1 60); do
    docker logs "$bridge" 2>&1 | grep -q "streaming bridge ready" && break
    sleep 1
  done
  docker logs "$bridge" 2>&1 | grep -q "streaming bridge ready" || fail "bridge did not start"
  local replay_out
  replay_out="$("${REPLAY[@]}" reference replay --corpus "/config/reference/$REFERENCE_CORPUS" \
    --cache-root /reference --fixture "$fixture_id" ${RATE:+--rate "$RATE"} </dev/null)" || fail "replay failed"
  REPLAY_SOURCE="$(summary_line "$replay_out" replay_source)"
  REPLAY_SUMMARY="$(summary_line "$replay_out" replay_summary)"
  [ "$(docker wait "$bridge")" = 0 ] || fail "bridge exited non-zero"
  [ "$(docker wait "$capture")" = 0 ] || fail "capture exited non-zero"
  BRIDGE_SUMMARY="$(summary_line "$(docker logs "$bridge" 2>&1)" bridge_summary)"
  CAPTURE_SUMMARY="$(summary_line "$(docker logs "$capture" 2>&1)" capture_summary)"
  STREAM_RECORDING="$(echo "$CAPTURE_SUMMARY" | jq -r '.path')"
  RECEIPT_PATH="$(echo "$CAPTURE_SUMMARY" | jq -r '.receipt_path')"
  ACQ_REPLAY_S="$(echo "$REPLAY_SUMMARY" | jq -r '.elapsed_seconds')"
  ACQ_CAPTURE_S="$(container_seconds "$capture")"
}

# streaming_capture_receipt <receipt-path> -> the capture receipt JSON
streaming_capture_receipt() {
  "${COMPOSE[@]}" run --rm -T --entrypoint cat ros2 "$1" </dev/null
}

# ── Publication and registration (ADR-008) ───────────────────────────────────

# streaming_scan <capture-root> -> the capture-volume observation report
streaming_scan() {
  "${PUBLISHER[@]}" scan-capture --capture-root "$1" </dev/null
}

# streaming_run_state <run-id> <scan-json> -> the reconciler's observed state of
# the run ("absent" when it has no capture, no published object and no record)
streaming_run_state() {
  local report
  report="$("${API_EXEC[@]}" "${RECONCILE[@]}" --capture-report - <<<"$2")" || fail "the reconciler failed"
  jq -r --arg r "$1" '[.runs[] | select(.run_id == $r) | .state] | first // "absent"' <<<"$report"
}

# streaming_publish_and_register <run-id> <capture-root>
# From whatever durable state the run is in to a registered RobotRun, through the
# production commands only: publish-pending from the capture's receipt, then
# `reconcile --once --apply` (which submits REGISTER_ROBOT_RUN, and retries or
# replaces a stalled Job within the attempt budget). Idempotent: a run that is
# already further along skips the earlier steps.
streaming_publish_and_register() {
  local run_id="$1" capture_root="$2" scan state pending reconciled attempt
  scan="$(streaming_scan "$capture_root")"
  state="$(streaming_run_state "$run_id" "$scan")"
  log "  reconciler state of $run_id: $state"
  case "$state" in
    publish_pending | publication_incomplete)
      pending="$("${PUBLISHER[@]}" publish-pending --capture-root "$capture_root" </dev/null)" || {
        echo "$pending" | jq . >&2 || true
        fail "publish-pending failed for $run_id"
      }
      streaming_check "publish-pending published $run_id from its receipt and verified both objects" \
        [ "$(echo "$pending" | jq -r --arg r "$run_id" '.results[] | select(.run_id == $r) | .state_after == "published"')" = true ]
      ;;
    registration_pending | registration_active | registration_stalled_candidate | registration_failed_transient | registered) ;;
    *) fail "$run_id is in reconciler state '$state'; the bootstrap does not repair it (see docs/adr/008-acquisition-lifecycle-reliability.md)" ;;
  esac
  for attempt in 1 2 3; do
    api_get "$API_BASE_URL" "/robot-runs/$run_id" >/dev/null 2>&1 && return 0
    scan="$(streaming_scan "$capture_root")"
    reconciled="$("${API_EXEC[@]}" "${RECONCILE[@]}" --apply --capture-report - <<<"$scan")" \
      || fail "reconcile --apply failed for $run_id"
    log "  reconcile --apply (pass $attempt): $(echo "$reconciled" | jq -c --arg r "$run_id" '[.actions[] | select(.run_id == $r) | {kind, outcome}]')"
    for _ in $(seq 1 60); do
      api_get "$API_BASE_URL" "/robot-runs/$run_id" >/dev/null 2>&1 && return 0
      sleep 2
    done
  done
  fail "RobotRun $run_id was not registered after 3 reconcile passes"
}

# streaming_release_capture <run-id> <capture-root>
# Removes the transient capture of a registered run: its MCAP and receipt are
# durable in the ArtifactStore. Refuses unless the registered recording is
# byte-for-byte the capture's (the receipt's checksum), so only what is
# provably published is ever removed.
streaming_release_capture() {
  local run_id="$1" capture_root="$2" scan receipt registered
  case "$capture_root" in /recordings/capture-?*) ;; *) fail "refusing to remove unexpected capture root '$capture_root'" ;; esac
  "${COMPOSE[@]}" run --rm -T --entrypoint test ros2 -d "$capture_root" </dev/null || return 0
  scan="$(streaming_scan "$capture_root")"
  receipt="$(jq -r --arg r "$run_id" '[.runs[] | select(.run_id == $r) | .receipt.recording_checksum] | first // empty' <<<"$scan")"
  registered="$(api_get "$API_BASE_URL" "/artifacts/$(api_get "$API_BASE_URL" "/robot-runs/$run_id" | jq -r '.robotRun.recordingArtifactId')" | jq -r '.artifact.checksum')"
  [ -n "$receipt" ] && [ "$receipt" = "$registered" ] \
    || fail "keeping the capture of $run_id: the registered recording ($registered) is not the capture's ($receipt)"
  "${COMPOSE[@]}" run --rm -T --entrypoint rm ros2 -rf "$capture_root" </dev/null
}

# ── Registered recording facts ───────────────────────────────────────────────

# STREAM_FACTS: {"<run_id>": <manifest facts>} for every registered RobotRun of the
# selection, read from the RobotRunManifests through the publisher container.
STREAM_FACTS="{}"

streaming_load_facts() {
  local fixture id run_id manifests="{}" mid uri run
  local fixtures=()
  while IFS= read -r fixture; do fixtures+=("$fixture"); done < <(echo "$BASELINE_FIXTURES" | jq -c '.[]')
  for fixture in "${fixtures[@]}"; do
    id="$(echo "$fixture" | jq -r '.fixture_id')"
    run_id="$(baseline_run_id "$id")"
    run="$(api_get "$API_BASE_URL" "/robot-runs/$run_id" 2>/dev/null)" || continue
    mid="$(echo "$run" | jq -r '.robotRun.manifestArtifactId')"
    uri="$(api_get "$API_BASE_URL" "/artifacts/$mid" | jq -r '.artifact.uri')"
    manifests="$(echo "$manifests" | jq -c --arg r "$run_id" --arg u "$uri" '. + {($r): $u}')"
  done
  [ "$manifests" != "{}" ] || { STREAM_FACTS="{}"; return 0; }
  STREAM_FACTS="$("${COMPOSE[@]}" run --rm -T -v "$REPO_ROOT/scripts/streaming:/workspace/streaming:ro" \
    --entrypoint python recording-publisher /workspace/streaming/manifest_facts.py \
    <<<"$(jq -cn --argjson m "$manifests" '{manifests: $m}')")" || fail "could not read the RobotRun manifests"
}

# streaming_assert_recording <run-id> <locked-sha256> <locked-size-bytes>
# A streamed RobotRun pins the captured recording, not the locked one: it was
# captured from Kafka, and what must match the locked recording is its content,
# the message count and the per-channel counts the lock records. (Payload
# equivalence is proven by e2e-streaming-equivalence.)
streaming_assert_recording() {
  local run_id="$1" sha="$2" fixture_id fixture facts artifact
  fixture_id="${run_id#run-$BASELINE_ID-}"
  fixture="$(echo "$BASELINE_FIXTURES" | jq -c --arg f "$fixture_id" '.[] | select(.fixture_id == $f)')"
  facts="$(echo "$STREAM_FACTS" | jq -c --arg r "$run_id" '.[$r] // empty')"
  [ -n "$facts" ] || fail "RobotRun $run_id is not registered"
  artifact="$(api_get "$API_BASE_URL" "/artifacts/$(api_get "$API_BASE_URL" "/robot-runs/$run_id" | jq -r '.robotRun.recordingArtifactId')" | jq -c '.artifact')"
  [ "$(echo "$facts" | jq -r '.recording.checksum')" = "$(echo "$artifact" | jq -r '.checksum')" ] \
    && [ "$(echo "$facts" | jq -r '.recording.size_bytes')" = "$(echo "$artifact" | jq -r '.sizeBytes')" ] \
    || fail "RobotRun $run_id: its recording ArtifactRecord disagrees with its manifest"
  [ "$(echo "$artifact" | jq -r '.checksum')" != "$sha" ] \
    || fail "RobotRun $run_id pins the locked recording itself: it was published, not streamed"
  [ "$(echo "$facts" | jq -r --arg robot "$ROBOT_ID" --arg t "$KAFKA_TOPIC" \
      '[.robot_id == $robot, .capture_source.kind == "kafka", .capture_source.topics == [$t], .source_clock == "mcap_log_time"] | all')" = true ] \
    || fail "RobotRun $run_id is not a Kafka capture of robot $ROBOT_ID on the recording clock: $(echo "$facts" | jq -c '{robot_id, capture_source, source_clock}')"
  [ "$(echo "$facts" | jq -cS '[.message_count, .channel_counts]')" \
    = "$(echo "$fixture" | jq -cS '[.recording.message_count, .recording.topic_counts]')" ] \
    || fail "RobotRun $run_id: its message count / per-channel counts differ from the locked recording's"
}

# streaming_verify <api> — read-only; one JSON summary on stdout: the canonical
# baseline summary (baseline_verify) plus, per fixture, the streamed recording's
# registered facts. `recording_sha256` / `recording_bytes` keep naming the locked
# source recording the stream was replayed from.
streaming_verify() {
  local summary
  [ -n "$BASELINE_FIXTURES" ] || baseline_resolve lock-only
  streaming_load_facts
  BASELINE_RECORDING_ASSERT=streaming_assert_recording
  summary="$(baseline_verify "$1")"
  echo "$summary" | jq -c --argjson facts "$STREAM_FACTS" '
    . + {transport: "ros2_kafka_capture"}
    | .fixtures |= map(. + {streamed_recording: ($facts[.robot_run_id]
        | {sha256: .recording.checksum, size_bytes: .recording.size_bytes,
           message_count, channel_counts})})'
}

# ── Resources ────────────────────────────────────────────────────────────────

# streaming_storage — one JSON object on stdout: free GiB of the host volume and of
# the Docker VM disk (whichever is lower bounds the run), and the sizes (MiB) of
# MinIO, the Kafka log and the transient capture volume.
streaming_storage() {
  local host vm minio kafka recordings
  host="$(df -Pk "$REPO_ROOT" | awk 'NR==2 { printf "%.1f", $4 / 1048576 }')"
  vm="$("${COMPOSE[@]}" run --rm -T --entrypoint df ros2 -Pk /recordings </dev/null | awk 'NR==2 { printf "%.1f", $4 / 1048576 }')"
  minio="$("${COMPOSE[@]}" exec -T minio du -sk /data | awk '{ printf "%d", $1 / 1024 }')"
  kafka="$("${COMPOSE[@]}" exec -T kafka du -sk /var/lib/kafka/data | awk '{ printf "%d", $1 / 1024 }')"
  recordings="$("${COMPOSE[@]}" run --rm -T --entrypoint du ros2 -sk /recordings </dev/null | awk '{ printf "%d", $1 / 1024 }')"
  jq -cn --argjson host "$host" --argjson vm "$vm" --argjson minio "$minio" \
    --argjson kafka "$kafka" --argjson rec "$recordings" \
    '{host_free_gib: $host, docker_vm_free_gib: $vm, minio_mib: $minio, kafka_mib: $kafka, acquisition_recordings_mib: $rec}'
}

# ── Pipelines ────────────────────────────────────────────────────────────────

# An execute that the API rejects is reported with its HTTP status instead of
# leaving the pipeline run to time out unnoticed.
dispatch_pipeline_run() {
  local code
  code="$(curl -sS -o /dev/null -w '%{http_code}' -X POST "$(api_url "$1" "/pipelines/runs/$2/execute")")" || code=000
  case "$code" in
    2??) ;;
    *)
      log "pipeline_execute_rejected $(jq -cn --arg r "$2" --arg c "$code" --arg t "$(date -u +%FT%TZ)" \
        '{pipeline_run_id: $r, http_status: $c, at: $t}')"
      fail "executing pipeline run $2 returned HTTP $code"
      ;;
  esac
}
