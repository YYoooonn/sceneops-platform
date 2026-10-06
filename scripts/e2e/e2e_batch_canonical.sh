#!/usr/bin/env bash
# e2e_batch_canonical.sh — L1 -> L2 canonicalization from a batch recording.
#
#   reference fixture (nuScenes mini, prepared by reference-data-bootstrap)
#     -> prepared batch MCAP                  finalized sensor-bearing recording,
#                                             checked against the corpus lock
#     -> recording-publisher container        publication, read in place
#     -> POST /robot-runs:register            RobotRun
#     -> canonical-bootstrap                  recording_scene_building and
#                                             recording_episode_building over
#                                             that one RobotRun
#     -> canonical Scenes and Episodes        source-faithful, checksum-pinned,
#                                             validated, profiled
#
# It proves that one registered recording canonicalizes into both domains
# without either rewriting the other, and that the whole path is re-runnable.
# Retry, dedup, force, replacement and failure semantics of the pipelines are
# infrastructure contracts, covered by `make test-infrastructure`.
#
# Data-plane steps run as one-shot containers; every platform operation goes
# through FastAPI. The host needs Docker Compose, curl and jq plus the API
# port: no uv, no PostgreSQL or MinIO access.
#
# Test-state class: MUTATING_ACQUISITION_TEST (docs/development/test-matrix.md). The
# RobotRun, its Scenes, Episodes and artifacts are durable and nothing removes them,
# so it runs only on a disposable runtime: `make local-reset && make e2e-batch-canonical
# DISPOSABLE_RUNTIME=1` (it refuses otherwise).
#
# Prerequisites: `make local-up`, `make acquisition-image` and
# `make reference-data-bootstrap` (data/reference holds the prepared recording).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"
source "$SCRIPT_DIR/lib.sh"

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
SOURCE_UNIT="${SOURCE_UNIT:-scene-0061}"
SUFFIX="$(date +%s)-$$"
export BASELINE_ID="${BASELINE_ID:-test-e2e-batch-$SUFFIX}"
export FIXTURE="$SOURCE_UNIT"
source "$REPO_ROOT/scripts/canonical/baseline_lib.sh"
# Registers a RobotRun of its own (the thing under test): disposable runtimes only.
baseline_guard_identity
RUN_ID="$(baseline_run_id "$SOURCE_UNIT")"
CLOCK="vehicle.source_time"

latest_run() { # <pipeline-type> -> pipeline_run_id of the newest run of the scope
  api_get "$API_BASE_URL" "/pipelines/runs?pipeline_type=$1&dataset_id=$DATASET_ID&dataset_version=$DATASET_VERSION&limit=100" \
    | jq -r '.pipelineRuns | sort_by(.createdAt) | last | .pipelineRunId'
}

echo "=== [0/5] control plane reachable ==="
require_api "$API_BASE_URL"
echo "  ✅  API healthy at $API_BASE_URL  baseline=$BASELINE_ID  run=$RUN_ID"
echo ""

echo "=== [1/5] prepared reference recording, verified against the corpus lock ==="
baseline_resolve full
FIXTURE_JSON="$(echo "$BASELINE_FIXTURES" | jq -c '.[0]')"
echo "$FIXTURE_JSON" | jq -c '{fixture_id, path, sha256: .recording.sha256, message_count: .recording.message_count}'
TOPIC_COUNTS="$(echo "$FIXTURE_JSON" | jq -c '.recording.topic_counts')"
echo ""

echo "=== [2/5] publish + POST /robot-runs:register -> RobotRun ==="
baseline_register_fixture "$FIXTURE_JSON"
RUN="$(api_get "$API_BASE_URL" "/robot-runs/$RUN_ID" | jq -c '.robotRun')"
check "the RobotRun pins exactly the locked recording" \
  [ "$(api_get "$API_BASE_URL" "/artifacts/$(echo "$RUN" | jq -r '.recordingArtifactId')" | jq -r '.artifact.checksum')" = "$(echo "$FIXTURE_JSON" | jq -r '.recording.sha256')" ]
echo ""

