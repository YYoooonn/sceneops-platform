#!/usr/bin/env bash
# benchmark_capture_backlog.sh — entry point for benchmark_capture_backlog.py: RobotRun
# arrival vs. Capture service, backlog and replay headroom against the real local
# broker and the real `sceneops_capture` CLI (docs/history/streaming-backpressure-study.md).
#
# Needs `make streaming-up`, the capture image and the locked reference recordings
# (`make reference-data-bootstrap`). Uses throwaway `bp.*` topics on the local broker and
# a throwaway Docker volume; it aborts below 1.6 GB free on the Docker VM disk.
#
# Usage (see the Python file's docstring for the subcommands):
#   tools/benchmarks/streaming/benchmark_capture_backlog.sh drain --name d1 --workload ref10 --runs 4 --slots 4
#   tools/benchmarks/streaming/benchmark_capture_backlog.sh regime --name r1 --workload ref5 \
#       --slots 1 --interval 1.5 --count 24 --stream-rate 1 --jitter 0.3
#   tools/benchmarks/streaming/benchmark_capture_backlog.sh summarize regime r1
# Results land in $RESULTS_DIR (default: ${TMPDIR:-/tmp}/sceneops-capture-backlog).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
cd "$REPO_ROOT"

RESULTS_DIR="${RESULTS_DIR:-${TMPDIR:-/tmp}/sceneops-capture-backlog}"
RECORDINGS_DIR="${REFERENCE_DATA_ROOT:-$REPO_ROOT/data/reference}/nuscenes-mini-v1/recordings"
PY="$SCRIPT_DIR/benchmark_capture_backlog.py"
mkdir -p "$RESULTS_DIR"

if [ "${1:-}" = summarize ]; then
  exec python3 "$PY" --results-dir "$RESULTS_DIR" "$@"
fi

VOLUME="sceneops-capture-backlog-bench"
docker volume create "$VOLUME" >/dev/null
trap 'docker volume rm "$VOLUME" >/dev/null 2>&1 || true' EXIT

docker compose --env-file .env.local --profile streaming run --rm -T \
  -v "$REPO_ROOT/tools/benchmarks:/workspace/tools/benchmarks:ro" \
  -v "$RECORDINGS_DIR:/ref:ro" \
  -v "$VOLUME:/out" \
  -v "$RESULTS_DIR:/results" \
  --entrypoint /ros_entrypoint.sh capture python3 \
  /workspace/tools/benchmarks/streaming/benchmark_capture_backlog.py --results-dir /results "$@"
