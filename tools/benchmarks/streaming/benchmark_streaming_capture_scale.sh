#!/usr/bin/env bash
# benchmark_streaming_capture_scale.sh — reproducible entry point for the
# Phase 6.6 streaming capture scale/backpressure benchmark
# (tools/benchmarks/streaming/benchmark_streaming_capture_scale.py). Real Kafka broker,
# real MCAP capture, no synthetic mocking.
#
# Runs the produce phase, then the capture phase, and prints one combined
# JSON report.
#
# Usage:
#   tools/benchmarks/streaming/benchmark_streaming_capture_scale.sh 3000 64
#   tools/benchmarks/streaming/benchmark_streaming_capture_scale.sh 30000 64
#   tools/benchmarks/streaming/benchmark_streaming_capture_scale.sh 100000 64
#   tools/benchmarks/streaming/benchmark_streaming_capture_scale.sh 5000 1000000   # large payload

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
cd "$REPO_ROOT"

MESSAGE_COUNT="${1:?Usage: $0 MESSAGE_COUNT [PAYLOAD_BYTES]}"
PAYLOAD_BYTES="${2:-64}"
ROBOT_RUN_ID="bench-$(date +%s)-$$"

COMPOSE="docker compose --env-file .env.local"

echo "=== benchmark: message_count=$MESSAGE_COUNT payload_bytes=$PAYLOAD_BYTES robot_run_id=$ROBOT_RUN_ID ===" >&2

PRODUCE_JSON="$($COMPOSE --profile streaming run --rm -T -v "$REPO_ROOT/tools/benchmarks:/workspace/tools/benchmarks:ro" --entrypoint /ros_entrypoint.sh capture python3 \
  /workspace/tools/benchmarks/streaming/benchmark_streaming_capture_scale.py \
  --phase produce --robot-run-id "$ROBOT_RUN_ID" \
  --message-count "$MESSAGE_COUNT" --payload-bytes "$PAYLOAD_BYTES")"
echo "  produce: $PRODUCE_JSON" >&2

CAPTURE_JSON="$($COMPOSE --profile streaming run --rm -T -v "$REPO_ROOT/tools/benchmarks:/workspace/tools/benchmarks:ro" --entrypoint /ros_entrypoint.sh capture python3 \
  /workspace/tools/benchmarks/streaming/benchmark_streaming_capture_scale.py \
  --phase capture --robot-run-id "$ROBOT_RUN_ID" \
  --message-count "$MESSAGE_COUNT" --payload-bytes "$PAYLOAD_BYTES")"
echo "  capture: $CAPTURE_JSON" >&2

jq -n \
  --argjson produce "$PRODUCE_JSON" \
  --argjson capture "$CAPTURE_JSON" \
  '{produce: $produce, capture: $capture}'
