#!/usr/bin/env bash
# e2e_ros2_streaming.sh — real-data E2E: nuScenes CAN replay -> real ROS2
# DDS -> ROS2 Streaming Bridge -> real Kafka -> TelemetryConsumer.
#
# Three stages, all running inside the ros2 container (real rclpy):
#   1. Unit tests (ros2/nodes/tests/) -- no Kafka, no live ROS graph.
#   2. Real CAN replay + bridge, mirroring makefiles/ros2.mk's
#      ros2-can-replay-record background/foreground pattern: the bridge
#      is backgrounded (bounded by `timeout $DURATION`), then the replay
#      runs in the foreground, then we wait for the bridge's own bounded
#      shutdown to flush + close.
#   3. Verification (scripts/e2e/ros2_streaming_verify.py) -- consumes
#      everything published under this run's robot_run_id from the real
#      broker and checks it against what the bridge itself reported.
#
# Touches ONLY the ros2 container and the real Kafka broker -- no
# Postgres, no MinIO, no api/worker.
#
# Prerequisites (this script does not do either of these for you):
#   make streaming-up      # starts the local Kafka broker
#   (the ros2 image is built on demand by `docker compose ... run`)
#
# Usage:
#   make e2e-ros2-streaming
#   SCENE=scene-0061 RATE=10.0 DURATION=20 scripts/e2e/e2e_ros2_streaming.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

SCENE="${SCENE:-scene-0061}"
RATE="${RATE:-10.0}"
DURATION="${DURATION:-20}"
ROBOT_ID="${ROBOT_ID:-robot-nuscenes-streaming}"
# date +%N (nanoseconds) is a GNU extension -- not available on macOS/BSD
# date, which silently emits a literal "N" instead of erroring. $$ (this
# script's own PID) is portable and sufficient for per-invocation
# uniqueness here.
ROBOT_RUN_ID="run-ros2-streaming-$(date +%s)-$$"

COMPOSE="docker compose --env-file .env.local"

echo "=== [1/3] unit tests (ros2 container, no Kafka) ==="
$COMPOSE --profile ros2 run --rm ros2 python3 -m pytest /workspace/nodes/tests/ -v
echo ""

echo "=== [2/3] real CAN replay -> ROS2 -> bridge -> Kafka ==="
echo "  scene=$SCENE rate=$RATE duration=${DURATION}s"
echo "  robot_id=$ROBOT_ID robot_run_id=$ROBOT_RUN_ID"
echo ""

BRIDGE_LOG="$(mktemp)"
trap 'rm -f "$BRIDGE_LOG"' EXIT

# `timeout $DURATION` is the bridge's ONLY termination signal here (it's
# a persistent subscriber, not something that exits on its own) -- its
# `wait` therefore ALWAYS reports exit code 124 (timeout) by design, not
# as a failure. `set -e` INSIDE this inner script still aborts on a real
# can_replay_node.py failure (surfaces immediately, before the trailing
# `|| true`); only the bridge's own expected timeout exit is neutralized.
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

echo "  bridge reported: published=$PUBLISHED_COUNT failed=${FAILED_COUNT:-0}"
if [ "${FAILED_COUNT:-0}" != "0" ]; then
  echo "❌ bridge reported ${FAILED_COUNT} failed message(s) -- aborting" >&2
  exit 1
fi
echo ""

echo "=== [3/3] verify via real Kafka consumer (real rclpy deserialize, inside ros2 container) ==="
$COMPOSE --profile ros2 run --rm ros2 python3 /workspace/scripts/e2e/ros2_streaming_verify.py \
  --robot-id "$ROBOT_ID" \
  --robot-run-id "$ROBOT_RUN_ID" \
  --expected-count "$PUBLISHED_COUNT" \
  --scene "$SCENE"
