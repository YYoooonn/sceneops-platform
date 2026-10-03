#!/usr/bin/env bash
# e2e_batch_acquisition.sh — black-box E2E for batch L1 acquisition
# (ADR-007 §29.4, §29.13). Data-plane steps run as one-shot containers;
# every SceneOps platform operation goes through the FastAPI control plane.
#
#   nuScenes v1.0-mini scene (read-only mount)
#     -> dataset-acquisition container        finalized MCAP in the acquisition-recordings volume
#     -> recording-publisher container        L1 conformance check, then publication to the
#                                             ArtifactStore (recording + RobotRunManifest)
#     -> POST /robot-runs:register            REGISTER_ROBOT_RUN Job, polled via GET /jobs/{id}
#     -> GET /robot-runs/{id}, GET /artifacts RobotRun + recording/manifest ArtifactRecords
#     -> POST /jobs ingest_robot_states       a production consumer: resolves and verifies the
#                                             registered recording (resolve_recording) and reads it
#     -> republish + re-register              idempotent through publisher result and API
#
# The host needs Docker Compose, curl and jq, plus the API's HTTP port. It
# never sees database or object-storage credentials or ports: containers
# get their settings from compose configuration and reach each other by
# service name. The MCAP moves between containers through a Docker volume,
# never through the host or the API.
#
# Prerequisites:
#   make local-up               API + workers + Postgres + MinIO, from current images
#   make acquisition-image      (make e2e-batch-acquisition builds it)
#   data/raw/nuscenes with v1.0-mini and can_bus (ACQUISITION_NUSCENES_ROOT overrides)
#
# Usage:
#   make e2e-batch-acquisition
#   SOURCE_UNIT=scene-0103 scripts/e2e/e2e_batch_acquisition.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"
source "$SCRIPT_DIR/lib.sh"

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
SOURCE_VERSION="${SOURCE_VERSION:-v1.0-mini}"
SOURCE_UNIT="${SOURCE_UNIT:-scene-0061}"
ROBOT_ID="${ROBOT_ID:-robot-batch-acquisition}"
ROBOT_RUN_ID="run-batch-acquisition-$(date +%s)-$$"
POLL_ATTEMPTS="${POLL_ATTEMPTS:-120}"

COMPOSE=(docker compose --env-file "${ENV_FILE:-.env.local}" --profile acquisition)
ACQUIRE=("${COMPOSE[@]}" run --rm -T dataset-acquisition)
PUBLISHER=("${COMPOSE[@]}" run --rm -T recording-publisher)

# Paths inside the containers (compose/acquisition.yaml).
DATAROOT=/input/nuscenes
RECORDING="/recordings/$ROBOT_RUN_ID.mcap"
REPEAT="/recordings/$ROBOT_RUN_ID.repeat.mcap"

