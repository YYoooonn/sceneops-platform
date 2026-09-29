#!/usr/bin/env bash
# canonical_contract.sh — shared spec-loading + contract-check functions for
# canonical_bootstrap.sh and canonical_verify.sh. Sourced by both, after they
# have already sourced scripts/e2e/lib.sh (api_url, fetch_artifacts_by_owner,
# assert_* helpers, etc.) themselves -- this file adds no new HTTP
# primitives, only baseline-specific checks built on top of lib.sh's.
#
# Every function here is read-only (GET requests + MinIO reads only) -- the
# create/mutate steps live exclusively in canonical_bootstrap.sh's own
# CREATE path, never in here. This is what lets canonical_verify.sh reuse
# every check function unchanged.

CANONICAL_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CANONICAL_REPO_ROOT="$(cd "$CANONICAL_SCRIPT_DIR/../.." && pwd)"

# ── spec loading ─────────────────────────────────────────────────────────────

load_baseline_spec() {
  SPEC_JSON="$(cd "$CANONICAL_REPO_ROOT" && uv run python scripts/canonical/read_baseline_spec.py)"

  BASELINE_VERSION="$(echo "$SPEC_JSON" | jq -r '.baseline_version')"
  SOURCE_FORMAT="$(echo "$SPEC_JSON" | jq -r '.source.format')"
  SOURCE_FORMAT_VERSION="$(echo "$SPEC_JSON" | jq -r '.source.format_version')"
  SOURCE_ROOT_URI="$(echo "$SPEC_JSON" | jq -r '.source.root_uri')"

  BASELINE_SCENES=()
  while read -r scene_name; do
    BASELINE_SCENES+=("$scene_name")
  done < <(echo "$SPEC_JSON" | jq -r '.scenes[]')

  ROBOT_ID="$(echo "$SPEC_JSON" | jq -r '.robot.robot_id')"
  ROBOT_PLATFORM="$(echo "$SPEC_JSON" | jq -r '.robot.platform')"
  REPLAY_RATE="$(echo "$SPEC_JSON" | jq -r '.robot.replay_rate')"
  RECORD_DURATION="$(echo "$SPEC_JSON" | jq -r '.robot.record_duration_s')"
  ALIGN_FREQUENCY_HZ="$(echo "$SPEC_JSON" | jq -r '.robot.align_frequency_hz')"

  SCENES_DATASET_ID="$(echo "$SPEC_JSON" | jq -r '.datasets.scenes.dataset_id')"
  SCENES_DATASET_VERSION="$(echo "$SPEC_JSON" | jq -r '.datasets.scenes.version')"
  SCENES_EXPECTED_SCENE_COUNT="$(echo "$SPEC_JSON" | jq -r '.datasets.scenes.expected.scene_count')"
  SCENES_EXPECTED_EPISODE_COUNT="$(echo "$SPEC_JSON" | jq -r '.datasets.scenes.expected.episode_count')"

  EPISODES_DATASET_ID="$(echo "$SPEC_JSON" | jq -r '.datasets.episodes.dataset_id')"
  EPISODES_DATASET_VERSION="$(echo "$SPEC_JSON" | jq -r '.datasets.episodes.version')"
  EPISODES_EXPECTED_SCENE_COUNT="$(echo "$SPEC_JSON" | jq -r '.datasets.episodes.expected.scene_count')"
  EPISODES_EXPECTED_EPISODE_COUNT="$(echo "$SPEC_JSON" | jq -r '.datasets.episodes.expected.episode_count')"

  CANONICAL_DATASET_ID="$(echo "$SPEC_JSON" | jq -r '.datasets.canonical.dataset_id')"
  CANONICAL_DATASET_VERSION="$(echo "$SPEC_JSON" | jq -r '.datasets.canonical.version')"
  CANONICAL_EXPECTED_SCENE_COUNT="$(echo "$SPEC_JSON" | jq -r '.datasets.canonical.expected.scene_count')"
  CANONICAL_EXPECTED_EPISODE_COUNT="$(echo "$SPEC_JSON" | jq -r '.datasets.canonical.expected.episode_count')"
}

