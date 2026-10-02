#!/usr/bin/env bash
# e2e_robot_run_registration.sh — real-data E2E: nuScenes CAN replay ->
# ROS2 -> Kafka -> durable MCAP capture (ros2/capture/) -> database-free
# Recording Publisher (MCAP + canonical RobotRunManifest, real MinIO) ->
# REGISTER_ROBOT_RUN via POST /robot-runs:register (ArtifactRecords +
# immutable RobotRunRecord, real Postgres), plus idempotent-retry and
# write-once conflict verification.
#
# Six stages:
#   1. Real CAN replay -> ROS2 -> bridge -> Kafka (same pattern as
#      e2e_ros2_streaming.sh / e2e_streaming_capture.sh).
#   2. Durable MCAP capture (ros2/capture/cli.py) -> finalized local MCAP
#      + CaptureResult.
#   3. Publish (python -m sceneops_integrations.recording, its own process)
#      then register (POST /robot-runs:register -> REGISTER_ROBOT_RUN Job).
#   4. Host-side verification (scripts/e2e/robot_run_registration_verify.py,
#      `uv run`) -- independently re-derives canonical state from
#      Postgres/MinIO, re-checks the manifest's canonical form and the
#      recording checksum, and opens the stored MCAP through RosbagAdapter.
#   5. Idempotent retry: publish + register the SAME file again.
#      - the publisher writes nothing;
#      - the identical HTTP request (POST /robot-runs:register) returns the
#        SAME, already-succeeded Job via execution-key dedup, with no new
#        execution. Its result is that Job's original registrar result, so
#        it still reads created=true: `created` describes the execution
#        that produced the result, not the latest request;
#      - a direct registrar re-run (sceneops-worker robots register, the
#        same registrar the Job handler runs) actually executes and reports
#        created=False;
#      - RobotRun and its two ArtifactRecords are unchanged throughout.
#   6. Conflict: publish a DIFFERENT (real, valid) MCAP under the SAME
#      run id -- the publisher must refuse (write-once key) and canonical
#      state must be unchanged. Uses the canonical baseline's own
#      data/raw/rosbag/scene-0061/scene-0061_0.mcap, read-only.
#
# Prerequisites (this script does not do either of these for you):
#   make local-up       # Postgres + MinIO + api + workers (images built
#                       # from the current tree)
#   make streaming-up   # local Kafka broker
#
# Usage:
#   make e2e-robot-run-registration
#   SCENE=scene-0061 RATE=10.0 scripts/e2e/e2e_robot_run_registration.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"
source "$SCRIPT_DIR/lib.sh"

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"

SCENE="${SCENE:-scene-0061}"
RATE="${RATE:-10.0}"
DURATION="${DURATION:-20}"
ROBOT_ID="${ROBOT_ID:-robot-nuscenes-registration}"
ROBOT_RUN_ID="run-registration-$(date +%s)-$$"

COMPOSE="docker compose --env-file .env.local"
CAPTURED_ROOT="/data/tmp_robot_run_registration/kafka-captured"
HOST_CAPTURED_ROOT="$REPO_ROOT/data/tmp_robot_run_registration/kafka-captured"

rm -rf "$HOST_CAPTURED_ROOT"

echo "=== [1/6] real CAN replay -> ROS2 -> bridge -> Kafka ==="
echo "  scene=$SCENE rate=$RATE duration=${DURATION}s"
echo "  robot_id=$ROBOT_ID robot_run_id=$ROBOT_RUN_ID"
echo ""

BRIDGE_LOG="$(mktemp)"
CAPTURE_LOG="$(mktemp)"
trap 'rm -f "$BRIDGE_LOG" "$CAPTURE_LOG"' EXIT

$COMPOSE --profile ros2 run --rm ros2 sh -c "
  set -e
  timeout $DURATION python3 /workspace/nodes/streaming_bridge_node.py \
    --robot-id $ROBOT_ID --robot-run-id $ROBOT_RUN_ID &
  BRIDGE_PID=\$!
  sleep 2
  python3 /workspace/nodes/can_replay_node.py --scene $SCENE --rate $RATE
  wait \$BRIDGE_PID || true
" 2>&1 | tee "$BRIDGE_LOG"
echo ""

PUBLISHED_COUNT="$(grep -oE 'published=[0-9]+' "$BRIDGE_LOG" | tail -1 | cut -d= -f2 || true)"
FAILED_COUNT="$(grep -oE 'failed=[0-9]+' "$BRIDGE_LOG" | tail -1 | cut -d= -f2 || true)"
if [ -z "${PUBLISHED_COUNT:-}" ] || [ "${FAILED_COUNT:-0}" != "0" ]; then
  echo "❌ bridge stage failed (published=${PUBLISHED_COUNT:-<missing>} failed=${FAILED_COUNT:-0})" >&2
  exit 1
fi
echo "  bridge reported: published=$PUBLISHED_COUNT failed=${FAILED_COUNT:-0}"
echo ""