cleanup() {
  "${COMPOSE[@]}" run --rm -T --entrypoint rm dataset-acquisition -f "$RECORDING" "$REPEAT" \
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

# ns since epoch -> the API's ISO-8601 UTC datetime (µs precision, the
# RobotRunManifest projection). Integer arithmetic in bash, not jq floats.
ns_to_iso() {
  local ns="$1" sec us
  sec=$((ns / 1000000000))
  us=$(((ns % 1000000000) / 1000))
  if [ "$us" -eq 0 ]; then
    jq -rn --argjson s "$sec" '$s | todate'
  else
    jq -rn --argjson s "$sec" --arg us "$(printf '%06d' "$us")" '$s | todate | sub("Z$"; "." + $us + "Z")'
  fi
}

acquire() {
  "${ACQUIRE[@]}" nuscenes --dataroot "$DATAROOT" --version "$SOURCE_VERSION" \
    --source-unit "$SOURCE_UNIT" --output "$1"
}

publish() {
  "${PUBLISHER[@]}" publish --mcap-path "$RECORDING" \
    --run-id "$ROBOT_RUN_ID" --robot-id "$ROBOT_ID" --source-kind file
}

robot_run_json() {
  curl -fsS "$(api_url "$API_BASE_URL" "/robot-runs/$ROBOT_RUN_ID")"
}

robot_run_artifacts_json() {
  fetch_artifacts_by_owner "$API_BASE_URL" robot_run "$ROBOT_RUN_ID" \
    | jq -cS '[.artifacts[] | {artifactId, kind, uri, checksum, sizeBytes, createdAt}] | sort_by(.artifactId)'
}

echo "=== [0/7] control plane reachable; acquisition image isolated ==="
curl -fsS "$API_BASE_URL/health" >/dev/null || fail "SceneOps API not reachable at $API_BASE_URL (make local-up)"
echo "  ✅  API healthy at $API_BASE_URL"
"${COMPOSE[@]}" run --rm -T --entrypoint python dataset-acquisition - \
  < scripts/checks/acquisition_image_boundary.py | sed 's/^/  /'
echo ""

echo "=== [1/7] nuScenes -> dataset-acquisition container -> MCAP ==="
echo "  source=nuScenes $SOURCE_VERSION/$SOURCE_UNIT robot_run_id=$ROBOT_RUN_ID"
SUMMARY="$(acquire "$RECORDING")"
echo "$SUMMARY" | jq -c '{path, sha256, size_bytes, message_count}'
REPEAT_SHA="$(acquire "$REPEAT" | jq -r '.sha256')"
check "second acquisition of the same source is byte-identical" \
  [ "$REPEAT_SHA" = "$(echo "$SUMMARY" | jq -r '.sha256')" ]
echo ""

echo "=== [2/7] L1 conformance (recording-publisher container) ==="
CONFORMANCE="$("${PUBLISHER[@]}" check --mcap-path "$RECORDING")" || {
  echo "$CONFORMANCE" | jq '.violations' >&2
  fail "recording does not conform to the L1 contract"
}
echo "$CONFORMANCE" | jq -c '{conforms, message_count, channels: (.channels | length)}'
check "conformance saw every acquired message" \
  [ "$(echo "$CONFORMANCE" | jq -r '.message_count')" = "$(echo "$SUMMARY" | jq -r '.message_count')" ]
for topic in /camera/front/image/compressed /camera/front/camera_info /lidar/top/points \
  /tf_static /tf /vehicle/odom /mission/status; do
  echo "$CONFORMANCE" | jq -e --arg t "$topic" '.channels[$t].message_count > 0' >/dev/null \
    || fail "required channel $topic missing"
done
echo "  ✅  camera, CameraInfo, lidar, /tf_static, /tf, CAN and mission channels present"
echo ""

echo "=== [3/7] publication (recording-publisher container -> ArtifactStore) ==="
PUBLICATION="$(publish)"
echo "$PUBLICATION" | jq -c '{run_id, manifest_uri, recording_written, manifest_written}'
MANIFEST_URI="$(echo "$PUBLICATION" | jq -r '.manifest_uri')"
check "publisher result describes the acquired recording" \
  [ "$(echo "$PUBLICATION" | jq -r '[.run_id, .recording_checksum, .recording_size_bytes] | @tsv')" \
    = "$(printf '%s\t%s\t%s' "$ROBOT_RUN_ID" "$(echo "$SUMMARY" | jq -r '.sha256')" "$(echo "$SUMMARY" | jq -r '.size_bytes')")" ]
echo ""

echo "=== [4/7] POST /robot-runs:register -> REGISTER_ROBOT_RUN Job ==="
JOB_JSON="$(register_robot_run "$API_BASE_URL" "$MANIFEST_URI")"
assert_job_succeeded "$JOB_JSON" "REGISTER_ROBOT_RUN should succeed"
RESULT="$(echo "$JOB_JSON" | jq -c '.job.result')"
echo "  result: $RESULT"
check "Job result: created, for this run and robot" \
  [ "$(echo "$RESULT" | jq -r '[.created, .run_id, .robot_id] | @tsv')" \
    = "$(printf 'true\t%s\t%s' "$ROBOT_RUN_ID" "$ROBOT_ID")" ]
check "Job result manifest checksum == publisher's" \
  [ "$(echo "$RESULT" | jq -r '.manifest_checksum')" = "$(echo "$PUBLICATION" | jq -r '.manifest_checksum')" ]
echo ""

echo "=== [5/7] control-plane state: GET /robot-runs, GET /artifacts ==="
RUN="$(robot_run_json | jq -c '.robotRun')"
echo "  robotRun: $(echo "$RUN" | jq -c '{runId, robotId, startedAt, endedAt, recordingFormat, sourceClock}')"
check "RobotRun belongs to the robot, records an mcap recording on the mcap_log_time clock" \
  [ "$(echo "$RUN" | jq -r '[.robotId, .recordingFormat, .sourceClock] | @tsv')" \
    = "$(printf '%s\tmcap\tmcap_log_time' "$ROBOT_ID")" ]
check "RobotRun extent == the recording's simulated receive-time range" \
  [ "$(echo "$RUN" | jq -r '[.startedAt, .endedAt] | @tsv')" \
    = "$(printf '%s\t%s' "$(ns_to_iso "$(echo "$SUMMARY" | jq -r '.first_log_time_ns')")" \
         "$(ns_to_iso "$(echo "$SUMMARY" | jq -r '.last_log_time_ns')")")" ]
check "RobotRun manifest checksum and artifact ids == Job result" \
  [ "$(echo "$RUN" | jq -r '[.manifestChecksum, .recordingArtifactId, .manifestArtifactId] | @tsv')" \
    = "$(echo "$RESULT" | jq -r '[.manifest_checksum, .recording_artifact_id, .manifest_artifact_id] | @tsv')" ]

RECORDING_ARTIFACT="$(curl -fsS "$(api_url "$API_BASE_URL" "/artifacts/$(echo "$RUN" | jq -r '.recordingArtifactId')")" | jq -c '.artifact')"
MANIFEST_ARTIFACT="$(curl -fsS "$(api_url "$API_BASE_URL" "/artifacts/$(echo "$RUN" | jq -r '.manifestArtifactId')")" | jq -c '.artifact')"
check "recording ArtifactRecord == acquired bytes (uri, sha256, size)" \
  [ "$(echo "$RECORDING_ARTIFACT" | jq -r '[.kind, .uri, .checksum, .sizeBytes] | @tsv')" \
    = "$(printf 'robot_run_recording\t%s\t%s\t%s' "$(echo "$PUBLICATION" | jq -r '.recording_uri')" \
         "$(echo "$SUMMARY" | jq -r '.sha256')" "$(echo "$SUMMARY" | jq -r '.size_bytes')")" ]
check "manifest ArtifactRecord == published manifest (uri, sha256)" \
  [ "$(echo "$MANIFEST_ARTIFACT" | jq -r '[.kind, .uri, .checksum] | @tsv')" \
    = "$(printf 'robot_run_manifest\t%s\t%s' "$MANIFEST_URI" "$(echo "$PUBLICATION" | jq -r '.manifest_checksum')")" ]
ARTIFACTS_BEFORE="$(robot_run_artifacts_json)"
check "the RobotRun owns exactly its recording and manifest ArtifactRecords" \
  [ "$(echo "$ARTIFACTS_BEFORE" | jq -r '[.[].kind] | sort | join(",")')" = "robot_run_manifest,robot_run_recording" ]
echo ""

echo "=== [6/7] ingest_robot_states Job: resolve + verify the registered recording ==="
INGEST="$(create_job "$API_BASE_URL" \
  "$(jq -cn --arg id "$ROBOT_RUN_ID" '{type: "ingest_robot_states", force: true, params: {robot_run_id: $id}}')")"
INGEST_JOB_ID="$(extract_job_id "$INGEST")"
execute_job "$API_BASE_URL" "$INGEST_JOB_ID" | jq -e '.execution.status' >/dev/null \
  || fail "ingest_robot_states dispatch failed"
INGEST_JSON="$(poll_job_terminal "$API_BASE_URL" "$INGEST_JOB_ID" "$POLL_ATTEMPTS" 2)"
assert_job_succeeded "$INGEST_JSON" "ingest_robot_states should resolve and read the recording"
echo "  result: $(echo "$INGEST_JSON" | jq -c '.job.result | {state_count, mission_count}')"
assert_json_gt "$INGEST_JSON" '.job.result.state_count' 0 'expected robot states from CAN channels'
MISSION="$(fetch_missions "$API_BASE_URL" "$ROBOT_RUN_ID" | jq -c '.missions')"
check "one completed Mission whose bounds are the recording's source-timeline extent" \
  [ "$(echo "$MISSION" | jq -r 'length, .[0].status, .[0].startedAt, .[0].endedAt' | paste -sd' ' -)" \
    = "1 completed $(echo "$RUN" | jq -r '.startedAt') $(echo "$RUN" | jq -r '.endedAt')" ]
echo ""

echo "=== [7/7] idempotency: republish, re-register (API) ==="
RUN_BEFORE="$(robot_run_json | jq -cS '.robotRun')"
RETRY="$(publish)"
check "identical republish writes nothing and yields the same manifest" \
  [ "$(echo "$RETRY" | jq -r '[.recording_written, .manifest_written, .manifest_uri, .manifest_checksum] | @tsv')" \
    = "$(printf 'false\tfalse\t%s\t%s' "$MANIFEST_URI" "$(echo "$PUBLICATION" | jq -r '.manifest_checksum')")" ]

FIRST_JOB_ID="$(echo "$JOB_JSON" | jq -r '.job.jobId')"
RESUBMIT="$(submit_robot_run_registration "$API_BASE_URL" "$MANIFEST_URI")"
check "re-POST /robot-runs:register returns the original Job without a new execution" \
  [ "$(echo "$RESUBMIT" | jq -r '[.job.jobId, (.execution | tostring)] | @tsv')" \
    = "$(printf '%s\tnull' "$FIRST_JOB_ID")" ]

FORCED="$(create_job "$API_BASE_URL" \
  "$(jq -cn --arg uri "$MANIFEST_URI" '{type: "register_robot_run", force: true, params: {manifest_uri: $uri}}')")"
FORCED_JOB_ID="$(extract_job_id "$FORCED")"
execute_job "$API_BASE_URL" "$FORCED_JOB_ID" >/dev/null
FORCED_JSON="$(poll_job_terminal "$API_BASE_URL" "$FORCED_JOB_ID" 60 2)"
assert_job_succeeded "$FORCED_JSON" "a forced re-registration should converge"
check "forced REGISTER_ROBOT_RUN re-executes the registrar and reports created=false" \
  [ "$(echo "$FORCED_JSON" | jq -r '.job.result.created')" = "false" ]
check "RobotRun and its ArtifactRecords are unchanged" \
  [ "$(robot_run_json | jq -cS '.robotRun')$(robot_run_artifacts_json)" = "$RUN_BEFORE$ARTIFACTS_BEFORE" ]
echo ""
echo "=== batch acquisition E2E complete: robot_run_id=$ROBOT_RUN_ID ==="
