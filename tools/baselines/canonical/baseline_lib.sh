#!/usr/bin/env bash
# baseline_lib.sh — identity, registration and verification of a canonical L1/L2
# baseline built from the reference corpus (config/reference/, docs/development/
# reference-corpus.md).
#
# A baseline is: one RobotRun per selected corpus fixture (the fixture's prepared
# batch recording, published and registered through the production path), the
# canonical Scene and Episode revisions built from those RobotRuns, validated and
# profiled, and one immutable DatasetVersion composed of exactly those revisions
# (COMPOSE_DATASET_VERSION, ADR-015 §7). It contains nothing derived (no labels,
# sample views, ScenarioSets, predictions, evaluations or learning exports):
# those are L3 workflows run on top of a baseline.
#
# Selection: FIXTURE=<id> (one fixture) or REFERENCE_SCOPE=<scope> (default
# smoke-1). Fixtures and their locked recordings are resolved only through
# corpus.json + corpus.lock.json, by the dataset-acquisition integration's
# `reference resolve`. Nothing here converts source data: a recording that is
# not prepared, or does not match the lock, is an error that points at
# `make reference-data-bootstrap`.
#
# Identity: the baseline of a mode is the golden reference contract's
# (config/reference/<corpus>/reference_contract.json), whatever the selection.
# A selection only narrows which fixtures are acted on: `smoke-1` is scene-0061
# of the contract's baseline, never a second baseline with RobotRuns of its own.
# BASELINE_MODE (recording_import by default; streaming_acquisition for the
# streaming baseline) picks the contract mode. An explicit BASELINE_ID names a
# different baseline: that creates non-contract RobotRuns, which only a
# disposable runtime may hold (require_disposable_runtime, tools/e2e/lib.sh).
# DATASET_ID / ROBOT_ID default to the baseline's own and may be overridden: a
# journey that only needs Scenes / Episodes of a contract RobotRun composes them
# into a DatasetVersion of its own and registers no RobotRun. A DatasetVersion is
# immutable, so its default name (DATASET_VERSION) follows the selection: `baseline`
# for the contract's whole selection, baseline-<fixture or scope> for a part of it.
# Another selection is another composition and never rebinds a name.
#
# BASELINE_RECORDING_ASSERT names the function that checks a registered RobotRun's
# recording against its fixture: the streaming baseline (tools/baselines/streaming) pins
# its captured MCAP, not the locked one.
#
# Sourced by canonical_bootstrap.sh and canonical_verify.sh after
# tools/e2e/lib.sh. Every read goes through the FastAPI control plane.

REFERENCE_CORPUS="${REFERENCE_CORPUS:-nuscenes-mini-v1}"
REFERENCE_SCOPE="${REFERENCE_SCOPE:-smoke-1}"
FIXTURE="${FIXTURE:-}"
export REFERENCE_DATA_ROOT="${REFERENCE_DATA_ROOT:-$REPO_ROOT/data/reference}"

BASELINE_MODE="${BASELINE_MODE:-recording_import}"
REFERENCE_CONTRACT_FILE="${REFERENCE_CONTRACT_FILE:-$REPO_ROOT/config/reference/$REFERENCE_CORPUS/reference_contract.json}"
[ -r "$REFERENCE_CONTRACT_FILE" ] || fail "no reference contract at $REFERENCE_CONTRACT_FILE"
CONTRACT_BASELINE_ID="$(jq -r --arg m "$BASELINE_MODE" '.ingestion_modes[$m].baseline_id // empty' "$REFERENCE_CONTRACT_FILE")"
[ -n "$CONTRACT_BASELINE_ID" ] || fail "the reference contract has no ingestion mode '$BASELINE_MODE'"
CONTRACT_FIXTURE_IDS="$(jq -c '[.fixtures[].fixture_id] | sort' "$REFERENCE_CONTRACT_FILE")"
BASELINE_ID="${BASELINE_ID:-$CONTRACT_BASELINE_ID}"
ROBOT_ID="${ROBOT_ID:-robot-$BASELINE_ID}"
DATASET_ID="${DATASET_ID:-sceneops-$BASELINE_ID}"
# One name is one composition: the contract's whole selection is `baseline`, a part of
# it (smoke-1, FIXTURE=) is composed under a name of its own.
CONTRACT_SCOPE="$(jq -r '.corpus.scope // empty' "$REFERENCE_CONTRACT_FILE")"
if [ -z "$FIXTURE" ] && [ "$REFERENCE_SCOPE" = "$CONTRACT_SCOPE" ]; then
  DATASET_VERSION="${DATASET_VERSION:-baseline}"