echo "=== [2/6] durable MCAP capture (Kafka -> ros2/capture) ==="
$COMPOSE --profile ros2 run --rm ros2 python3 /workspace/capture/cli.py \
  --robot-id "$ROBOT_ID" --robot-run-id "$ROBOT_RUN_ID" \
  --output-root "$CAPTURED_ROOT" \
  --max-messages "$PUBLISHED_COUNT" \
  2>&1 | tee "$CAPTURE_LOG"
echo ""

CAPTURE_MESSAGE_COUNT="$(grep -oE 'message_count=[0-9]+' "$CAPTURE_LOG" | tail -1 | cut -d= -f2)"
CAPTURE_FIRST_SEQ="$(grep -oE 'first_sequence=[0-9]+' "$CAPTURE_LOG" | tail -1 | cut -d= -f2)"
CAPTURE_SHA256="$(grep -oE 'sha256=[0-9a-f]+' "$CAPTURE_LOG" | tail -1 | cut -d= -f2)"

if [ "$CAPTURE_FIRST_SEQ" != "0" ] || [ "$CAPTURE_MESSAGE_COUNT" != "$PUBLISHED_COUNT" ] || [ -z "$CAPTURE_SHA256" ]; then
  echo "❌ capture stage produced unexpected results" >&2
  exit 1
fi
echo "  captured message_count=$CAPTURE_MESSAGE_COUNT sha256=$CAPTURE_SHA256"
echo ""

MCAP_PATH="$CAPTURED_ROOT/$ROBOT_RUN_ID/${ROBOT_RUN_ID}_0.mcap"
EXPECTED_CHECKSUM="sha256:$CAPTURE_SHA256"
KAFKA_TOPIC="sceneops.robot.telemetry.v1"

echo "=== [3/6] publish (MinIO) + REGISTER_ROBOT_RUN (Postgres) ==="
PUBLICATION="$(publish_robot_run_recording "$REPO_ROOT" "$ROBOT_ID" "$ROBOT_RUN_ID" \
  "$MCAP_PATH" kafka "nuscenes-can-replay" "$KAFKA_TOPIC")"
echo "  publication: $PUBLICATION"
MANIFEST_URI="$(echo "$PUBLICATION" | jq -r '.manifest_uri')"
JOB_JSON="$(register_robot_run "$API_BASE_URL" "$MANIFEST_URI")"
assert_job_succeeded "$JOB_JSON" "REGISTER_ROBOT_RUN should succeed"
echo "$JOB_JSON" | jq '.job.result'
[ "$(echo "$JOB_JSON" | jq -r '.job.result.created')" = "true" ] || {
  echo "❌ first registration did not report created=true" >&2
  exit 1
}
echo ""

echo "=== [4/6] host-side verification (real Postgres + MinIO, RosbagAdapter) ==="
# Host-side overrides for the same real Postgres/MinIO the stages above
# wrote to, via their host-published local-stack ports (same convention as
# makefiles/setup.mk's test-integration).
SCENEOPS_DATABASE_URL="postgresql+asyncpg://sceneops:sceneops@localhost:${POSTGRES_PORT:-5432}/sceneops" \
MINIO_API_PORT="${MINIO_API_PORT:-9000}" \
MINIO_ROOT_USER="${MINIO_ROOT_USER:-minioadmin}" \
MINIO_ROOT_PASSWORD="${MINIO_ROOT_PASSWORD:-minioadmin}" \
uv run python scripts/e2e/robot_run_registration_verify.py \
  --robot-id "$ROBOT_ID" --robot-run-id "$ROBOT_RUN_ID" \
  --manifest-uri "$MANIFEST_URI" \
  --expected-checksum "$EXPECTED_CHECKSUM" \
  --expected-topic "$KAFKA_TOPIC"
echo ""

echo "=== [5/6] idempotent retry (same file, same run id) ==="
FIRST_JOB_ID="$(echo "$JOB_JSON" | jq -r '.job.jobId')"
FIRST_RESULT="$(echo "$JOB_JSON" | jq -cS '.job.result')"
RUN_BEFORE="$(curl -sS "$(api_url "$API_BASE_URL" "/robot-runs/$ROBOT_RUN_ID")" | jq -cS '.robotRun')"
ARTIFACTS_BEFORE="$(fetch_artifacts_by_owner "$API_BASE_URL" robot_run "$ROBOT_RUN_ID" \
  | jq -cS '[.artifacts[] | {artifactId, kind, uri, checksum, sizeBytes, createdAt}] | sort_by(.artifactId)')"
[ "$(echo "$ARTIFACTS_BEFORE" | jq -r '[.[].kind] | sort | join(",")')" = "robot_run_manifest,robot_run_recording" ] || {
  echo "❌ expected exactly one recording + one manifest ArtifactRecord, got $ARTIFACTS_BEFORE" >&2
  exit 1
}