# ── top-level contract status (create-or-verify decision) ──────────────────

fetch_dataset_version() {
  curl -sS "$(api_url "$API_BASE_URL" "/datasets/$1/versions/$2")"
}

# contract_status <dataset_id> <version> <expected_scene_count> <expected_episode_count>
# Echoes exactly one of: "absent" | "matches" | "mismatch <details>"
#
# Deliberately reads LIVE counts (GET /scenes, GET /episodes) rather than
# DatasetVersionRecord.scene.sceneCount/episode.episodeCount. Scene's cached
# count is written once per dataset_scene_ingestion run covering all scenes
# at once, so it agrees with the live count -- but episode.episodeCount
# (PostgresDatasetVersionRepository.update_episode_summary) is a plain
# last-write-wins column overwrite, not an accumulator
# (packages/sceneops-db/sceneops_db/postgres/datasets.py). Since this
# baseline's Episode domain is built one raw_log_episode_building dispatch
# per scene (same per-scene-loop pattern scripts/e2e/e2e_robot_learning.sh
# already uses for MAX_SCENES>1), that cached field ends up holding only
# the LAST scene's episode count, never the cumulative total -- a
# pre-existing characteristic of the frozen DatasetVersion summary
# semantics, not something this script works around by writing to it
# differently. The live count is the correct source of truth here.
contract_status() {
  local dataset_id="$1" version="$2" expected_scene="$3" expected_episode="$4"
  local version_json
  version_json="$(fetch_dataset_version "$dataset_id" "$version")"

  if ! echo "$version_json" | jq -e '.version' >/dev/null 2>&1; then
    echo "absent"
    return 0
  fi

  local actual_scene actual_episode
  actual_scene="$(curl -sS "$(api_url "$API_BASE_URL" "/scenes?dataset_id=$dataset_id&dataset_version=$version")" | jq '.scenes | length')"
  actual_episode="$(curl -sS "$(api_url "$API_BASE_URL" "/episodes?dataset_id=$dataset_id&dataset_version=$version&limit=200")" | jq -r '.count // (.episodes | length)')"

  if [ "$actual_scene" = "$expected_scene" ] && [ "$actual_episode" = "$expected_episode" ]; then
    echo "matches"
  else
    echo "mismatch (scene_count actual=$actual_scene expected=$expected_scene; episode_count actual=$actual_episode expected=$expected_episode)"
  fi
}

# ── deep, read-only domain verification ──────────────────────────────────────

