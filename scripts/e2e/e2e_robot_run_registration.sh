#!/usr/bin/env bash
# e2e_robot_run_registration.sh — real-data E2E: nuScenes CAN replay ->
# ROS2 -> Kafka -> durable MCAP capture (ros2/capture/) -> ArtifactStore
# (real MinIO) -> ArtifactRecord -> canonical RobotRun (real Postgres) ->
# retrieve stored MCAP -> RosbagAdapter, plus idempotent-retry and
# conflict verification.
#
# Six stages:
#   1. Real CAN replay -> ROS2 -> bridge -> Kafka (same pattern as
#      e2e_ros2_streaming.sh / e2e_streaming_capture.sh).
#   2. Durable MCAP capture (ros2/capture/cli.py) -> finalized local MCAP
#      + CaptureResult.
#   3. Register: `sceneops-worker robots register-capture` (worker-cli,
#      real Postgres + real MinIO from .env.local -- the worker's normal
#      configured backend, not a test double).
#   4. Host-side verification (scripts/e2e/robot_run_registration_verify.py,
#      `uv run`) -- independently re-derives canonical state from
#      Postgres/MinIO directly, retrieves the stored MCAP, and opens it
#      through RosbagAdapter.
#   5. Idempotent retry: register the SAME file again -- must report
#      created=False and leave canonical state unchanged.
#   6. Conflict: register a DIFFERENT (real, valid) MCAP under the SAME
#      robot_run_id -- must fail without mutating existing state. Uses
#      the canonical baseline's own data/raw/rosbag/scene-0061/
#      scene-0061_0.mcap, read-only.
#
# Prerequisites (this script does not do either of these for you):
#   make local-up       # Postgres + MinIO + api (+ worker-cli's deps)
#   make streaming-up   # local Kafka broker
#
# Usage:
#   make e2e-robot-run-registration
#   SCENE=scene-0061 RATE=10.0 scripts/e2e/e2e_robot_run_registration.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

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
CAPTURE_PARTITION="$(grep -oE 'partition=[0-9]+' "$CAPTURE_LOG" | tail -1 | cut -d= -f2)"
CAPTURE_FIRST_OFFSET="$(grep -oE 'first_offset=[0-9]+' "$CAPTURE_LOG" | tail -1 | cut -d= -f2)"
CAPTURE_LAST_OFFSET="$(grep -oE 'last_offset=[0-9]+' "$CAPTURE_LOG" | tail -1 | cut -d= -f2)"
CAPTURE_FIRST_SEQ="$(grep -oE 'first_sequence=[0-9]+' "$CAPTURE_LOG" | tail -1 | cut -d= -f2)"
CAPTURE_LAST_SEQ="$(grep -oE 'last_sequence=[0-9]+' "$CAPTURE_LOG" | tail -1 | cut -d= -f2)"
CAPTURE_SHA256="$(grep -oE 'sha256=[0-9a-f]+' "$CAPTURE_LOG" | tail -1 | cut -d= -f2)"

if [ "$CAPTURE_FIRST_SEQ" != "0" ] || [ "$CAPTURE_MESSAGE_COUNT" != "$PUBLISHED_COUNT" ] || [ -z "$CAPTURE_SHA256" ]; then
  echo "❌ capture stage produced unexpected results" >&2
  exit 1
fi
echo "  captured message_count=$CAPTURE_MESSAGE_COUNT sha256=$CAPTURE_SHA256"
echo ""

MCAP_PATH="$CAPTURED_ROOT/$ROBOT_RUN_ID/${ROBOT_RUN_ID}_0.mcap"
EXPECTED_CHECKSUM="sha256:$CAPTURE_SHA256"
CAPTURE_METADATA_JSON=$(printf '{"topic":"sceneops.robot.telemetry.v1","partition":%s,"first_offset":%s,"last_offset":%s,"first_sequence":%s,"last_sequence":%s,"message_count":%s}' \
  "$CAPTURE_PARTITION" "$CAPTURE_FIRST_OFFSET" "$CAPTURE_LAST_OFFSET" "$CAPTURE_FIRST_SEQ" "$CAPTURE_LAST_SEQ" "$CAPTURE_MESSAGE_COUNT")

echo "=== [3/6] register: ArtifactStore + ArtifactRecord + RobotRun (real Postgres + MinIO) ==="
$COMPOSE --profile debug --profile worker run --rm worker-cli \
  sceneops-worker robots register-capture \
  --robot-id "$ROBOT_ID" --robot-run-id "$ROBOT_RUN_ID" \
  --mcap-path "$MCAP_PATH" \
  --capture-metadata-json "$CAPTURE_METADATA_JSON"
echo ""

echo "=== [4/6] host-side verification (real Postgres + MinIO, RosbagAdapter) ==="
# Host-side overrides for the same real Postgres/MinIO the registration
# stage above just wrote to, reached via their host-published local-stack
# ports rather than in-network service names -- mirrors
# makefiles/setup.mk's own test-integration env var convention and
# smoke_streaming.sh's identical host-vs-in-network split.
SCENEOPS_DATABASE_URL="postgresql+asyncpg://sceneops:sceneops@localhost:${POSTGRES_PORT:-5432}/sceneops" \
MINIO_API_PORT="${MINIO_API_PORT:-9000}" \
MINIO_ROOT_USER="${MINIO_ROOT_USER:-minioadmin}" \
MINIO_ROOT_PASSWORD="${MINIO_ROOT_PASSWORD:-minioadmin}" \
uv run python scripts/e2e/robot_run_registration_verify.py \
  --robot-id "$ROBOT_ID" --robot-run-id "$ROBOT_RUN_ID" \
  --expected-checksum "$EXPECTED_CHECKSUM"
echo ""

echo "=== [5/6] idempotent retry (same file, same robot_run_id) ==="
RETRY_LOG="$(mktemp)"
trap 'rm -f "$BRIDGE_LOG" "$CAPTURE_LOG" "$RETRY_LOG"' EXIT
$COMPOSE --profile debug --profile worker run --rm worker-cli \
  sceneops-worker robots register-capture \
  --robot-id "$ROBOT_ID" --robot-run-id "$ROBOT_RUN_ID" \
  --mcap-path "$MCAP_PATH" \
  2>&1 | tee "$RETRY_LOG"

if ! grep -q "created=False" "$RETRY_LOG"; then
  echo "❌ idempotent retry did not report created=False -- see $RETRY_LOG" >&2
  exit 1
fi
echo "  ✅  idempotent retry correctly created nothing new"
echo ""

echo "=== [6/6] conflict: different (real) MCAP under the same robot_run_id ==="
CONFLICT_MCAP="$REPO_ROOT/data/raw/rosbag/scene-0061/scene-0061_0.mcap"
if [ ! -f "$CONFLICT_MCAP" ]; then
  echo "  (skip) canonical baseline fixture not present at $CONFLICT_MCAP"
else
  if $COMPOSE --profile debug --profile worker run --rm worker-cli \
    sceneops-worker robots register-capture \
    --robot-id "$ROBOT_ID" --robot-run-id "$ROBOT_RUN_ID" \
    --mcap-path "/data/raw/rosbag/scene-0061/scene-0061_0.mcap"; then
    echo "❌ conflicting registration (same robot_run_id, different checksum) unexpectedly succeeded" >&2
    exit 1
  fi
  echo "  ✅  conflicting registration correctly refused (see traceback above)"
fi

echo ""
echo "=== RobotRun registration E2E complete ==="