echo "=== [3/5] canonical-bootstrap: the same RobotRun -> Scenes and Episodes ==="
BASELINE="$("$REPO_ROOT/scripts/canonical/canonical_bootstrap.sh")"
echo "$BASELINE" | jq -c '{dataset_id, dataset_version, scene_count, episode_count}'
SCENE_COUNT="$(echo "$BASELINE" | jq -r '.scene_count')"
check "the whole recording is one Scene (whole_recording segmentation)" [ "$SCENE_COUNT" -eq 1 ]
SCENE_RUN="$(latest_run recording_scene_building)"
EPISODE_RUN="$(latest_run recording_episode_building)"
SCENE_BUILD="$(task_json "$API_BASE_URL" "$SCENE_RUN" build_recording_scenes)"
EPISODE_BUILD="$(task_json "$API_BASE_URL" "$EPISODE_RUN" build_recording_episodes)"
echo ""

echo "=== [4/5] canonical Scenes ==="
SCENES="$(api_get "$API_BASE_URL" "/scenes?dataset_id=$DATASET_ID&dataset_version=$DATASET_VERSION&limit=500")"
SCENE_MANIFEST_IDS="$(echo "$SCENE_BUILD" | jq -c '.result.refs.manifest_artifact_ids | sort')"
check "SceneRecords are the registered set and point at the source RobotRun" \
  [ "$(echo "$SCENES" | jq -r --arg run "$RUN_ID" '[.scenes[] | select(.robotRunId == $run)] | length')" = "$SCENE_COUNT" ]
check "each SceneRecord pins exactly a manifest revision the build published" \
  [ "$(echo "$SCENES" | jq -c '[.scenes[].manifestArtifactId] | sort')" = "$SCENE_MANIFEST_IDS" ]
check "segment windows are on the declared source clock" \
  [ "$(echo "$SCENES" | jq -r '[.scenes[].windowClock] | unique | join(",")')" = "sensor.header_stamp" ]
check "observed channels are the configured topics, verbatim" \
  [ "$(echo "$SCENES" | jq -r '[.scenes[].observedChannels[]] | unique | join(",")')" = "/camera/front/image/compressed,/lidar/top/points" ]
for id in $(echo "$SCENES" | jq -r '.scenes[].manifestArtifactId'); do
  api_get "$API_BASE_URL" "/artifacts/$id" \
    | jq -e '.artifact.kind == "scene_manifest" and (.artifact.checksum | startswith("sha256:"))' >/dev/null \
    || fail "manifest artifact $id is not a checksum-pinned scene_manifest"
done
echo "  ✅  every SceneRecord's manifest is a checksum-pinned scene_manifest ArtifactRecord"
PAYLOADS="$(echo "$SCENE_BUILD" | jq -r '.result.summary.payload_artifact_count')"
check "OBSERVATION_PAYLOAD ArtifactRecords exist for every planned payload ($PAYLOADS)" \
  [ "$(count_artifacts "$API_BASE_URL" observation_payload robot_run "$RUN_ID")" = "$PAYLOADS" ]
check "DatasetVersion scene summary was written by the registrar" \
  [ "$(api_get "$API_BASE_URL" "/datasets/$DATASET_ID/versions/$DATASET_VERSION" | jq -r '.version.scene.sceneCount')" = "$SCENE_COUNT" ]
echo ""

echo "=== [5/5] canonical Episodes, and the two domains stay independent ==="
EPISODES="$(api_get "$API_BASE_URL" "/episodes?dataset_id=$DATASET_ID&dataset_version=$DATASET_VERSION&limit=500")"
EPISODE="$(echo "$EPISODES" | jq -c '.episodes[0]')"
EPISODE_ID="$(echo "$EPISODE" | jq -r '.episodeId')"
check "one Episode per recorded task (mission-$SOURCE_UNIT)" \
  [ "$(echo "$EPISODE_BUILD" | jq -r '.result.summary.unit_keys | join(",")')" = "task-mission-$SOURCE_UNIT-000" ]
check "the EpisodeRecord points at the source RobotRun and a published revision" \
  [ "$(echo "$EPISODE" | jq -r '.robotRunId')$(echo "$EPISODES" | jq -c '[.episodes[].manifestArtifactId] | sort')" = "$RUN_ID$(echo "$EPISODE_BUILD" | jq -c '.result.refs.manifest_artifact_ids | sort')" ]