# verify_scene_domain <dataset_id> <version> <expected_scene_count>
# Full check when expected_scene_count > 0 (ownership, quality readiness,
# manifest artifact); a plain zero-count check otherwise (Scene-only
# dataset's "unset" domain — see SceneVersionSummary.is_unset()).
verify_scene_domain() {
  local dataset_id="$1" version="$2" expected="$3"

  local scenes_json scene_count
  scenes_json="$(curl -sS "$(api_url "$API_BASE_URL" "/scenes?dataset_id=$dataset_id&dataset_version=$version")")"
  scene_count="$(echo "$scenes_json" | jq '.scenes | length')"

  if [ "$scene_count" != "$expected" ]; then
    echo "❌ [scene] $dataset_id/$version: expected $expected scene(s), GET /scenes returned $scene_count" >&2
    return 1
  fi

  # Cross-check the cached DatasetVersion summary against live membership --
  # an invariant, not an optional extra: neither is trusted alone (see
  # docs/development/canonical-baseline.md's summary-contract note).
  local cached_scene_count
  cached_scene_count="$(fetch_dataset_version "$dataset_id" "$version" | jq -r '.version.scene.sceneCount // 0')"
  if [ "$cached_scene_count" != "$expected" ]; then
    echo "❌ [scene] $dataset_id/$version: cached DatasetVersion.scene.sceneCount=$cached_scene_count, expected $expected (live=$scene_count)" >&2
    return 1
  fi

  if [ "$expected" = "0" ]; then
    echo "  [scene] $dataset_id/$version: OK — scene_count=0 (no Scene domain activity, as expected)" >&2
    return 0
  fi

  local mismatched
  mismatched="$(echo "$scenes_json" | jq --arg d "$dataset_id" --arg v "$version" \
    '[.scenes[] | select(.datasetId != $d or .datasetVersion != $v)] | length')"
  if [ "${mismatched:-0}" != "0" ]; then
    echo "❌ [scene] $dataset_id/$version: $mismatched scene(s) returned do not report this ownership" >&2
    return 1
  fi

  local quality_json readiness ground_truth manifest_uri
  quality_json="$(curl -sS "$(api_url "$API_BASE_URL" "/datasets/$dataset_id/versions/$version/quality")")"
  readiness="$(echo "$quality_json" | jq -r '.readiness')"
  ground_truth="$(echo "$quality_json" | jq -r '.groundTruth.hasGroundTruth')"
  manifest_uri="$(echo "$quality_json" | jq -r '.manifestUri // empty')"

  if [ "$readiness" != "ready" ]; then
    echo "❌ [scene] $dataset_id/$version: quality readiness=$readiness (expected ready)" >&2
    return 1
  fi
  if [ "$ground_truth" != "true" ]; then
    echo "❌ [scene] $dataset_id/$version: groundTruth.hasGroundTruth=$ground_truth (expected true)" >&2
    return 1
  fi
  if [ -z "$manifest_uri" ]; then
    echo "❌ [scene] $dataset_id/$version: quality.manifestUri missing" >&2
    return 1
  fi

  local artifacts_json manifest_count
  artifacts_json="$(fetch_artifacts_by_owner "$API_BASE_URL" "dataset_version" "$dataset_id:$version")"
  manifest_count="$(echo "$artifacts_json" | jq '[.artifacts[] | select(.kind == "dataset_manifest")] | length')"
  if [ "${manifest_count:-0}" -lt 1 ]; then
    echo "❌ [scene] $dataset_id/$version: no dataset_manifest artifact registered" >&2
    return 1
  fi

  echo "  [scene] $dataset_id/$version: OK — scene_count=$scene_count readiness=ready groundTruth=true manifest present" >&2
  return 0
}

