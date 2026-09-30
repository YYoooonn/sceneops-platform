#!/usr/bin/env bash
# e2e_streaming_capture.sh — real-data E2E: nuScenes CAN replay -> real
# ROS2 DDS -> ROS2 Streaming Bridge -> real Kafka -> durable MCAP capture
# (ros2/capture/) -> validated finalized MCAP -> RosbagAdapter
# compatibility check -> semantic comparison against a direct
# `ros2 bag record` of the same scene.
#
# Five stages, all touching only the ros2 container + real Kafka broker +
# scratch files under ./data/tmp_streaming_capture/ (never
# data/raw/rosbag/<scene>, the canonical baseline dataset location) --
# no Postgres, no MinIO, no api/worker, no canonical RobotRun/Scene/
# Episode/ArtifactRecord created.
#
#   1. Unit tests (ros2/capture/tests/) -- no Kafka, no live ROS graph.
#   2. Real CAN replay + bridge -> Kafka (same pattern as
#      e2e_ros2_streaming.sh) -- produces the bridge's own published
#      count, used as this capture's deterministic stop condition.
#   3. Durable MCAP capture (ros2/capture/cli.py) consuming exactly that
#      run's messages from the real broker into a finalized MCAP.
#   4. A second, independent CAN replay recorded directly via
#      `ros2 bag record` (the existing oracle path) for semantic
#      comparison -- NOT byte-identical, a fresh replay pass.
#   5. Host-side verification (scripts/e2e/mcap_capture_verify.py, via
#      `uv run` -- RosbagAdapter needs no ROS2/rclpy install): opens the
#      captured MCAP through the same RosbagAdapter apps/worker uses
#      (mandatory compatibility check, no DB writes) and compares it
#      against the direct-recorded bag.
#
# Prerequisites (this script does not do either of these for you):
#   make streaming-up      # starts the local Kafka broker
#   (the ros2 image is built on demand by `docker compose ... run`)
#
# Usage:
#   make e2e-streaming-capture
#   SCENE=scene-0061 RATE=10.0 DURATION=20 scripts/e2e/e2e_streaming_capture.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

SCENE="${SCENE:-scene-0061}"
RATE="${RATE:-10.0}"
DURATION="${DURATION:-20}"
ROBOT_ID="${ROBOT_ID:-robot-nuscenes-capture}"
ROBOT_RUN_ID="run-streaming-capture-$(date +%s)-$$"

COMPOSE="docker compose --env-file .env.local"

SCRATCH_ROOT="/data/tmp_streaming_capture"
CAPTURED_ROOT="$SCRATCH_ROOT/kafka-captured"
DIRECT_ROOT="$SCRATCH_ROOT/direct/$SCENE"
HOST_SCRATCH_ROOT="$REPO_ROOT/data/tmp_streaming_capture"

rm -rf "$HOST_SCRATCH_ROOT"

echo "=== [1/5] unit tests (ros2 container, no Kafka) ==="
$COMPOSE --profile ros2 run --rm ros2 python3 -m pytest /workspace/capture/tests/ -v
echo ""

echo "=== [2/5] real CAN replay -> ROS2 -> bridge -> Kafka ==="
echo "  scene=$SCENE rate=$RATE duration=${DURATION}s"
echo "  robot_id=$ROBOT_ID robot_run_id=$ROBOT_RUN_ID"
echo ""

BRIDGE_LOG="$(mktemp)"
trap 'rm -f "$BRIDGE_LOG"' EXIT

# See e2e_ros2_streaming.sh for why the bridge's `wait` always reports
# exit code 124 (timeout) by design, neutralized by `|| true`.
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

if [ -z "${PUBLISHED_COUNT:-}" ]; then
  echo "❌ could not parse the bridge's own published count from its log -- aborting" >&2
  exit 1
fi
if [ "${FAILED_COUNT:-0}" != "0" ]; then
  echo "❌ bridge reported ${FAILED_COUNT} failed message(s) -- aborting" >&2
  exit 1
fi
echo "  bridge reported: published=$PUBLISHED_COUNT failed=${FAILED_COUNT:-0}"
echo ""

echo "=== [3/5] durable MCAP capture (Kafka -> ros2/capture) ==="
CAPTURE_LOG="$(mktemp)"
trap 'rm -f "$BRIDGE_LOG" "$CAPTURE_LOG"' EXIT
$COMPOSE --profile ros2 run --rm ros2 python3 /workspace/capture/cli.py \
  --robot-id "$ROBOT_ID" --robot-run-id "$ROBOT_RUN_ID" \
  --output-root "$CAPTURED_ROOT" \
  --max-messages "$PUBLISHED_COUNT" \
  2>&1 | tee "$CAPTURE_LOG"
echo ""

CAPTURED_MESSAGE_COUNT="$(grep -oE 'message_count=[0-9]+' "$CAPTURE_LOG" | tail -1 | cut -d= -f2 || true)"
CAPTURED_FIRST_SEQ="$(grep -oE 'first_sequence=[0-9]+' "$CAPTURE_LOG" | tail -1 | cut -d= -f2 || true)"

if [ "${CAPTURED_FIRST_SEQ:-}" != "0" ]; then
  echo "❌ capture's first_sequence was '${CAPTURED_FIRST_SEQ:-<missing>}', expected 0" >&2
  exit 1
fi
if [ "${CAPTURED_MESSAGE_COUNT:-}" != "$PUBLISHED_COUNT" ]; then
  echo "❌ capture's message_count (${CAPTURED_MESSAGE_COUNT:-<missing>}) != bridge published count ($PUBLISHED_COUNT)" >&2
  exit 1
fi
echo "  captured message_count=$CAPTURED_MESSAGE_COUNT first_sequence=$CAPTURED_FIRST_SEQ"
echo ""

echo "=== [4/5] direct ros2 bag record (comparison oracle, scratch path) ==="
$COMPOSE --profile ros2 run --rm ros2 sh -c "
  set -e
  rm -rf $DIRECT_ROOT
  timeout $DURATION ros2 bag record -o $DIRECT_ROOT --storage mcap \
    /vehicle/odom /vehicle/imu /vehicle/status /vehicle/control /mission/status &
  sleep 2
  python3 /workspace/nodes/can_replay_node.py --scene $SCENE --rate $RATE
  wait
" || true
echo ""

echo "=== [5/5] host-side verification (RosbagAdapter, no DB writes) ==="
uv run python scripts/e2e/mcap_capture_verify.py \
  --captured-mcap "$HOST_SCRATCH_ROOT/kafka-captured/$ROBOT_RUN_ID/${ROBOT_RUN_ID}_0.mcap" \
  --direct-mcap "$HOST_SCRATCH_ROOT/direct/$SCENE/${SCENE}_0.mcap" \
  --robot-id "$ROBOT_ID" --robot-run-id "$ROBOT_RUN_ID" \
  --expected-message-count "$PUBLISHED_COUNT"
