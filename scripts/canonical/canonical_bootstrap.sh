#!/usr/bin/env bash
# canonical_bootstrap.sh
#
# create-or-verify materialization of the frozen v0.0 canonical baseline
# family (see docs/development/canonical-baseline.md and
# config/baselines/canonical-v0.0.yaml, the single authoritative spec this
# script reads):
#
#   sceneops-scenes/v0.0      (10 Scenes,   0 Episodes)
#   sceneops-episodes/v0.0    (0 Scenes,   10 Episodes)
#   sceneops-canonical/v0.0   (10 Scenes,  10 Episodes)  -- default dev baseline
#
# Reuses the SAME real pipelines/jobs scripts/e2e/e2e_scene.sh and
# scripts/e2e/e2e_robot_learning.sh already exercise (dataset_scene_ingestion,
# raw_log_episode_building, align_episode, profile/validate_aligned_episode,
# export_learning_data v2-sharded, curate_episodes) — nothing here writes
# Postgres/MinIO state directly. Never touches the test-e2e-* identities
# those scripts own.
#
# ── create-or-verify contract (never silently repairs) ──────────────────────
#   all three baselines absent               -> CREATE all three, then verify
#   all three baselines present and matching -> verify only, exit 0, no mutation
#   anything else (partial / mismatched)     -> FAIL loudly; recovery requires
#                                                an explicit reset+rebuild
#                                                (FORCE=1 make local-reset &&
#                                                 make canonical-bootstrap)
#
# ── physical artifact reuse ──────────────────────────────────────────────────
# Each of the 10 scenes' real CAN replay -> MCAP recording happens exactly
# ONCE per bootstrap run (fresh every CREATE, via `rm -rf` + real ROS2
# replay — same as e2e_robot_learning.sh) and is reused by reference
# (robot_run_id) to build Episodes into BOTH sceneops-episodes/v0.0 and
# sceneops-canonical/v0.0 — build_episodes resolves episodes purely from the
# RobotRun's recording and the pipeline's own dataset_id/dataset_version, so
# reusing one RobotRun for two independent dataset-scoped Episode builds is
# already-safe existing behavior, not new architecture (see
# apps/worker/sceneops_worker/jobs/dataset/build_episodes.py). The RobotRun
# is created by the Recording Publisher + REGISTER_ROBOT_RUN (lib.sh's
# publish_and_register_robot_run), and the one RobotRun per scene is
# exactly what both build_episodes_for() calls below resolve through. Scene
# ingestion (Postgres SceneRecord + ArtifactStore SceneManifest) is NOT
# deduplicated between sceneops-scenes/v0.0 and sceneops-canonical/v0.0 --
# doing so would require new cross-dataset ArtifactRecord ownership
# semantics, which this task does not introduce. Independent canonical
# identity always wins over storage deduplication.
#
# Usage:
#   make canonical-bootstrap
#   bash scripts/canonical/canonical_bootstrap.sh
#
# Env overrides:
#   API_BASE_URL     (default: http://localhost:8000)
#   API_PREFIX       (default: /api/v1)
#   POLL_TIMEOUT     max poll attempts, 5s each (default: 60 = 5 min)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
source "$REPO_ROOT/scripts/e2e/lib.sh"
source "$SCRIPT_DIR/canonical_contract.sh"

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
API_PREFIX="${API_PREFIX:-/api/v1}"
POLL_TIMEOUT="${POLL_TIMEOUT:-60}"
COMPOSE="docker compose -f $REPO_ROOT/compose.yaml"

echo "=== canonical-bootstrap: v0.0 baseline family (create-or-verify) ==="
load_baseline_spec
echo "  baseline_version=$BASELINE_VERSION"
echo "  scenes ($( IFS=,; echo "${BASELINE_SCENES[*]}" ))"
echo "  $SCENES_DATASET_ID/$SCENES_DATASET_VERSION  $EPISODES_DATASET_ID/$EPISODES_DATASET_VERSION  $CANONICAL_DATASET_ID/$CANONICAL_DATASET_VERSION"
echo ""

# ── 1. Decide: create, verify-only, or fail on partial state ────────────────