# verify_episode_domain <dataset_id> <version> <expected_episode_count> [--open-dataset]
# Full check when expected_episode_count > 0 (ownership, learning export
# manifest byte-level verification via verify_learning_export.py, curation
# artifact); a plain zero-count check otherwise.
verify_episode_domain() {
  local dataset_id="$1" version="$2" expected="$3" open_dataset_flag="${4:-}"

  local episodes_json episode_count
  episodes_json="$(curl -sS "$(api_url "$API_BASE_URL" "/episodes?dataset_id=$dataset_id&dataset_version=$version&limit=200")")"
  episode_count="$(echo "$episodes_json" | jq -r '.count // (.episodes | length)')"

  if [ "$episode_count" != "$expected" ]; then
    echo "❌ [episode] $dataset_id/$version: expected $expected episode(s), GET /episodes returned $episode_count" >&2
    return 1
  fi

  # Cross-check the cached DatasetVersion summary against live membership --
  # this is exactly the invariant that was broken (episode.episodeCount
  # stuck at the last per-scene dispatch's own count instead of true
  # membership); never trust the cached summary alone, and never trust the
  # live count alone either (see docs/development/canonical-baseline.md).
  local cached_episode_count
  cached_episode_count="$(fetch_dataset_version "$dataset_id" "$version" | jq -r '.version.episode.episodeCount // 0')"
  if [ "$cached_episode_count" != "$expected" ]; then
    echo "❌ [episode] $dataset_id/$version: cached DatasetVersion.episode.episodeCount=$cached_episode_count, expected $expected (live=$episode_count)" >&2
    return 1
  fi

  if [ "$expected" = "0" ]; then
    echo "  [episode] $dataset_id/$version: OK — episode_count=0 (no Episode domain activity, as expected)" >&2
    return 0
  fi

  local mismatched
  mismatched="$(echo "$episodes_json" | jq --arg d "$dataset_id" --arg v "$version" \
    '[.episodes[] | select(.datasetId != $d or .datasetVersion != $v)] | length')"
  if [ "${mismatched:-0}" != "0" ]; then
    echo "❌ [episode] $dataset_id/$version: $mismatched episode(s) returned do not report this ownership" >&2
    return 1
  fi

  local artifacts_json manifest_artifacts manifest_count
  artifacts_json="$(fetch_artifacts_by_owner "$API_BASE_URL" "dataset_version" "$dataset_id:$version")"
  manifest_artifacts="$(echo "$artifacts_json" | jq '[.artifacts[] | select(.kind == "learning_data_export_manifest")]')"
  manifest_count="$(echo "$manifest_artifacts" | jq 'length')"
  if [ "${manifest_count:-0}" -ne 1 ]; then
    echo "❌ [episode] $dataset_id/$version: expected exactly 1 learning_data_export_manifest artifact, found $manifest_count" >&2
    return 1
  fi

  local curation_count
  curation_count="$(echo "$artifacts_json" | jq '[.artifacts[] | select(.kind == "episode_curation_manifest")] | length')"
  if [ "${curation_count:-0}" -lt 1 ]; then
    echo "❌ [episode] $dataset_id/$version: no episode_curation_manifest artifact registered" >&2
    return 1
  fi

  local manifest_uri manifest_checksum
  manifest_uri="$(echo "$manifest_artifacts" | jq -r '.[0].uri')"
  manifest_checksum="$(echo "$manifest_artifacts" | jq -r '.[0].checksum')"

  local first_episode_id observation_channels action_channels
  first_episode_id="$(echo "$episodes_json" | jq -r '.episodes[0].episodeId')"
  observation_channels="$(echo "$episodes_json" | jq -r --arg e "$first_episode_id" \
    '.episodes[] | select(.episodeId == $e) | .observationChannels | join(",")')"
  action_channels="$(echo "$episodes_json" | jq -r --arg e "$first_episode_id" \
    '.episodes[] | select(.episodeId == $e) | .actionChannels | join(",")')"

  local py_args=(scripts/canonical/verify_learning_export.py
    --manifest-uri "$manifest_uri"
    --manifest-checksum "$manifest_checksum"
    --expected-episode-count "$expected"
    --observation-channels "$observation_channels"
    --action-channels "$action_channels")
  if [ "$open_dataset_flag" = "--open-dataset" ]; then
    py_args+=(--open-dataset)
  fi

  echo "  [episode] $dataset_id/$version: verifying LearningDataExportManifest ($manifest_uri) ..." >&2
  if ! (cd "$CANONICAL_REPO_ROOT" && MINIO_ENDPOINT_URL="${MINIO_ENDPOINT_URL:-http://localhost:9000}" \
        MINIO_ROOT_USER="${MINIO_ROOT_USER:-minioadmin}" MINIO_ROOT_PASSWORD="${MINIO_ROOT_PASSWORD:-minioadmin}" \
        MINIO_BUCKET="${MINIO_BUCKET:-sceneops}" uv run python "${py_args[@]}" >&2); then
    echo "❌ [episode] $dataset_id/$version: learning-export verification failed" >&2
    return 1
  fi

  echo "  [episode] $dataset_id/$version: OK — episode_count=$episode_count, export manifest + curation artifact verified" >&2
  return 0
}