# assert_registration_unchanged <label>: RobotRun (incl. registeredAt) and
# both ArtifactRecords are byte-for-byte the same as after [3/6].
assert_registration_unchanged() {
  local run_now artifacts_now
  run_now="$(curl -sS "$(api_url "$API_BASE_URL" "/robot-runs/$ROBOT_RUN_ID")" | jq -cS '.robotRun')"
  artifacts_now="$(fetch_artifacts_by_owner "$API_BASE_URL" robot_run "$ROBOT_RUN_ID" \
    | jq -cS '[.artifacts[] | {artifactId, kind, uri, checksum, sizeBytes, createdAt}] | sort_by(.artifactId)')"
  [ "$run_now" = "$RUN_BEFORE" ] || { echo "❌ $1: RobotRun changed" >&2; exit 1; }
  [ "$artifacts_now" = "$ARTIFACTS_BEFORE" ] || { echo "❌ $1: RobotRun ArtifactRecords changed" >&2; exit 1; }
}

RETRY_PUBLICATION="$(publish_robot_run_recording "$REPO_ROOT" "$ROBOT_ID" "$ROBOT_RUN_ID" \
  "$MCAP_PATH" kafka "nuscenes-can-replay" "$KAFKA_TOPIC")"
echo "  publication: $RETRY_PUBLICATION"
if [ "$(echo "$RETRY_PUBLICATION" | jq -r '.recording_written, .manifest_written' | sort -u)" != "false" ]; then
  echo "❌ identical republish wrote objects again" >&2
  exit 1
fi
echo "  ✅  identical republish wrote nothing"

# HTTP retry: Job-level dedup, no second registrar execution.
RETRY_SUBMISSION="$(submit_robot_run_registration "$API_BASE_URL" "$MANIFEST_URI")"
[ "$(echo "$RETRY_SUBMISSION" | jq -r '.job.jobId')" = "$FIRST_JOB_ID" ] || {
  echo "❌ identical POST /robot-runs:register did not return the original Job" >&2
  echo "$RETRY_SUBMISSION" | jq . >&2
  exit 1
}
[ "$(echo "$RETRY_SUBMISSION" | jq -r '.execution')" = "null" ] || {
  echo "❌ identical POST /robot-runs:register dispatched a new execution" >&2
  exit 1
}
RETRY_JOB_JSON="$(fetch_job "$API_BASE_URL" "$FIRST_JOB_ID")"
assert_job_succeeded "$RETRY_JOB_JSON" "deduplicated registration Job should remain succeeded"
[ "$(echo "$RETRY_JOB_JSON" | jq -cS '.job.result')" = "$FIRST_RESULT" ] || {
  echo "❌ deduplicated registration Job result changed" >&2
  exit 1
}
assert_registration_unchanged "HTTP retry"
echo "  ✅  HTTP retry returned the same succeeded Job ($FIRST_JOB_ID), no new execution, original result (created=true) unchanged"

# Registrar retry: the registrar actually runs again and converges.
REGISTRAR_RETRY="$(docker compose -f "$REPO_ROOT/compose.yaml" --env-file "$REPO_ROOT/.env.local" \
  --profile debug --profile worker run --rm -T worker-cli \
  sceneops-worker robots register --manifest-uri "$MANIFEST_URI" 2>&1)"
echo "$REGISTRAR_RETRY" | tail -1
echo "$REGISTRAR_RETRY" | grep -q "created=False" || {
  echo "❌ direct registrar retry did not report created=False" >&2
  exit 1
}
assert_registration_unchanged "registrar retry"
echo "  ✅  direct registrar retry executed and reported created=False; RobotRun + ArtifactRecords unchanged"
echo ""

echo "=== [6/6] conflict: different (real) MCAP under the same run id ==="
CONFLICT_MCAP="$REPO_ROOT/data/raw/rosbag/scene-0061/scene-0061_0.mcap"
if [ ! -f "$CONFLICT_MCAP" ]; then
  echo "  (skip) canonical baseline fixture not present at $CONFLICT_MCAP"
else
  if publish_robot_run_recording "$REPO_ROOT" "$ROBOT_ID" "$ROBOT_RUN_ID" \
    "/data/raw/rosbag/scene-0061/scene-0061_0.mcap" kafka "nuscenes-can-replay" \
    "$KAFKA_TOPIC"; then
    echo "❌ conflicting publication (same run id, different bytes) unexpectedly succeeded" >&2
    exit 1
  fi
  echo "  ✅  conflicting publication correctly refused (see error above)"
  SCENEOPS_DATABASE_URL="postgresql+asyncpg://sceneops:sceneops@localhost:${POSTGRES_PORT:-5432}/sceneops" \
  MINIO_API_PORT="${MINIO_API_PORT:-9000}" \
  MINIO_ROOT_USER="${MINIO_ROOT_USER:-minioadmin}" \
  MINIO_ROOT_PASSWORD="${MINIO_ROOT_PASSWORD:-minioadmin}" \
  uv run python scripts/e2e/robot_run_registration_verify.py \
    --robot-id "$ROBOT_ID" --robot-run-id "$ROBOT_RUN_ID" \
    --manifest-uri "$MANIFEST_URI" \
    --expected-checksum "$EXPECTED_CHECKSUM" \
    --expected-topic "$KAFKA_TOPIC"
fi

echo ""
echo "=== RobotRun publication + registration E2E complete ==="