echo "--- 1. Contract presence check ---"
STATUS_SCENES="$(contract_status "$SCENES_DATASET_ID" "$SCENES_DATASET_VERSION" "$SCENES_EXPECTED_SCENE_COUNT" "$SCENES_EXPECTED_EPISODE_COUNT")"
STATUS_EPISODES="$(contract_status "$EPISODES_DATASET_ID" "$EPISODES_DATASET_VERSION" "$EPISODES_EXPECTED_SCENE_COUNT" "$EPISODES_EXPECTED_EPISODE_COUNT")"
STATUS_CANONICAL="$(contract_status "$CANONICAL_DATASET_ID" "$CANONICAL_DATASET_VERSION" "$CANONICAL_EXPECTED_SCENE_COUNT" "$CANONICAL_EXPECTED_EPISODE_COUNT")"
echo "  $SCENES_DATASET_ID/$SCENES_DATASET_VERSION: $STATUS_SCENES"
echo "  $EPISODES_DATASET_ID/$EPISODES_DATASET_VERSION: $STATUS_EPISODES"
echo "  $CANONICAL_DATASET_ID/$CANONICAL_DATASET_VERSION: $STATUS_CANONICAL"
echo ""

ALL_ABSENT=1
ALL_MATCH=1
for status in "$STATUS_SCENES" "$STATUS_EPISODES" "$STATUS_CANONICAL"; do
  [ "$status" = "absent" ] || ALL_ABSENT=0
  [ "$status" = "matches" ] || ALL_MATCH=0
done

if [ "$ALL_MATCH" = "1" ]; then
  echo "=== All three baselines already exist and match the v0.0 contract. ==="
  echo "    Verifying (read-only, no mutation) ..."
  echo ""
  if ! verify_full_contract; then
    echo "❌ canonical-bootstrap: existing baseline family failed deep verification (corrupted/partial state)." >&2
    echo "   Not auto-repaired. Recover via: FORCE=1 make local-reset && make canonical-bootstrap" >&2
    exit 1
  fi
  echo ""
  echo "=== canonical-bootstrap: no-op (already verified) ==="
  exit 0
fi

if [ "$ALL_ABSENT" != "1" ]; then
  echo "❌ canonical-bootstrap: baseline family is in a partial/mismatched state:" >&2
  echo "   $SCENES_DATASET_ID/$SCENES_DATASET_VERSION: $STATUS_SCENES" >&2
  echo "   $EPISODES_DATASET_ID/$EPISODES_DATASET_VERSION: $STATUS_EPISODES" >&2
  echo "   $CANONICAL_DATASET_ID/$CANONICAL_DATASET_VERSION: $STATUS_CANONICAL" >&2
  echo "   Refusing to silently repair or partially create. Recover via:" >&2
  echo "     FORCE=1 make local-reset && make canonical-bootstrap" >&2
  exit 1
fi

echo "=== All three baselines absent — creating v0.0 baseline family ==="
echo ""

# ── 2. Upsert the three DatasetVersions up front ─────────────────────────────

echo "--- 2. Upsert Dataset/DatasetVersion (all three) ---"
upsert_dataset "$API_BASE_URL" "$SCENES_DATASET_ID" "SceneOps canonical baseline (Scene-only)" >/dev/null
upsert_dataset_version "$API_BASE_URL" "$SCENES_DATASET_ID" "$SCENES_DATASET_VERSION" "$SOURCE_ROOT_URI" >/dev/null
upsert_dataset "$API_BASE_URL" "$EPISODES_DATASET_ID" "SceneOps canonical baseline (Episode-only)" >/dev/null
upsert_dataset_version "$API_BASE_URL" "$EPISODES_DATASET_ID" "$EPISODES_DATASET_VERSION" >/dev/null
upsert_dataset "$API_BASE_URL" "$CANONICAL_DATASET_ID" "SceneOps canonical baseline (combined)" >/dev/null
upsert_dataset_version "$API_BASE_URL" "$CANONICAL_DATASET_ID" "$CANONICAL_DATASET_VERSION" "$SOURCE_ROOT_URI" >/dev/null
echo "  OK"
echo ""

# ── 3. Scene domain: dataset_scene_ingestion for scenes + canonical ─────────

SCENE_IDS_JSON="$(printf '%s\n' "${BASELINE_SCENES[@]}" | jq -R . | jq -s .)"