else
  DATASET_VERSION="${DATASET_VERSION:-baseline-${FIXTURE:-$REFERENCE_SCOPE}}"
fi

# The resolved fixtures of the selection (a JSON array), set by baseline_resolve.
BASELINE_FIXTURES=""

log() { echo "$@" >&2; }

# baseline_run_id <fixture-id>
baseline_run_id() {
  echo "run-$BASELINE_ID-$1"
}

# baseline_resolve <full|lock-only>
# Resolves the selection through the corpus and its lock: one JSON object per
# fixture (id, source unit, locked recording facts, and for `full` the verified
# path of the prepared recording inside the containers). `full` also hashes every
# cached recording against the lock; `lock-only` reads no recording. Fails with
# the reason; never converts.
baseline_resolve() {
  local mode="$1" selection=() flags=() output status=0
  if [ -n "$FIXTURE" ]; then
    selection=(--fixture "$FIXTURE")
  else
    selection=(--scope "$REFERENCE_SCOPE")
  fi
  [ "$mode" = lock-only ] && flags=(--lock-only)
  output="$(compose run --rm -T --no-deps --user "$(id -u):$(id -g)" -e HOME=/tmp \
    reference-data reference resolve --corpus "/config/reference/$REFERENCE_CORPUS" \
    --cache-root /reference "${selection[@]}" ${flags[@]+"${flags[@]}"} </dev/null)" || status=$?
  if [ "$status" -ne 0 ]; then
    echo "$output" | jq -r 'select(.status == "failed") | "  \(.fixture_id):", (.problems[] | "    \(.)")' >&2 \
      || echo "$output" >&2
    fail "the reference corpus ($REFERENCE_CORPUS: ${FIXTURE:-$REFERENCE_SCOPE}) is not usable as locked; run \`make reference-data-bootstrap REFERENCE_SCOPE=$REFERENCE_SCOPE\` first"
  fi
  BASELINE_FIXTURES="$(echo "$output" | jq -sc .)"
}

# baseline_assert_recording <run-id> <locked-sha256> <locked-size-bytes>
# The registered RobotRun pins exactly the locked recording.
baseline_assert_recording() {
  local run_id="$1" sha="$2" size="$3" run recording
  run="$(api_get "$API_BASE_URL" "/robot-runs/$run_id")" || fail "RobotRun $run_id is not registered"
  recording="$(api_get "$API_BASE_URL" "/artifacts/$(echo "$run" | jq -r '.robotRun.recordingArtifactId')" | jq -c '.artifact')"
  [ "$(echo "$recording" | jq -r '.checksum')" = "$sha" ] \
    || fail "RobotRun $run_id pins recording $(echo "$recording" | jq -r '.checksum'), but the locked recording is $sha (a stale baseline: make local-reset, or use another BASELINE_ID)"
  [ "$(echo "$recording" | jq -r '.sizeBytes')" = "$size" ] \
    || fail "RobotRun $run_id pins a recording of $(echo "$recording" | jq -r '.sizeBytes') bytes, the locked recording is $size"
}