# verify_cross_dataset_isolation
# Section 13's requirement: the same real source scenes feed all three
# baselines, but no canonical Scene/Episode identity may be shared between
# them. Compares live GET /scenes and GET /episodes id sets pairwise.
verify_cross_dataset_isolation() {
  echo "--- Cross-dataset identity isolation ---" >&2

  local scenes_a scenes_b overlap
  scenes_a="$(curl -sS "$(api_url "$API_BASE_URL" "/scenes?dataset_id=$SCENES_DATASET_ID&dataset_version=$SCENES_DATASET_VERSION")" | jq -c '[.scenes[].sceneId] | sort')"
  scenes_b="$(curl -sS "$(api_url "$API_BASE_URL" "/scenes?dataset_id=$CANONICAL_DATASET_ID&dataset_version=$CANONICAL_DATASET_VERSION")" | jq -c '[.scenes[].sceneId] | sort')"
  overlap="$(jq -n --argjson a "$scenes_a" --argjson b "$scenes_b" '[$a[] as $x | select(([$b[] | select(. == $x)] | length) > 0)] | length')"
  if [ "${overlap:-0}" != "0" ]; then
    echo "❌ $overlap Scene id(s) shared between $SCENES_DATASET_ID/$SCENES_DATASET_VERSION and $CANONICAL_DATASET_ID/$CANONICAL_DATASET_VERSION" >&2
    return 1
  fi
  echo "  OK — $SCENES_DATASET_ID/$SCENES_DATASET_VERSION and $CANONICAL_DATASET_ID/$CANONICAL_DATASET_VERSION Scene ids fully disjoint" >&2

  local episodes_a episodes_b episode_overlap
  episodes_a="$(curl -sS "$(api_url "$API_BASE_URL" "/episodes?dataset_id=$EPISODES_DATASET_ID&dataset_version=$EPISODES_DATASET_VERSION&limit=200")" | jq -c '[.episodes[].episodeId] | sort')"
  episodes_b="$(curl -sS "$(api_url "$API_BASE_URL" "/episodes?dataset_id=$CANONICAL_DATASET_ID&dataset_version=$CANONICAL_DATASET_VERSION&limit=200")" | jq -c '[.episodes[].episodeId] | sort')"
  episode_overlap="$(jq -n --argjson a "$episodes_a" --argjson b "$episodes_b" '[$a[] as $x | select(([$b[] | select(. == $x)] | length) > 0)] | length')"
  if [ "${episode_overlap:-0}" != "0" ]; then
    echo "❌ $episode_overlap Episode id(s) shared between $EPISODES_DATASET_ID/$EPISODES_DATASET_VERSION and $CANONICAL_DATASET_ID/$CANONICAL_DATASET_VERSION" >&2
    return 1
  fi
  echo "  OK — $EPISODES_DATASET_ID/$EPISODES_DATASET_VERSION and $CANONICAL_DATASET_ID/$CANONICAL_DATASET_VERSION Episode ids fully disjoint" >&2

  return 0
}

# verify_full_contract
# Runs every deep check for all three baselines plus cross-dataset
# isolation. Used by both canonical_verify.sh and canonical_bootstrap.sh's
# post-create verification pass. Assumes load_baseline_spec has already run.
verify_full_contract() {
  local failed=0

  echo "--- Verifying sceneops-scenes/v0.0 ---" >&2
  verify_scene_domain "$SCENES_DATASET_ID" "$SCENES_DATASET_VERSION" "$SCENES_EXPECTED_SCENE_COUNT" || failed=1
  verify_episode_domain "$SCENES_DATASET_ID" "$SCENES_DATASET_VERSION" "$SCENES_EXPECTED_EPISODE_COUNT" || failed=1

  echo "--- Verifying sceneops-episodes/v0.0 ---" >&2
  verify_scene_domain "$EPISODES_DATASET_ID" "$EPISODES_DATASET_VERSION" "$EPISODES_EXPECTED_SCENE_COUNT" || failed=1
  verify_episode_domain "$EPISODES_DATASET_ID" "$EPISODES_DATASET_VERSION" "$EPISODES_EXPECTED_EPISODE_COUNT" || failed=1

  echo "--- Verifying sceneops-canonical/v0.0 ---" >&2
  verify_scene_domain "$CANONICAL_DATASET_ID" "$CANONICAL_DATASET_VERSION" "$CANONICAL_EXPECTED_SCENE_COUNT" || failed=1
  verify_episode_domain "$CANONICAL_DATASET_ID" "$CANONICAL_DATASET_VERSION" "$CANONICAL_EXPECTED_EPISODE_COUNT" --open-dataset || failed=1

  verify_cross_dataset_isolation || failed=1

  return "$failed"
}
