#!/usr/bin/env bash
# e2e_episode_learning.sh — the Episode learning journey (ADR-007 §33.6, §33.7,
# §34): canonical Episodes -> AlignedEpisodes -> LearningDataExport -> export
# verification and a LeRobot round trip.
#
#   canonical-bootstrap                        RobotRun -> canonical Episodes (L1/L2)
#   episode_learning_data_building pipeline    align_episode (explicit timeline /
#                                              association, pinned Episode revisions)
#                                              -> export_learning_data (the exact
#                                              aligned revisions, validated)
#   VALIDATE / PROFILE_ALIGNED_EPISODE         atomic jobs on that exact revision
#   export verification                        manifest, every shard checksum and
#                                              row count, SceneOpsDataset reads
#                                              (worker image)
#   LeRobot round trip                         lerobot-integration container
#                                              exports the pinned export; the
#                                              official LeRobot reader reads it back
#                                              and every frame equals the export's
#                                              dense window
#   retry                                      the same recipe on the same revisions
#                                              converges on the same ids
#
# The canonical Episodes are never rewritten: alignment is a derived revision.
# Data-plane steps run as one-shot containers; every platform operation goes
# through FastAPI. The host needs Docker Compose, curl and jq.
#
# Test-state class: REFERENCE_DERIVED (docs/development/test-matrix.md). The RobotRun is
# the golden reference contract's (BASELINE_ID, default ref-nuscenes-mini-full-10); no
# RobotRun is created, and nothing is written to the reference DatasetVersion. The
# Episodes, AlignedEpisodes and learning export of the journey live in a fixed, test-owned
# identity (DATASET_ID, default sceneops-test-episode-learning). Aligned revisions and the
# export are addressed by the revisions they consume, so a repeated run reuses the
# DatasetVersion and converges on the same records instead of adding new ones; a runtime
# reset (make local-reset) drops them.
#
# Prerequisites: `make local-up`, `make acquisition-image`, `make reference-data-bootstrap`, `make lerobot-image`,
# data/raw/nuscenes with v1.0-mini and can_bus.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"
source "$SCRIPT_DIR/lib.sh"

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
SOURCE_UNIT="${SOURCE_UNIT:-scene-0061}"
# The source RobotRun is the golden reference contract's (BASELINE_ID defaults to it);
# everything the journey writes goes to a fixed DatasetVersion of its own.
export DATASET_ID="${DATASET_ID:-sceneops-test-episode-learning}"
export FIXTURE="$SOURCE_UNIT"
source "$REPO_ROOT/scripts/canonical/baseline_lib.sh"

# The alignment, stated explicitly: a 5 Hz timeline over the Episode window on
# the Episode's own window clock; nearest association within 10 s for every
# channel, actions included. The wide tolerance and the nearest-action override
# are deliberate: a LeRobot dataset has no missing-value concept and dense
# projection has exactly one missing-value policy (error), so the round trip
# needs every numeric channel resolved at every step of the recording. Tight
# tolerances, or the semantic default for actions (hold the previous recorded
# action), leave the sparse control stream missing before its first sample and
# nothing densely exportable.
ALIGNMENT="$(jq -cn '{
  target_frequency_hz: 5.0, tolerance_us: 10000000, max_gap_us: 10000000,
  channel_policies: ({"/vehicle/control#steering": 0, "/vehicle/control#throttle": 0, "/vehicle/control#brake": 0}
    | with_entries(.value = {policy: "nearest", tolerance_us: 10000000}))}')"

# Canonical Episodes carry no task, and LeRobot requires one per frame: the caller
# states it in the export request (an episode's own task would win).
LEROBOT_TASK="drive the recorded scene"
LEROBOT_DIR_HOST="$REPO_ROOT/data/runs/e2e-lerobot/$DATASET_ID"
LEROBOT_DIR="/data/runs/e2e-lerobot/$DATASET_ID"
REPO_ID="lerobot-$DATASET_ID"
lerobot() {
  docker compose --env-file "$ENV_FILE" --profile lerobot run --rm -T "$@"
}
trap 'rm -rf "$LEROBOT_DIR_HOST"' EXIT