# baseline_register_fixture <resolved-fixture-json>
# The prepared recording -> publish -> REGISTER_ROBOT_RUN -> RobotRun, create-or-
# verify: a registered RobotRun is reused, and either way it must pin exactly the
# locked recording. The recording is published straight from the read-only
# reference mount; it is never copied.
baseline_register_fixture() {
  local fixture="$1" id run_id sha size path
  id="$(echo "$fixture" | jq -r '.fixture_id')"
  run_id="$(baseline_run_id "$id")"
  sha="$(echo "$fixture" | jq -r '.recording.sha256')"
  size="$(echo "$fixture" | jq -r '.recording.size_bytes')"
  path="$(echo "$fixture" | jq -r '.path')"
  if api_get "$API_BASE_URL" "/robot-runs/$run_id" >/dev/null 2>&1; then
    log "--- RobotRun $run_id is registered: reused"
  else
    { [ -n "$path" ] && [ "$path" != null ]; } \
      || fail "RobotRun $run_id is not registered and the fixture was resolved without its recording (baseline_resolve full)"
    log "--- $id -> publish -> register ($(basename "$path"))"
    register_recording "$API_BASE_URL" "$path" "$run_id" "$ROBOT_ID" file >/dev/null
    log "  ✅  RobotRun $run_id registered"
  fi
  baseline_assert_recording "$run_id" "$sha" "$size"
}

# baseline_run_registered <run-id>
baseline_run_registered() {
  api_get "$API_BASE_URL" "/robot-runs/$1" >/dev/null 2>&1
}

# baseline_all_registered — every fixture of the selection already has its RobotRun
# (so nothing has to be published or replayed, and the cached recordings need not
# be re-hashed against the lock).
baseline_all_registered() {
  local id
  for id in $(echo "$BASELINE_FIXTURES" | jq -r '.[].fixture_id'); do
    baseline_run_registered "$(baseline_run_id "$id")" || return 1
  done
}

# baseline_capacity_preflight <mode> — before the first write, refuse when the Docker VM
# cannot hold what the selection's unregistered fixtures will add
# (tools/dev/capacity_preflight.py). Exhausting the VM's disk stops the shared PostgreSQL
# server; a refusal here has written nothing. Registered RobotRuns whose builds are
# still pending are not estimated.
baseline_capacity_preflight() {
  local id report pending=()
  for id in $(echo "$BASELINE_FIXTURES" | jq -r '.[].fixture_id'); do
    baseline_run_registered "$(baseline_run_id "$id")" || pending+=(--fixture "$id")
  done
  [ "${#pending[@]}" -gt 0 ] || return 0
  report="$(python3 "$REPO_ROOT/tools/dev/capacity_preflight.py" --corpus "$REFERENCE_CORPUS" \
    --mode "$1" "${pending[@]}")" || {
    [ -z "$report" ] || log "capacity_preflight $report"
    fail "not enough free space for $1 of $(( ${#pending[@]} / 2 )) fixture(s) (capacity_preflight above; docs/development/local-development.md#disk-hygiene)"
  }
  log "capacity_preflight $report"
}

# baseline_ensure_dataset <name> — the Dataset exists (a namespace; its versions
# are only ever composed). Nothing is written when it already does.
baseline_ensure_dataset() {
  api_get "$API_BASE_URL" "/datasets/$DATASET_ID" >/dev/null 2>&1 && return 0
  upsert_dataset "$API_BASE_URL" "$DATASET_ID" "$1" >/dev/null
}

# baseline_version_composed — $DATASET_ID/$DATASET_VERSION exists. A DatasetVersion
# is complete from creation and never changes, so an existing one is verified, not
# built again.
baseline_version_composed() {
  api_get "$API_BASE_URL" "/datasets/$DATASET_ID/versions/$DATASET_VERSION" >/dev/null 2>&1
}

# baseline_compose <pins-json-array> — the baseline's DatasetVersion, composed of
# exactly these revision pins. Composing an existing name with the same pins
# converges; with other pins (a changed producer or build configuration) it fails
# loudly and the version keeps its composition.
baseline_compose() {
  local job
  job="$(compose_dataset_version "$API_BASE_URL" "$DATASET_ID" "$DATASET_VERSION" "$1")"
  log "  ✅  DatasetVersion $DATASET_ID/$DATASET_VERSION: $(job_result "$job" | jq -c '{created, member_count, scene_count, episode_count, composition_checksum}')"
}

