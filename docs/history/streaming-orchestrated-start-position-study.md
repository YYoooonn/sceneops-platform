# Orchestrated start position: bridge RUN_START to pinned Capture

Point-in-time verification record (documentation category C). The current contract
is in [Streaming transport](../architecture/streaming-transport.md) §14, §26 and §28.

| | |
| --- | --- |
| Date | 2026-10-09 |
| Branch / base HEAD | `dev` / `7806c72` (working tree with the change uncommitted) |
| Environment | macOS host, Docker Compose; `apache/kafka:3.9.2` single-node KRaft; `streaming-bridge` and `capture` images rebuilt from the working tree |
| Question | Does the normal orchestrated path (bridge acknowledges `RUN_START` -> launcher -> `capture --start-position`) read the topic from `RUN_START` instead of scanning its history? |

## Workload

A fresh topic with 3 partitions, preloaded with 30 background runs of 1,000 telemetry
records each plus `RUN_START`/`RUN_END` (partition end offsets 9,018 / 7,014 / 14,028,
30,060 records). The real bridge container (`python -m sceneops_streaming_bridge`,
`--exit-after-idle-seconds 4`) bridged 400 `nav_msgs/Odometry` messages published by
`ros2 topic pub` from a separate container. The launcher's `streaming_wait_run_started`
read the position; the Capture container was started from it after the bridge was already
up.

## Functional result

```text
bridge_summary    published 400, failed 0
run_started       partition 0, offset 9018 (bridge start -> position: 1.1 s, including
                  container start, producer connect and the launcher's 1 s poll)
capture           exit 0, 400 messages, finalization explicit_run_end
receipt kafka     partition 0, first_offset 9018 (= the acknowledged RUN_START), last_offset 9419
```

A first attempt in which `ros2 topic pub` published unstamped messages made the bridge
reject all 400 (zero source timestamp), exit non-zero and Capture refuse the run; this
was a test-input error, and it showed the failure path end to end.

## Scan avoided

The same completed run captured from the same topic, three trials each, instrumented by
wrapping `KafkaTelemetryConsumer.poll` (a throwaway harness, not in the repository):

| Mode | Records consumed before `RUN_START` | First record | `RUN_START` seen | Capture total |
| --- | --- | --- | --- | --- |
| pinned (`--start-position`) | 0 | 0.01-0.06 s | 0.01-0.06 s | 1.04-1.09 s |
| no hint (subscribe, scan) | 26,426 | 3.07 s | 3.31-3.33 s | 3.33-3.34 s |

The pinned capture reads exactly the run's 402 records (`RUN_START`, 400 telemetry,
`RUN_END`); the scan read 26,828 records on all three partitions to reach the same
`RUN_START` and produced the same offsets and message count.

## Limits of this evidence

- One broker, one preloaded topic of 30,060 records, three trials per mode; the scan cost
  grows with the topic's history, so these numbers say nothing about other sizes.
- The timings are of a completed run captured afterwards. The live orchestrated run was
  verified for correctness (receipt offsets, counts), not timed.
- Partition invariants over several interleaved runs and the failure/race cases are
  covered by `tests/streaming/test_bridge_pinned_capture.py`, not by this run.

## Deferred

Consumer-group commit removal, retention (a retained-log lower bound on the position),
backpressure, persistent orchestration state and distributed scheduling.