run_scene_ingestion() {
  local dataset_id="$1" version="$2"
  echo "--- Scene ingestion: $dataset_id/$version (${#BASELINE_SCENES[@]} scenes) ---"

  local payload
  payload="$(jq -n \
    --arg d "$dataset_id" --arg v "$version" \
    --arg fmt "$SOURCE_FORMAT" --arg root "$SOURCE_ROOT_URI" --arg fv "$SOURCE_FORMAT_VERSION" \
    --argjson scene_ids "$SCENE_IDS_JSON" \
    '{
      type: "dataset_scene_ingestion",
      dataset_id: $d,
      dataset_version: $v,
      force: true,
      params: {
        ingest_scenes: {
          source_format: $fmt,
          source_root_uri: $root,
          source_format_version: $fv,
          source_scene_ids: $scene_ids,
          mode: "upsert"
        },
        register_scene: {replace_existing: true},
        validate_scene: {require_target_channels: ["CAM_FRONT", "LIDAR_TOP"]},
        profile_scene: {profile_samples: true, profile_assets: true},
        build_scene_index: {},
        build_dataset_manifest: {}
      }
    }')"

  local create_resp pipeline_run_id pipeline_json
  create_resp="$(create_pipeline_run "$API_BASE_URL" "$payload")"
  pipeline_run_id="$(extract_pipeline_run_id "$create_resp")"
  dispatch_pipeline_run "$API_BASE_URL" "$pipeline_run_id" >/dev/null
  pipeline_json="$(poll_pipeline_terminal "$API_BASE_URL" "$pipeline_run_id" "$POLL_TIMEOUT" 5)"
  assert_pipeline_succeeded "$pipeline_json" "dataset_scene_ingestion should succeed for $dataset_id/$version" "$API_BASE_URL" "$pipeline_run_id"
  echo "  OK — pipeline_run_id=$pipeline_run_id"
  echo ""
}

run_scene_ingestion "$SCENES_DATASET_ID" "$SCENES_DATASET_VERSION"
run_scene_ingestion "$CANONICAL_DATASET_ID" "$CANONICAL_DATASET_VERSION"

# ── 4. Episode domain: CAN replay -> MCAP (once per scene) -> build_episodes ─
#      into BOTH sceneops-episodes/v0.0 and sceneops-canonical/v0.0 ──────────

echo "--- Build ROS2 sandbox image ---"
$COMPOSE --profile ros2 build ros2 >/dev/null
echo "  OK"
echo ""

upsert_robot "$API_BASE_URL" "$ROBOT_ID" "$ROBOT_PLATFORM" >/dev/null

declare -a EPISODES_EPISODE_IDS=()
declare -a CANONICAL_EPISODE_IDS=()

build_episodes_for() {
  local dataset_id="$1" version="$2" robot_run_id="$3"
  local payload create_resp pipeline_run_id pipeline_json tasks_json build_task episode_ids_json episode_count
  payload="$(cat <<JSON
{
  "type": "raw_log_episode_building",
  "dataset_id": "$dataset_id",
  "dataset_version": "$version",
  "force": true,
  "params": {
    "build_episodes": {
      "robot_id": "$ROBOT_ID",
      "robot_run_id": "$robot_run_id",
      "segmentation": {"strategy": "mission_boundary"}
    },
    "register_episode": {"replace_existing": true},
    "profile_episode": {"triggered": true}
  }
}
JSON
)"
  create_resp="$(create_pipeline_run "$API_BASE_URL" "$payload")"
  pipeline_run_id="$(extract_pipeline_run_id "$create_resp")"
  dispatch_pipeline_run "$API_BASE_URL" "$pipeline_run_id" >/dev/null
  pipeline_json="$(poll_pipeline_terminal "$API_BASE_URL" "$pipeline_run_id" "$POLL_TIMEOUT" 5)"
  assert_pipeline_succeeded "$pipeline_json" "raw_log_episode_building should succeed for $dataset_id/$version" "$API_BASE_URL" "$pipeline_run_id"

  tasks_json="$(fetch_pipeline_tasks "$API_BASE_URL" "$pipeline_run_id")"
  build_task="$(echo "$tasks_json" | jq '.tasks[] | select(.pipelineTaskId == "build_episodes")')"
  episode_ids_json="$(echo "$build_task" | jq -c '.result.rawResult.episode_ids')"
  episode_count="$(echo "$episode_ids_json" | jq 'length')"
  if [ "${episode_count:-0}" -lt 1 ]; then
    echo "❌ build_episodes produced 0 episodes for $dataset_id/$version (robot_run_id=$robot_run_id)" >&2
    exit 1
  fi
  echo "$episode_ids_json"
}