check "the Episode window is on the declared source clock" [ "$(echo "$EPISODE" | jq -r '.windowClock')" = "$CLOCK" ]
MANIFEST="$(api_get "$API_BASE_URL" "/episodes/$EPISODE_ID/manifest" | jq -c '.manifest')"
check "the window is [running marker, completed marker + 1)" \
  [ "$(echo "$MANIFEST" | jq '[.events[].timestamp_ns] as $e | [.lineage.source.start_timestamp_ns == ($e | min), .lineage.source.end_timestamp_ns == ($e | max) + 1] | all')" = true ]
check "every recorded action, state and observation is kept (no resampling)" \
  [ "$(echo "$MANIFEST" | jq -c --argjson c "$TOPIC_COUNTS" '[([.actions[] | select(.topic == "/vehicle/control")] | length) == $c["/vehicle/control"], ([.states[] | select(.topic == "/vehicle/odom")] | length) == $c["/vehicle/odom"], ([.observations[]] | length) == $c["/camera/front/image/compressed"]] | all')" = true ]
check "streams stay asynchronous (action and odometry timestamps differ)" \
  [ "$(echo "$MANIFEST" | jq '([.actions[].timestamp_ns] - [.states[] | select(.topic == "/vehicle/odom") | .timestamp_ns] | length) > 0')" = true ]
check "action values are the configured fields, verbatim" \
  [ "$(echo "$MANIFEST" | jq -r '.actions[0].values | keys | join(",")')" = "brake,steering,throttle" ]
check "no label, outcome or alignment field exists in the manifest" \
  [ "$(echo "$MANIFEST" | jq '[has("outcome"), has("task"), has("control_frequency_hz"), has("annotations")] | any')" = false ]
check "DatasetVersion episode summary was written by the registrar" \
  [ "$(api_get "$API_BASE_URL" "/datasets/$DATASET_ID/versions/$DATASET_VERSION" | jq -r '.version.episode.episodeCount')" = "$(echo "$EPISODES" | jq '.episodes | length')" ]
check "the Episode build reused every camera payload the Scene build extracted" \
  [ "$(echo "$EPISODE_BUILD" | jq -r '.result.summary.created_payload_count')" = 0 ]
check "Scene and Episode payloads are one set on the RobotRun" \
  [ "$(count_artifacts "$API_BASE_URL" observation_payload robot_run "$RUN_ID")" = "$PAYLOADS" ]

for scene_id in $(echo "$SCENES" | jq -r '.scenes[].sceneId'); do
  api_get "$API_BASE_URL" "/scenes/$scene_id/quality" \
    | jq -e '.validation != null and .profile != null' >/dev/null \
    || fail "Scene $scene_id has no validation/profile run for its current revision"
done
echo "  ✅  every Scene validated and profiled at its current revision"
check "the Episode is validated, profiled and ready at its current revision" \
  [ "$(api_get "$API_BASE_URL" "/episodes/$EPISODE_ID/quality" | jq -r '[.validation != null, .profile != null, .readiness == "ready"] | all')" = true ]
echo ""

echo "=== bootstrap converges: re-running it changes no canonical record ==="
BASELINE_AGAIN="$("$REPO_ROOT/scripts/canonical/canonical_bootstrap.sh")"
check "same Scenes and Episodes" [ "$BASELINE_AGAIN" = "$BASELINE" ]
check "SceneRecords and EpisodeRecords are byte-for-byte unchanged" \
  [ "$(api_get "$API_BASE_URL" "/scenes?dataset_id=$DATASET_ID&dataset_version=$DATASET_VERSION&limit=500" | jq -cS '[.scenes[] | del(.updatedAt)]')$(api_get "$API_BASE_URL" "/episodes?dataset_id=$DATASET_ID&dataset_version=$DATASET_VERSION&limit=500" | jq -cS '[.episodes[] | del(.updatedAt)]')" = "$(echo "$SCENES" | jq -cS '[.scenes[] | del(.updatedAt)]')$(echo "$EPISODES" | jq -cS '[.episodes[] | del(.updatedAt)]')" ]
echo ""
echo "=== batch canonical E2E complete: robot_run_id=$RUN_ID dataset=$DATASET_ID/$DATASET_VERSION scenes=$SCENE_COUNT ==="