episodes_json() {
  api_get "$API_BASE_URL" "/episodes?dataset_id=$DATASET_ID&dataset_version=$DATASET_VERSION&limit=500"
}

episode_revisions() {
  episodes_json | jq -cS '[.episodes[] | {episodeId, manifestArtifactId, manifestChecksum}]'
}

echo "=== [0/7] canonical-bootstrap: RobotRun -> canonical Episodes ==="
require_api "$API_BASE_URL"
BASELINE="$("$REPO_ROOT/scripts/canonical/canonical_bootstrap.sh")"
echo "$BASELINE" | jq -c '{dataset_id, dataset_version, scene_count, episode_count, episode_ids}'
EPISODE_COUNT="$(echo "$BASELINE" | jq -r '.episode_count')"
check "$EPISODE_COUNT canonical Episode(s) are registered" [ "$EPISODE_COUNT" -ge 1 ]
EPISODE_REVISIONS="$(episode_revisions)"
PINS="$(episodes_json | jq -c '[.episodes[] | {
  episode_id: .episodeId, source_artifact_id: .manifestArtifactId,
  source_manifest_sha256: (.manifestChecksum | sub("^sha256:"; ""))}]')"
echo ""

echo "=== [1/7] episode_learning_data_building: align the pinned Episodes, export exactly those revisions ==="
PIPELINE_PARAMS="$(jq -cn --argjson pins "$PINS" --argjson alignment "$ALIGNMENT" \
  '{align_episode: {episodes: $pins, alignment_config: $alignment}}')"
# Not forced: the identical request returns the PipelineRun that already holds this result (a
# failed or interrupted one is redispatched), so a repeated journey adds no PipelineRun here.
# The forced retry below is what re-executes the stages every run.
PIPELINE="$(run_pipeline "$API_BASE_URL" episode_learning_data_building "$DATASET_ID" "$DATASET_VERSION" "$PIPELINE_PARAMS" \
  '{"force": false}')"
assert_pipeline_succeeded "$(fetch_pipeline_run "$API_BASE_URL" "$PIPELINE")" \
  "episode_learning_data_building should succeed" "$API_BASE_URL" "$PIPELINE"
fetch_pipeline_tasks "$API_BASE_URL" "$PIPELINE" | jq -r '.tasks[] | "  \(.pipelineTaskId): \(.status)"'
check "both stages succeeded" \
  [ "$(fetch_pipeline_tasks "$API_BASE_URL" "$PIPELINE" | jq '[.tasks[] | select(.status != "succeeded")] | length')" = 0 ]
ALIGN="$(task_json "$API_BASE_URL" "$PIPELINE" align_episode)"
EXPORT="$(task_json "$API_BASE_URL" "$PIPELINE" export_learning_data)"
EXPORT_INPUTS="$(echo "$ALIGN" | jq -c '.result.refs.export_inputs')"
echo "  align:  $(echo "$ALIGN" | jq -c '.result.summary | {episode_count, step_count}')"
echo "  export: $(echo "$EXPORT" | jq -c '.result.summary | {episode_count, row_counts, shard_counts}')"
check "every Episode was aligned" \
  [ "$(echo "$EXPORT_INPUTS" | jq 'length')" = "$EPISODE_COUNT" ]
for input in $(echo "$EXPORT_INPUTS" | jq -c '.[]'); do
  ALIGNED_ID="$(echo "$input" | jq -r '.aligned_artifact_id')"
  ALIGNED_CHECKSUM="$(echo "$input" | jq -r '.aligned_artifact_checksum')"
  EPISODE_ID="$(echo "$input" | jq -r '.episode_id')"
  ALIGNED_RECORD="$(api_get "$API_BASE_URL" "/artifacts/$ALIGNED_ID")"
  check "$ALIGNED_ID is derived from the Episode and the artifact checksum, not random" \
    [ "$(echo "$ALIGNED_ID" | grep -c '^aligned-')" = 1 ]
  check "it is a checksum-pinned aligned_episode_manifest in the Episode's DatasetVersion" \
    [ "$(echo "$ALIGNED_RECORD" | jq -r --arg c "$ALIGNED_CHECKSUM" --arg e "$EPISODE_ID" --arg ds "$DATASET_ID" --arg v "$DATASET_VERSION" \
        '[.artifact.kind, .artifact.checksum, .artifact.ownerId, .artifact.datasetId, .artifact.datasetVersion] | join(",") == ("aligned_episode_manifest," + $c + "," + $e + "," + $ds + "," + $v)')" = true ]
  check "the record carries the alignment clock and the exact source revision it consumed" \
    [ "$(echo "$ALIGNED_RECORD" | jq -r --argjson pins "$PINS" --arg e "$EPISODE_ID" \
        '.artifact.metadata.source_artifact_id == ($pins[] | select(.episode_id == $e) | .source_artifact_id) and (.artifact.metadata.source_clock | length) > 0')" = true ]