for SCENE_NAME in "${BASELINE_SCENES[@]}"; do
  echo "=== Scene: $SCENE_NAME ==="

  RUN_ID="run-${SCENE_NAME}-canonical-${BASELINE_VERSION}"
  BAG_DIR="/data/raw/rosbag/${SCENE_NAME}"
  MCAP_URI="${BAG_DIR}/${SCENE_NAME}_0.mcap"

  echo "--- Record CAN replay (ros2 Docker sandbox) — fresh MCAP, once per scene ---"
  rm -rf "${REPO_ROOT}${BAG_DIR}"
  $COMPOSE --profile ros2 run --rm ros2 sh -c " \
    timeout $RECORD_DURATION ros2 bag record -o $BAG_DIR --storage mcap \
      /vehicle/odom /vehicle/imu /vehicle/status /vehicle/control /mission/status & \
    sleep 2; \
    python3 /workspace/nodes/can_replay_node.py --scene $SCENE_NAME --rate $REPLAY_RATE; \
    wait \
  "
  require_mcap_file "$REPO_ROOT" "$MCAP_URI"
  echo "  bag=${MCAP_URI}  OK"

  echo "--- Register RobotRun (artifact-backed; shared physical recording, reused by both Episode-bearing baselines) ---"
  publish_and_register_robot_run "$REPO_ROOT" "$API_BASE_URL" \
    "$ROBOT_ID" "$RUN_ID" "$MCAP_URI" ros2_bag "$ROBOT_PLATFORM" >/dev/null
  echo "  run_id=$RUN_ID"

  echo "--- build_episodes -> $EPISODES_DATASET_ID/$EPISODES_DATASET_VERSION ---"
  SCENE_EPISODES_EPISODE_IDS_JSON="$(build_episodes_for "$EPISODES_DATASET_ID" "$EPISODES_DATASET_VERSION" "$RUN_ID")"
  while read -r one_id; do EPISODES_EPISODE_IDS+=("$one_id"); done < <(echo "$SCENE_EPISODES_EPISODE_IDS_JSON" | jq -r '.[]')

  echo "--- build_episodes -> $CANONICAL_DATASET_ID/$CANONICAL_DATASET_VERSION (reuses same RobotRun/MCAP) ---"
  SCENE_CANONICAL_EPISODE_IDS_JSON="$(build_episodes_for "$CANONICAL_DATASET_ID" "$CANONICAL_DATASET_VERSION" "$RUN_ID")"
  while read -r one_id; do CANONICAL_EPISODE_IDS+=("$one_id"); done < <(echo "$SCENE_CANONICAL_EPISODE_IDS_JSON" | jq -r '.[]')

  echo "  OK"
  echo ""
done

echo "=== All scenes built: ${#EPISODES_EPISODE_IDS[@]} episode(s) in $EPISODES_DATASET_ID, ${#CANONICAL_EPISODE_IDS[@]} in $CANONICAL_DATASET_ID ==="
echo ""

# ── 5. Align/profile/validate + export_learning_data (v2-sharded) + curate ──