# baseline_guard_identity — only the golden reference identity may be created on a
# runtime that holds the reference state; any other baseline registers RobotRuns
# that outlive the run.
baseline_guard_identity() {
  [ "$BASELINE_ID" = "$CONTRACT_BASELINE_ID" ] && return 0
  require_disposable_runtime "baseline '$BASELINE_ID' is not the golden reference identity '$CONTRACT_BASELINE_ID'"
}

# baseline_exact_membership — the baseline's robot holds exactly the selection. It
# does not when the selection (smoke-1, FIXTURE=) is a part of the contract's
# baseline: the rest of the contract's RobotRuns are then expected to be there.
baseline_exact_membership() {
  [ "$BASELINE_ID" != "$CONTRACT_BASELINE_ID" ] \
    || [ "$(echo "$BASELINE_FIXTURES" | jq -c '[.[].fixture_id] | sort')" = "$CONTRACT_FIXTURE_IDS" ]
}

# baseline_build_scope <pipeline-type> <build-task> <register-task> <profile-task> <run-id> <config>
# One pipeline run over one RobotRun's recording scope: build, register, validate,
# profile; no DatasetVersion. An identical succeeded run is reused, not run again
# (the revisions it registered are immutable). Prints the revision pins the run's
# register stage returned, for the composition.
baseline_build_scope() {
  local type="$1" build_task="$2" register_task="$3" profile_task="$4" run_id="$5" config="$6"
  local params pipeline
  params="$(jq -cn --arg b "$build_task" --arg p "$profile_task" \
    --arg run "$run_id" --argjson config "$config" '{
      ($b): {robot_run_id: $run, build_config: $config},
      ($p): {triggered: true}}')"
  pipeline="$(run_pipeline "$API_BASE_URL" "$type" "" "" "$params" '{"force": false}')"
  assert_pipeline_succeeded "$(fetch_pipeline_run "$API_BASE_URL" "$pipeline")" \
    "$type for $run_id should succeed" "$API_BASE_URL" "$pipeline" >&2
  log "  ✅  $type ($run_id): $pipeline"
  task_json "$API_BASE_URL" "$pipeline" "$register_task" | jq -c '.result.refs.revisions'
}