done
check "the canonical Episodes are unchanged by alignment and export" \
  [ "$(episode_revisions)" = "$EPISODE_REVISIONS" ]
MANIFEST_ARTIFACT_ID="$(echo "$EXPORT" | jq -r '.result.refs.manifest_artifact_id')"
EXPORT_ID="$(echo "$EXPORT" | jq -r '.result.refs.export_id')"
check "the export manifest ArtifactRecord id is deterministic" \
  [ "$(echo "$MANIFEST_ARTIFACT_ID" | grep -c '^lexport-')" = 1 ]
EXPORT_MANIFEST="$(api_get "$API_BASE_URL" "/artifacts/$MANIFEST_ARTIFACT_ID" | jq -c '.artifact')"
MANIFEST_URI="$(echo "$EXPORT_MANIFEST" | jq -r '.uri')"
MANIFEST_CHECKSUM="$(echo "$EXPORT_MANIFEST" | jq -r '.checksum')"
echo ""

echo "=== [2/7] retry: the same recipe on the same revisions converges ==="
PIPELINE_B="$(run_pipeline "$API_BASE_URL" episode_learning_data_building "$DATASET_ID" "$DATASET_VERSION" "$PIPELINE_PARAMS")"
assert_pipeline_succeeded "$(fetch_pipeline_run "$API_BASE_URL" "$PIPELINE_B")" "retry should succeed" "$API_BASE_URL" "$PIPELINE_B"
check "the same aligned revisions" \
  [ "$(task_json "$API_BASE_URL" "$PIPELINE_B" align_episode | jq -c '.result.refs.export_inputs')" = "$EXPORT_INPUTS" ]
check "the same export id and manifest record" \
  [ "$(task_json "$API_BASE_URL" "$PIPELINE_B" export_learning_data | jq -r '[.result.refs.export_id, .result.refs.manifest_artifact_id] | join(",")')" = "$EXPORT_ID,$MANIFEST_ARTIFACT_ID" ]
echo ""

echo "=== [3/7] VALIDATE / PROFILE_ALIGNED_EPISODE (atomic jobs) on that exact revision ==="
FIRST="$(echo "$EXPORT_INPUTS" | jq -c '.[0]')"
ANALYSIS_PARAMS="$(echo "$FIRST" | jq -c '{episode_id, aligned_artifact_id, aligned_artifact_checksum}')"
VALIDATION="$(run_job "$API_BASE_URL" validate_aligned_episode "$DATASET_ID" "$DATASET_VERSION" "$ANALYSIS_PARAMS")"
assert_job_succeeded "$VALIDATION" "validate_aligned_episode should succeed"
PROFILE="$(run_job "$API_BASE_URL" profile_aligned_episode "$DATASET_ID" "$DATASET_VERSION" "$ANALYSIS_PARAMS")"
assert_job_succeeded "$PROFILE" "profile_aligned_episode should succeed"
check "the aligned revision is structurally valid" [ "$(job_result "$VALIDATION" | jq -r '.valid')" = true ]
check "the profile pins the same aligned revision" \
  [ "$(job_result "$PROFILE" | jq -r '.aligned_artifact_id')" = "$(echo "$FIRST" | jq -r '.aligned_artifact_id')" ]
echo ""