align_profile_validate_export_curate() {
  local dataset_id="$1" version="$2"
  shift 2
  local episode_ids=("$@")
  local episode_id align_payload align_create_resp align_job_id align_job_json aligned_artifact_id
  local stage_payload stage_create_resp stage_job_id stage_job_json
  local export_inputs=()

  for episode_id in "${episode_ids[@]}"; do
    echo "--- align/profile/validate $episode_id ($dataset_id/$version) ---"
    align_payload="$(cat <<JSON
{
  "type": "align_episode",
  "dataset_id": "$dataset_id",
  "dataset_version": "$version",
  "force": true,
  "params": {
    "episode_id": "$episode_id",
    "alignment_config": {
      "target_frequency_hz": $ALIGN_FREQUENCY_HZ,
      "tolerance_us": 200000,
      "max_gap_us": 2000000
    },
    "source_context": {"source_clock": "mcap_log_time"}
  }
}
JSON
)"
    align_create_resp="$(create_job "$API_BASE_URL" "$align_payload")"
    align_job_id="$(extract_job_id "$align_create_resp")"
    execute_job "$API_BASE_URL" "$align_job_id" >/dev/null
    align_job_json="$(poll_job_terminal "$API_BASE_URL" "$align_job_id" "$POLL_TIMEOUT" 3)"
    assert_job_succeeded "$align_job_json" "align_episode should succeed for $episode_id"
    aligned_artifact_id="$(echo "$align_job_json" | jq -r '.job.result.aligned_artifact_id')"

    for job_type in profile_aligned_episode validate_aligned_episode; do
      stage_payload="$(cat <<JSON
{
  "type": "$job_type",
  "dataset_id": "$dataset_id",
  "dataset_version": "$version",
  "force": true,
  "params": {"episode_id": "$episode_id", "aligned_artifact_id": "$aligned_artifact_id"}
}
JSON
)"
      stage_create_resp="$(create_job "$API_BASE_URL" "$stage_payload")"
      stage_job_id="$(extract_job_id "$stage_create_resp")"
      execute_job "$API_BASE_URL" "$stage_job_id" >/dev/null
      stage_job_json="$(poll_job_terminal "$API_BASE_URL" "$stage_job_id" "$POLL_TIMEOUT" 3)"
      assert_job_succeeded "$stage_job_json" "$job_type should succeed for $episode_id"
    done

    export_inputs+=("{\"episode_id\": \"$episode_id\", \"aligned_artifact_id\": \"$aligned_artifact_id\"}")
  done
  echo ""

  echo "--- export_learning_data (v2-sharded, ${#export_inputs[@]} episode(s)) -> $dataset_id/$version ---"
  local export_inputs_json export_payload export_create_resp export_job_id export_job_json export_manifest_artifact_id
  export_inputs_json="[$(IFS=,; echo "${export_inputs[*]}")]"
  export_payload="$(jq -n --argjson inputs "$export_inputs_json" --arg d "$dataset_id" --arg v "$version" \
    '{type: "export_learning_data", dataset_id: $d, dataset_version: $v, force: true, params: {inputs: $inputs}}')"
  export_create_resp="$(create_job "$API_BASE_URL" "$export_payload")"
  export_job_id="$(extract_job_id "$export_create_resp")"
  execute_job "$API_BASE_URL" "$export_job_id" >/dev/null
  export_job_json="$(poll_job_terminal "$API_BASE_URL" "$export_job_id" "$POLL_TIMEOUT" 3)"
  assert_job_succeeded "$export_job_json" "export_learning_data should succeed for $dataset_id/$version"
  export_manifest_artifact_id="$(echo "$export_job_json" | jq -r '.job.result.manifest_artifact_id')"
  echo "  export_id=$(echo "$export_job_json" | jq -r '.job.result.export_id')  manifest_artifact_id=$export_manifest_artifact_id  shard_counts=$(echo "$export_job_json" | jq -c '.job.result.shard_counts')"
  echo ""

  echo "--- curate_episodes (unrestricted policy) -> $dataset_id/$version ---"
  local curate_payload curate_create_resp curate_job_id curate_job_json
  curate_payload="$(cat <<JSON
{
  "type": "curate_episodes",
  "dataset_id": "$dataset_id",
  "dataset_version": "$version",
  "force": true,
  "params": {
    "learning_data_export_manifest_artifact_id": "$export_manifest_artifact_id",
    "policy": {}
  }
}
JSON
)"
  curate_create_resp="$(create_job "$API_BASE_URL" "$curate_payload")"
  curate_job_id="$(extract_job_id "$curate_create_resp")"
  execute_job "$API_BASE_URL" "$curate_job_id" >/dev/null
  curate_job_json="$(poll_job_terminal "$API_BASE_URL" "$curate_job_id" "$POLL_TIMEOUT" 3)"
  assert_job_succeeded "$curate_job_json" "curate_episodes should succeed for $dataset_id/$version"
  echo "  selected=$(echo "$curate_job_json" | jq -r '.job.result.selected_count')/$(echo "$curate_job_json" | jq -r '.job.result.candidate_count')"
  echo ""
}

align_profile_validate_export_curate "$EPISODES_DATASET_ID" "$EPISODES_DATASET_VERSION" "${EPISODES_EPISODE_IDS[@]}"
align_profile_validate_export_curate "$CANONICAL_DATASET_ID" "$CANONICAL_DATASET_VERSION" "${CANONICAL_EPISODE_IDS[@]}"

# ── 6. Post-create verification (never report success without it) ─────────

echo "--- Post-create verification ---"
if ! verify_full_contract; then
  echo "❌ canonical-bootstrap: baseline family created but failed post-create verification." >&2
  exit 1
fi

echo ""
echo "=== canonical-bootstrap: v0.0 baseline family created and verified ==="