# baseline_verify <api>
# Read-only. Checks the baseline through supported APIs and artifact pins and
# prints one JSON summary on stdout; exits non-zero on the first violation.
# Resolves the fixtures through the lock if the caller has not. Besides the
# generic membership checks it asserts the reference baseline's expected shape
# per RobotRun (config/baselines/baseline_shape.json).
baseline_verify() {
  local api="$1" fixture id run_id sha size fixture_scenes fixture_episodes
  local scenes_ready episodes_ready quality manifest_pins mid mchecksum artifact
  local all_scenes all_episodes version expected registered
  local run_ids="[]" entries="[]" fixtures scenes_expected episodes_expected

  [ -n "$BASELINE_FIXTURES" ] || baseline_resolve lock-only
  scenes_expected="$(jq -r '.scenes_per_robot_run' "$BASELINE_CONFIG_DIR/baseline_shape.json")"
  episodes_expected="$(jq -r '.episodes_per_robot_run' "$BASELINE_CONFIG_DIR/baseline_shape.json")"

  # The registered RobotRuns of the baseline's robot are exactly the expected set.
  expected="$(echo "$BASELINE_FIXTURES" | jq -c --arg b "$BASELINE_ID" '[.[].fixture_id | "run-" + $b + "-" + .] | sort')"
  registered="$(api_get "$api" "/robot-runs?robot_id=$ROBOT_ID&limit=500" | jq -c '[.robotRuns[].runId] | sort')"
  if baseline_exact_membership; then
    [ "$registered" = "$expected" ] \
      || fail "the RobotRuns of $ROBOT_ID are not the baseline's fixtures: missing $(jq -cn --argjson e "$expected" --argjson r "$registered" '$e - $r'), unexpected $(jq -cn --argjson e "$expected" --argjson r "$registered" '$r - $e')"
  else
    [ "$(jq -cn --argjson e "$expected" --argjson r "$registered" '$e - $r')" = "[]" ] \
      || fail "the selected RobotRuns are not all registered under $ROBOT_ID: missing $(jq -cn --argjson e "$expected" --argjson r "$registered" '$e - $r')"
  fi

  all_scenes="$(api_get "$api" "/datasets/$DATASET_ID/versions/$DATASET_VERSION/scenes?limit=500")" \
    || fail "DatasetVersion $DATASET_ID/$DATASET_VERSION is not composed"
  all_episodes="$(api_get "$api" "/datasets/$DATASET_ID/versions/$DATASET_VERSION/episodes?limit=500")"

  fixtures=()
  while IFS= read -r fixture; do fixtures+=("$fixture"); done < <(echo "$BASELINE_FIXTURES" | jq -c '.[]')
  for fixture in "${fixtures[@]}"; do
    id="$(echo "$fixture" | jq -r '.fixture_id')"
    run_id="$(baseline_run_id "$id")"
    sha="$(echo "$fixture" | jq -r '.recording.sha256')"
    size="$(echo "$fixture" | jq -r '.recording.size_bytes')"
    "${BASELINE_RECORDING_ASSERT:-baseline_assert_recording}" "$run_id" "$sha" "$size"
    run_ids="$(echo "$run_ids" | jq -c --arg r "$run_id" '. + [$r]')"

    fixture_scenes="$(echo "$all_scenes" | jq -c --arg r "$run_id" '[.scenes[] | select(.robotRunId == $r)]')"
    fixture_episodes="$(echo "$all_episodes" | jq -c --arg r "$run_id" '[.episodes[] | select(.robotRunId == $r)]')"
    # The expected shape is the reference baseline's own contract
    # (config/baselines/baseline_shape.json, set by its build configurations);
    # membership itself is read from the API, not derived from the shape.
    [ "$(echo "$fixture_scenes" | jq 'length')" = "$scenes_expected" ] \
      || fail "fixture $id ($run_id) has $(echo "$fixture_scenes" | jq 'length') Scene(s), the reference baseline expects $scenes_expected per RobotRun (a baseline built with another Scene build configuration: make local-reset, or use another BASELINE_ID)"
    [ "$(echo "$fixture_episodes" | jq 'length')" = "$episodes_expected" ] \
      || fail "fixture $id ($run_id) has $(echo "$fixture_episodes" | jq 'length') Episode(s), the reference baseline expects $episodes_expected per RobotRun"

    # Every member pins exactly the checksum of its manifest ArtifactRecord.
    manifest_pins="$({
      echo "$fixture_scenes" | jq -r '.[] | "\(.manifestArtifactId) \(.manifestChecksum)"'
      echo "$fixture_episodes" | jq -r '.[] | "\(.manifestArtifactId) \(.manifestChecksum)"'
    })"
    while read -r mid mchecksum; do
      artifact="$(api_get "$api" "/artifacts/$mid" | jq -c '.artifact')"
      [ "$(echo "$artifact" | jq -r '.checksum')" = "$mchecksum" ] \
        || fail "manifest artifact $mid of $id does not carry the checksum its record pins"
    done <<<"$manifest_pins"

    # Validated and profiled at the revision the DatasetVersion pins, and ready.
    scenes_ready=0
    for scene_id in $(echo "$fixture_scenes" | jq -r '.[].sceneId'); do
      quality="$(api_get "$api" "/scenes/$scene_id/quality?dataset_id=$DATASET_ID&dataset_version=$DATASET_VERSION")"
      echo "$quality" | jq -e '.validation != null and .profile != null and .readiness == "ready"' >/dev/null \
        || fail "Scene $scene_id ($id) is not validated, profiled and ready at the revision $DATASET_ID/$DATASET_VERSION pins"
      scenes_ready=$((scenes_ready + 1))
    done
    episodes_ready=0
    for episode_id in $(echo "$fixture_episodes" | jq -r '.[].episodeId'); do
      quality="$(api_get "$api" "/episodes/$episode_id/quality?dataset_id=$DATASET_ID&dataset_version=$DATASET_VERSION")"
      echo "$quality" | jq -e '.validation != null and .profile != null and .readiness == "ready"' >/dev/null \
        || fail "Episode $episode_id ($id) is not validated, profiled and ready at the revision $DATASET_ID/$DATASET_VERSION pins"
      episodes_ready=$((episodes_ready + 1))
    done

    entries="$(echo "$entries" | jq -c --argjson f "$fixture" --arg run "$run_id" \
      --argjson scenes "$fixture_scenes" --argjson episodes "$fixture_episodes" \
      --argjson sr "$scenes_ready" --argjson er "$episodes_ready" '. + [{
        fixture_id: $f.fixture_id, source_unit: $f.source_unit, robot_run_id: $run,
        recording_sha256: $f.recording.sha256, recording_bytes: $f.recording.size_bytes,
        message_count: $f.recording.message_count,
        scene_count: ($scenes | length), scene_ids: ([$scenes[].sceneId] | sort),
        episode_count: ($episodes | length), episode_ids: ([$episodes[].episodeId] | sort),
        scenes_ready: $sr, episodes_ready: $er}]')"
  done

  # Every Scene and Episode of the DatasetVersion belongs to the baseline: to a
  # selected run, or (a selection that is a part of the contract's baseline) to a
  # RobotRun registered under the baseline's robot.
  if baseline_exact_membership; then
    [ "$(echo "$all_scenes" | jq '.scenes | length')" = "$(echo "$entries" | jq '[.[].scene_count] | add')" ] \
      || fail "a Scene of $DATASET_ID/$DATASET_VERSION points at a RobotRun outside the baseline"
    [ "$(echo "$all_episodes" | jq '.episodes | length')" = "$(echo "$entries" | jq '[.[].episode_count] | add')" ] \
      || fail "an Episode of $DATASET_ID/$DATASET_VERSION points at a RobotRun outside the baseline"
  else
    [ "$(jq -cn --argjson s "$all_scenes" --argjson e "$all_episodes" --argjson r "$registered" \
        '[$s.scenes[].robotRunId, $e.episodes[].robotRunId] | unique - $r')" = "[]" ] \
      || fail "a Scene or Episode of $DATASET_ID/$DATASET_VERSION points at a RobotRun outside the baseline"
  fi

  version="$(api_get "$api" "/datasets/$DATASET_ID/versions/$DATASET_VERSION" | jq -c '.version')"
  [ "$(echo "$version" | jq -r '.scene.sceneCount')" = "$(echo "$all_scenes" | jq '.scenes | length')" ] \
    || fail "DatasetVersion scene summary differs from its Scene members"
  [ "$(echo "$version" | jq -r '.episode.episodeCount')" = "$(echo "$all_episodes" | jq '.episodes | length')" ] \
    || fail "DatasetVersion episode summary differs from its Episode members"

  jq -cn --arg id "$BASELINE_ID" --arg robot "$ROBOT_ID" --arg ds "$DATASET_ID" \
    --arg dv "$DATASET_VERSION" --arg corpus "$REFERENCE_CORPUS" \
    --arg scope "$REFERENCE_SCOPE" --arg fixture "$FIXTURE" \
    --arg composition "$(echo "$version" | jq -r '.compositionChecksum')" \
    --argjson runs "$run_ids" --argjson entries "$entries" '{
      baseline_id: $id, robot_id: $robot, dataset_id: $ds, dataset_version: $dv,
      composition_checksum: $composition,
      reference: {corpus: $corpus, scope: (if $fixture == "" then $scope else null end),
                  fixture: (if $fixture == "" then null else $fixture end)},
      robot_run_ids: $runs,
      scene_count: ([$entries[].scene_count] | add),
      scene_ids: ([$entries[].scene_ids[]] | sort),
      episode_count: ([$entries[].episode_count] | add),
      episode_ids: ([$entries[].episode_ids[]] | sort),
      totals: {
        fixtures: ($entries | length), robot_runs: ($runs | length),
        scenes: ([$entries[].scene_count] | add), episodes: ([$entries[].episode_count] | add),
        recording_bytes: ([$entries[].recording_bytes] | add),
        messages: ([$entries[].message_count] | add)},
      fixtures: $entries}'
}