echo "=== [4/7] export verification: manifest, shards and SceneOpsDataset reads (worker image) ==="
VERIFY="$(worker_python /workspace/scripts/e2e/verify_learning_export.py \
  --manifest-uri "$MANIFEST_URI" --manifest-checksum "$MANIFEST_CHECKSUM" \
  --expected-episode-count "$EPISODE_COUNT" --open-dataset)"
echo "$VERIFY" | jq -c '{ok, checks, shard_counts, dataset: (.dataset | {episode_count, step_count, dense_projection})}'
check "every verification check passed" [ "$(echo "$VERIFY" | jq -r '.ok')" = true ]
OBS_CHANNELS="$(echo "$VERIFY" | jq -r '.dataset.dense_projection.observation_channels | join(",")')"
ACTION_CHANNELS="$(echo "$VERIFY" | jq -r '.dataset.dense_projection.action_channels | join(",")')"
STEP_COUNT="$(echo "$VERIFY" | jq -r '.dataset.step_count')"
check "the export carries dense-projectable channels to round-trip" \
  [ -n "$OBS_CHANNELS" -a -n "$ACTION_CHANNELS" ]
check "the export holds exactly the aligned steps" \
  [ "$STEP_COUNT" = "$(echo "$ALIGN" | jq -r '.result.summary.step_count')" ]
echo ""

echo "=== [5/7] LeRobot round trip: pinned export -> lerobot-integration container -> official reader ==="
rm -rf "$LEROBOT_DIR_HOST"
mkdir -p "$LEROBOT_DIR_HOST"
worker_python /workspace/scripts/e2e/lerobot_build_request.py \
  --manifest-uri "$MANIFEST_URI" --manifest-checksum "$MANIFEST_CHECKSUM" \
  --dataset-id "$DATASET_ID" --dataset-version "$DATASET_VERSION" \
  --export-root-uri "$LEROBOT_DIR/$REPO_ID" --repo-id "$REPO_ID" \
  --observation-channels "$OBS_CHANNELS" --action-channels "$ACTION_CHANNELS" \
  --default-task "$LEROBOT_TASK" >"$LEROBOT_DIR_HOST/request.json"
lerobot lerobot-integration --request-file "$LEROBOT_DIR/request.json" --output-file "$LEROBOT_DIR/result.json"
check "the container wrote an IntegrationResult" [ -s "$LEROBOT_DIR_HOST/result.json" ]
READBACK="$(lerobot --entrypoint python lerobot-integration /workspace/scripts/e2e/lerobot_verify_export.py \
  --result-file "$LEROBOT_DIR/result.json" --export-root "$LEROBOT_DIR/$REPO_ID" \
  --manifest-uri "$MANIFEST_URI" --manifest-checksum "$MANIFEST_CHECKSUM" \
  --observation-channels "$OBS_CHANNELS" --action-channels "$ACTION_CHANNELS")"
echo "  $READBACK"
check "every LeRobot frame equals the export's dense window" [ "$(echo "$READBACK" | jq -r '.ok')" = true ]
check "the LeRobot dataset has the export's episodes and steps" \
  [ "$(echo "$READBACK" | jq -r '[.total_episodes, .total_frames] | join(",")')" = "$EPISODE_COUNT,$STEP_COUNT" ]
echo ""

echo "=== [6/7] a LeRobot export never overwrites its target ==="
set +e
lerobot lerobot-integration --request-file "$LEROBOT_DIR/request.json" --output-file "$LEROBOT_DIR/rerun-result.json" \
  >"$LEROBOT_DIR_HOST/rerun.log" 2>&1
RERUN_EXIT=$?
set -e
check "re-running against the populated target fails" [ "$RERUN_EXIT" -ne 0 ]
check "and says why" grep -q "already exists" "$LEROBOT_DIR_HOST/rerun.log"
check "and writes no result" [ ! -e "$LEROBOT_DIR_HOST/rerun-result.json" ]
echo ""

echo "=== [7/7] canonical state untouched by every derived step ==="
check "Episode revisions are exactly those registered before alignment" \
  [ "$(episode_revisions)" = "$EPISODE_REVISIONS" ]
echo ""
echo "=== episode learning E2E complete: dataset=$DATASET_ID/$DATASET_VERSION export=$EXPORT_ID ==="
