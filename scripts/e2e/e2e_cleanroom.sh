#!/usr/bin/env bash
# e2e_cleanroom.sh — the one acceptance that proves SceneOps can reconstruct its golden
# runtime from the preserved reference inputs.
#
#   preserved                     data/raw, data/reference, config/reference
#   make local-reset              PostgreSQL, Redis, MinIO, Kafka log, acquisition-recordings
#                                 and generated ./data artifacts   [DESTRUCTIVE]
#   prove empty                   every generated store, read back
#   reference-data-verify         the locked corpus (every fixture) is still valid; nothing is
#                                 regenerated
#   reference-contract-bootstrap  Recording Import + Streaming Acquisition into the golden
#                                 contract from nothing
#   reference-contract-verify     REQUIRE_PRISTINE=1: exactly the contract, nothing else
#   reference-contract-bootstrap  again: converges on what exists (no import, no replay, no
#                                 Kafka start, no build, no record or object changed)
#   e2e-scene-ml                  REFERENCE_DERIVED journey on one contract fixture
#   e2e-episode-learning          REFERENCE_DERIVED journey on the same fixture
#                                 (each journey a second time: derived state converges)
#   reference-contract-verify     the contract is still valid beside the derived state
#
# The cleanroom owns reconstruction reproducibility only. Transport equivalence, recovery,
# infrastructure, the orchestrators, model backends and benchmarks have acceptance surfaces
# of their own and are not run here. It keeps no verifier of its own for the golden state:
# reference-data-verify, reference-contract-verify and the two journeys are the checks.
#
# Platform operations go through FastAPI. What the API cannot show is read from the
# containers that hold it (MinIO object count and size, Redis keys, Docker volumes and
# events); nothing is written there.
#
# Test-state class: CLEANROOM_ACCEPTANCE (docs/development/test-matrix.md). It ends with the
# golden contract plus the fixed derived Datasets of the two journeys; nothing it creates
# depends on time, a counter or a random value.
#
# Requires interactive confirmation (the reset is destructive) unless FORCE=1.
#
# Usage:
#   make e2e-cleanroom
#   FORCE=1 make e2e-cleanroom

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"
source "$SCRIPT_DIR/lib.sh"

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
export API_BASE_URL API_PREFIX ENV_FILE

# The identity is the contract's and the derived Datasets are the journeys' fixed ones.
for variable in BASELINE_ID DATASET_ID; do
  [ -z "${!variable:-}" ] || fail "$variable is not accepted: e2e-cleanroom reconstructs the contract's identity and the journeys' fixed Datasets"
done

REFERENCE_CORPUS="${REFERENCE_CORPUS:-nuscenes-mini-v1}"
CONTRACT_FILE="$REPO_ROOT/config/reference/$REFERENCE_CORPUS/reference_contract.json"
[ -r "$CONTRACT_FILE" ] || fail "no reference contract at $CONTRACT_FILE"
CONTRACT_SCOPE="$(jq -r '.corpus.scope' "$CONTRACT_FILE")"
EXPECT_FIXTURES="$(jq '.expected_totals.fixtures' "$CONTRACT_FILE")"
EXPECT_RUNS="$(jq '.expected_totals.robot_runs' "$CONTRACT_FILE")"
EXPECT_SCENES="$(jq '.expected_totals.scenes' "$CONTRACT_FILE")"
EXPECT_EPISODES="$(jq '.expected_totals.episodes' "$CONTRACT_FILE")"
EXPECT_UNITS=$((EXPECT_SCENES + EXPECT_EPISODES))

# The one fixture the journeys run on, and the fixed Datasets they write into
# (scripts/e2e/tests/test_derived_identities.py keeps these equal to the journeys' defaults).
UNIT="${SOURCE_UNIT:-scene-0061}"
SCENE_ML_DATASET="sceneops-test-scene-ml"
EPISODE_LEARNING_DATASET="sceneops-test-episode-learning"
DERIVED_DATASETS="$(jq -cn --arg a "$SCENE_ML_DATASET" --arg b "$EPISODE_LEARNING_DATASET" '[$a, $b] | sort')"

MAKE_Q=(make -s --no-print-directory -C "$REPO_ROOT" ENV_FILE="$ENV_FILE" API_BASE_URL="$API_BASE_URL" API_PREFIX="$API_PREFIX")
CONTRACT_PY=(python3 "$REPO_ROOT/scripts/reference/reference_contract.py")
DC=(docker compose --env-file "$ENV_FILE")

# Every collection the API lists. A generated runtime is empty exactly when all are.
API_COLLECTIONS="robots robot-runs datasets scenes episodes scenarios jobs pipelines/runs executions artifacts models inference/runs evaluations/runs"
# The services a convergent bootstrap must not start: replay, ROS 2 bridge / capture, Kafka.
STREAMING_SERVICES_RE='^(kafka|ros2|dataset-replay)$'

WORK="$(mktemp -d)"
EVENTS_PID=""
cleanup() {
  [ -z "$EVENTS_PID" ] || kill "$EVENTS_PID" 2>/dev/null || true
  rm -rf "$WORK"
}
trap cleanup EXIT

now() { perl -MTime::HiRes=time -e 'printf "%.3f\n", time'; }
seconds_since() { awk -v a="$1" -v b="$(now)" 'BEGIN { printf "%.1f", b - a }'; }
step() { printf '\n--- %s ---\n' "$*"; }

# ── Reads ────────────────────────────────────────────────────────────────────

# count_collection <path> — records in an API collection, paged.
count_collection() {
  local offset=0 total=0 n
  while :; do
    n="$(api_get "$API_BASE_URL" "$1?limit=500&offset=$offset" | jq '[.[] | arrays | length] | add // 0')"
    total=$((total + n))
    [ "$n" -lt 500 ] && break
    offset=$((offset + 500))
  done
  echo "$total"
}

minio_exec() { "${DC[@]}" exec -T minio sh -c "$1"; }

# Objects of every bucket (an object is a directory holding xl.meta) and the size of the
# whole MinIO volume in MiB.
minio_object_count() {
  minio_exec 'for d in /data/*; do [ -d "$d" ] && ls -AR "$d"; done; true' \
    | awk '$0 == "xl.meta" { n++ } END { print n + 0 }'
}
minio_mib() { minio_exec 'du -sk /data' | awk '{ printf "%d", $1 / 1024 }'; }

redis_cli() { "${DC[@]}" exec -T redis redis-cli "$@" | tr -d '\r'; }

# Redis keys other than the broker's queue bindings, which exist as soon as a worker connects.
redis_state_keys() {
  redis_cli --scan | awk '!/^_kombu\.binding\./ { n++ } END { print n + 0 }'
}

compose_project() {
  "${DC[@]}" --profile streaming --profile acquisition config --format json | jq -r '.name'
}

# service_container <service> — the id of a compose service's container (any state), if any.
service_container() {
  docker ps -aq --filter "label=com.docker.compose.service=$1" \
    --filter "label=com.docker.compose.project=$(compose_project)" | head -1
}

volume_absent() { ! docker volume inspect "$(compose_project)_$1" >/dev/null 2>&1; }
no_streaming_containers() {
  [ -z "$(service_container kafka)" ] && [ -z "$(docker ps -aq --filter 'name=^/(capture|bridge)-')" ]
}
directory_empty() { [ -z "$(find "$1" -mindepth 1 2>/dev/null | head -1)" ]; }

# kafka_started_at — the broker container's identity and start time, `none` if it does not exist.
kafka_started_at() {
  local id
  id="$(service_container kafka)"
  [ -n "$id" ] || { echo none; return 0; }
  docker inspect -f '{{.Id}} {{.State.StartedAt}}' "$id"
}

# state_counts — the size of every API collection and of MinIO, as one JSON object.
state_counts() {
  local c out='{}' n
  for c in $API_COLLECTIONS; do
    n="$(count_collection "/$c")"
    out="$(jq -c --arg k "$c" --argjson n "$n" '. + {($k): $n}' <<<"$out")"
  done
  jq -c --argjson m "$(minio_object_count)" '. + {minio_objects: $m}' <<<"$out"
}

golden_fingerprint() { "${CONTRACT_PY[@]}" fingerprint --golden; }
platform_fingerprint() { "${CONTRACT_PY[@]}" fingerprint; }

# preserved_digest — one hash over path, size and mtime of every preserved input file.
preserved_digest() {
  local stat_format=(-f '%N %z %m') d
  stat -f '%N' "$REPO_ROOT" >/dev/null 2>&1 || stat_format=(-c '%n %s %Y')
  for d in data/raw data/reference config/reference; do
    [ -d "$REPO_ROOT/$d" ] || { echo "missing $d"; continue; }
    (cd "$REPO_ROOT" && find "$d" -type f -print0 | sort -z | xargs -0 stat "${stat_format[@]}")
  done | shasum -a 256 | awk '{ print $1 }'
}
preserved_files() {
  (cd "$REPO_ROOT" && find data/raw data/reference config/reference -type f | wc -l | tr -d ' ')
}

# storage_report — free space and the size of every store, as one JSON object.
storage_report() {
  local host vm kafka postgres docker_df host_data
  host="$(df -Pk "$REPO_ROOT" | awk 'NR==2 { printf "%.1f", $4 / 1048576 }')"
  vm="$("${DC[@]}" --profile ros2 run --rm -T --entrypoint df ros2 -Pk / </dev/null | awk 'NR==2 { printf "%.1f", $4 / 1048576 }')"
  kafka=null
  if [ -n "$(service_container kafka)" ]; then
    kafka="$("${DC[@]}" exec -T kafka du -sk /var/lib/kafka/data | awk '{ printf "%d", $1 / 1024 }')"
  fi
  postgres="$("${DC[@]}" exec -T postgres du -sk /var/lib/postgresql/data | awk '{ printf "%d", $1 / 1024 }')"
  docker_df="$(docker system df --format '{{json .}}' | jq -sc 'map({key: .Type, value: .Size}) | from_entries')"
  host_data="$(cd "$REPO_ROOT" && du -sk data/raw data/reference data/artifacts data/runs data/inputs 2>/dev/null \
    | jq -Rn '[inputs | split("\t") | {key: .[1], value: (.[0] | tonumber / 1024 | floor)}] | from_entries')"
  jq -cn --argjson host "$host" --argjson vm "$vm" --argjson minio "$(minio_mib)" --argjson kafka "$kafka" \
    --argjson postgres "$postgres" --argjson docker "$docker_df" --argjson data "$host_data" \
    '{host_free_gib: $host, docker_vm_free_gib: $vm, minio_mib: $minio, kafka_mib: $kafka,
      postgres_mib: $postgres, docker_objects: $docker, host_data_mib: $data}'
}

# ── Assertions ───────────────────────────────────────────────────────────────

# assert_runtime_empty — every generated store, read back from where it lives.
assert_runtime_empty() {
  local c d
  for c in $API_COLLECTIONS; do
    check "PostgreSQL: /$c holds no record" [ "$(count_collection "/$c")" = 0 ]
  done
  check "MinIO: no object in any bucket" [ "$(minio_object_count)" = 0 ]
  check "Redis: no queued task" \
    [ "$(redis_cli llen sceneops.jobs)$(redis_cli llen sceneops.pipeline_runs)" = 00 ]
  check "Redis: no key besides the broker bindings (no task result)" [ "$(redis_state_keys)" = 0 ]
  check "Kafka: no broker, capture or bridge container" no_streaming_containers
  check "Kafka: no log volume" volume_absent kafka-data
  check "acquisition-recordings: no capture volume" volume_absent acquisition-recordings
  for d in datasets runs models artifacts; do
    check "data/$d holds no generated file" directory_empty "$REPO_ROOT/data/$d"
  done
}

# jq_true <json> <jq-program> [jq args...] — the program evaluates to true.
jq_true() {
  local json="$1" program="$2"
  shift 2
  [ "$(jq -r "$@" "$program" <<<"$json")" = true ]
}

# run_contract <bootstrap|verify> [REQUIRE_PRISTINE=1] — sets REPORT to the JSON report.
run_contract() {
  local command="$1" status=0
  shift
  REPORT="$("${MAKE_Q[@]}" "reference-contract-$command" "$@")" || status=$?
  if [ "$status" -ne 0 ]; then
    echo "$REPORT" | jq -r '.violations[]? | "    [\(.code)] \(.message)"' >&2 2>/dev/null || true
    fail "reference-contract-$command $* failed (exit $status)"
  fi
}

# assert_contract_valid <report> <derived-datasets-json-array> <pristine true|false>
assert_contract_valid() {
  check "the contract is valid: $EXPECT_RUNS RobotRuns, $EXPECT_SCENES Scenes, $EXPECT_EPISODES Episodes" \
    jq_true "$1" '.ok == true and .totals.observed == .totals.expected and .state.contract_robot_runs == $r
      and .state.contract_scenes == $s and .state.contract_episodes == $e' \
    --argjson r "$EXPECT_RUNS" --argjson s "$EXPECT_SCENES" --argjson e "$EXPECT_EPISODES"
  check "no non-contract RobotRun and no foreign Dataset" \
    jq_true "$1" '.state.non_contract_robot_runs == 0 and .state.foreign_datasets == 0'
  check "the derived Datasets are exactly $2" \
    jq_true "$1" '.inventory.derived_test_datasets == $d and .state.pristine == $p' \
    --argjson d "$2" --argjson p "$3"
  check "no orphan Scene or Episode" \
    jq_true "$1" '(.inventory.orphan_scenes | length) == 0 and (.inventory.orphan_episodes | length) == 0'
}

echo "=================================================================="
echo " e2e-cleanroom: reconstruct the golden runtime from preserved inputs"
echo "=================================================================="
echo ""
echo "This will:"
echo "  1. build images             from the current tree"
echo "  2. make local-reset         [DESTRUCTIVE] generated runtime state; preserves data/raw,"
echo "                              data/reference, config/reference"
echo "  3. prove the runtime empty and the inputs untouched"
echo "  4. reference-data-verify    the locked corpus ($CONTRACT_SCOPE)"
echo "  5. reference-contract-bootstrap + verify (REQUIRE_PRISTINE=1): $EXPECT_RUNS RobotRuns"
echo "  6. reference-contract-bootstrap again: convergence, nothing re-executed"
echo "  7. e2e-scene-ml, e2e-episode-learning on $UNIT (each twice), then reference-contract-verify"
echo ""
T_TOTAL="$(now)"

step "1. images from the current tree"
T="$(now)"
make -C "$REPO_ROOT" compose-build acquisition-image lerobot-image
"${DC[@]}" --profile ros2 build ros2
S_IMAGES="$(seconds_since "$T")"

step "2. preserved inputs, before the reset"
DIGEST_BEFORE="$(preserved_digest)"
FILES_BEFORE="$(preserved_files)"
echo "  data/raw + data/reference + config/reference: $FILES_BEFORE files, digest ${DIGEST_BEFORE:0:16}"
STORAGE_BEFORE="$(storage_report)"

step "3. make local-reset (generated runtime state)"
T="$(now)"
FORCE="${FORCE:-0}" "${MAKE_Q[@]}" local-reset
S_RESET="$(seconds_since "$T")"
require_api "$API_BASE_URL"

step "4. the generated runtime is empty; the inputs are not"
assert_runtime_empty
check "the preserved inputs are byte-for-byte as before the reset ($FILES_BEFORE files)" \
  [ "$(preserved_digest)" = "$DIGEST_BEFORE" ]
STORAGE_EMPTY="$(storage_report)"

step "5. reference-data-verify ($CONTRACT_SCOPE): the locked corpus, nothing regenerated"
T="$(now)"
DATA_VERIFY="$("${MAKE_Q[@]}" reference-data-verify REFERENCE_SCOPE="$CONTRACT_SCOPE")" || fail "reference-data-verify failed"
S_DATA_VERIFY="$(seconds_since "$T")"
check "all $EXPECT_FIXTURES fixtures of the corpus agree with the lock" \
  jq_true "$DATA_VERIFY" '.fixtures == $n and .scope == $s' --argjson n "$EXPECT_FIXTURES" --arg s "$CONTRACT_SCOPE"

step "6. reference-contract-bootstrap REQUIRE_PRISTINE=1: reconstruction from nothing"
T="$(now)"
run_contract bootstrap REQUIRE_PRISTINE=1
S_RECONSTRUCT="$(seconds_since "$T")"
check "every contract record was created: $EXPECT_RUNS RobotRuns, $EXPECT_UNITS Scenes and Episodes; none reused" \
  jq_true "$REPORT" '.bootstrap.before.missing == $r and .bootstrap.changes.robot_runs.created == $r
    and .bootstrap.changes.robot_runs.reused == 0 and .bootstrap.changes.units.created == $u
    and .bootstrap.changes.units.reused == 0' \
  --argjson r "$EXPECT_RUNS" --argjson u "$EXPECT_UNITS"
STORAGE_RECONSTRUCTED="$(storage_report)"

step "7. reference-contract-verify REQUIRE_PRISTINE=1"
T="$(now)"
run_contract verify REQUIRE_PRISTINE=1
S_PRISTINE_VERIFY="$(seconds_since "$T")"
assert_contract_valid "$REPORT" '[]' true

step "8. second reference-contract-bootstrap: convergence"
GOLDEN_0="$(golden_fingerprint)"
PLATFORM_0="$(platform_fingerprint)"
COUNTS_0="$(state_counts)"
KAFKA_0="$(kafka_started_at)"
echo "  before: $COUNTS_0"
echo "  kafka:  $KAFKA_0"
# The daemon keeps only a short event history, so the events are recorded live.
docker events --since "$(now)" --filter type=container --format '{{json .}}' >"$WORK/events.jsonl" 2>/dev/null &
EVENTS_PID=$!
sleep 1
T="$(now)"
run_contract bootstrap REQUIRE_PRISTINE=1
S_CONVERGE="$(seconds_since "$T")"
sleep 1
kill "$EVENTS_PID" 2>/dev/null || true
wait "$EVENTS_PID" 2>/dev/null || true
EVENTS_PID=""
COUNTS_1="$(state_counts)"
# Container lifecycle only: health checks of the running services show up as exec events.
SERVICES_STARTED="$(jq -rs '[.[] | select(.Action | test("^(create|start|restart)$"))
  | .Actor.Attributes["com.docker.compose.service"] // .Actor.Attributes.name] | unique | join(" ")' "$WORK/events.jsonl")"
echo "  after:  $COUNTS_1"
echo "  containers created or started during the bootstrap: ${SERVICES_STARTED:-none}"
check "the bootstrap found all $EXPECT_RUNS RobotRuns complete and created, changed or removed nothing" \
  jq_true "$REPORT" '.bootstrap.before.registered == $r and .bootstrap.before.missing == 0
    and .bootstrap.before.registered_incomplete == 0
    and .bootstrap.changes.robot_runs.created == 0 and .bootstrap.changes.units.created == 0
    and (.bootstrap.changes.robot_runs.changed | length) == 0 and (.bootstrap.changes.units.changed | length) == 0
    and (.bootstrap.changes.robot_runs.removed | length) == 0 and (.bootstrap.changes.units.removed | length) == 0' \
  --argjson r "$EXPECT_RUNS"
check "no Recording Import, Scene build or Episode build: no RobotRun, Job, PipelineRun or artifact was added" \
  [ "$COUNTS_0" = "$COUNTS_1" ]
check "no MinIO object was written ($(jq '.minio_objects' <<<"$COUNTS_1") objects before and after)" \
  jq_true "$COUNTS_1" '.minio_objects == $n' --argjson n "$(jq '.minio_objects' <<<"$COUNTS_0")"
check "no streaming replay and no Kafka startup: no kafka, ros2 or replay container was created or started" \
  [ -z "$(tr ' ' '\n' <<<"$SERVICES_STARTED" | grep -E "$STREAMING_SERVICES_RE" || true)" ]
check "the Kafka broker was not restarted" [ "$(kafka_started_at)" = "$KAFKA_0" ]
check "no record of the platform changed" [ "$(platform_fingerprint)" = "$PLATFORM_0" ]
check "the golden contract is unchanged" [ "$(golden_fingerprint)" = "$GOLDEN_0" ]
assert_contract_valid "$REPORT" '[]' true

step "9. e2e-scene-ml on $UNIT (REFERENCE_DERIVED)"
T="$(now)"
make -C "$REPO_ROOT" e2e-scene-ml SCENE="$UNIT"
S_SCENE_ML="$(seconds_since "$T")"

step "10. e2e-episode-learning on $UNIT (REFERENCE_DERIVED)"
T="$(now)"
make -C "$REPO_ROOT" e2e-episode-learning SCENE="$UNIT"
S_EPISODE_LEARNING="$(seconds_since "$T")"

step "11. the golden contract after the journeys"
PLATFORM_1="$(platform_fingerprint)"
check "no new RobotRun: the platform holds exactly the contract's $EXPECT_RUNS, unchanged" \
  jq_true "$PLATFORM_1" '(.robot_runs | length) == $r and .robot_runs == $before.robot_runs' \
  --argjson r "$EXPECT_RUNS" --argjson before "$PLATFORM_0"
check "golden RobotRun, Scene and Episode fingerprints are unchanged" [ "$(golden_fingerprint)" = "$GOLDEN_0" ]
check "the Datasets are the contract's and the journeys' fixed ones, no others" \
  jq_true "$PLATFORM_1" '(.datasets | sort) == (($before.datasets + $d) | sort)' \
  --argjson before "$PLATFORM_0" --argjson d "$DERIVED_DATASETS"
COUNTS_2="$(state_counts)"
echo "  state: $COUNTS_2"

step "12. both journeys again: derived state converges"
T="$(now)"
make -C "$REPO_ROOT" e2e-scene-ml SCENE="$UNIT"
S_SCENE_ML_AGAIN="$(seconds_since "$T")"
T="$(now)"
make -C "$REPO_ROOT" e2e-episode-learning SCENE="$UNIT"
S_EPISODE_LEARNING_AGAIN="$(seconds_since "$T")"
COUNTS_3="$(state_counts)"
echo "  state: $COUNTS_3"
check "no Dataset, Scene, Episode or RobotRun was added or rewritten" [ "$(platform_fingerprint)" = "$PLATFORM_1" ]
check "no artifact and no MinIO object was added" \
  jq_true "$COUNTS_3" '.artifacts == $b.artifacts and .minio_objects == $b.minio_objects' --argjson b "$COUNTS_2"
check "the golden contract is still unchanged" [ "$(golden_fingerprint)" = "$GOLDEN_0" ]
# Execution history is append-only by platform design (docs/development/test-matrix.md):
# reported, not asserted.
HISTORY_ADDED="$(jq -c --argjson b "$COUNTS_2" '{jobs: (.jobs - $b.jobs),
  pipeline_runs: (.["pipelines/runs"] - $b["pipelines/runs"]), executions: (.executions - $b.executions),
  scenarios: (.scenarios - $b.scenarios), models: (.models - $b.models),
  inference_runs: (.["inference/runs"] - $b["inference/runs"]),
  evaluation_runs: (.["evaluations/runs"] - $b["evaluations/runs"])}' <<<"$COUNTS_3")"
echo "  execution history appended by the second run: $HISTORY_ADDED"

step "13. reference-contract-verify beside the derived state"
T="$(now)"
run_contract verify
S_FINAL_VERIFY="$(seconds_since "$T")"
FINAL_REPORT="$REPORT"
assert_contract_valid "$FINAL_REPORT" "$DERIVED_DATASETS" false
check "the preserved inputs are still byte-for-byte as before the reset" [ "$(preserved_digest)" = "$DIGEST_BEFORE" ]
STORAGE_FINAL="$(storage_report)"
S_TOTAL="$(seconds_since "$T_TOTAL")"

echo ""
echo "=================================================================="
echo " PASSED: e2e-cleanroom"
echo "=================================================================="
echo "An empty generated runtime was reconstructed from the preserved reference inputs into"
echo "the golden contract, and both primary journeys consumed it without changing it."
echo ""
echo "--- final state ---"
jq -n --argjson report "$FINAL_REPORT" '{
  golden_state: {
    robot_runs: $report.state.contract_robot_runs,
    scenes: $report.state.contract_scenes,
    episodes: $report.state.contract_episodes,
    non_contract_robot_runs: $report.state.non_contract_robot_runs,
    foreign_datasets: $report.state.foreign_datasets},
  derived_test_state: {
    datasets: $report.inventory.derived_test_datasets,
    scenes: $report.inventory.derived_test_scenes,
    episodes: $report.inventory.derived_test_episodes}}'
echo ""
echo "--- timing (seconds) and storage: diagnostic, not a benchmark ---"
jq -n \
  --argjson images "$S_IMAGES" --argjson reset "$S_RESET" --argjson data_verify "$S_DATA_VERIFY" \
  --argjson reconstruct "$S_RECONSTRUCT" --argjson pristine_verify "$S_PRISTINE_VERIFY" \
  --argjson converge "$S_CONVERGE" --argjson scene_ml "$S_SCENE_ML" --argjson episode_learning "$S_EPISODE_LEARNING" \
  --argjson scene_ml_again "$S_SCENE_ML_AGAIN" --argjson episode_learning_again "$S_EPISODE_LEARNING_AGAIN" \
  --argjson final_verify "$S_FINAL_VERIFY" --argjson total "$S_TOTAL" \
  --argjson before "$STORAGE_BEFORE" --argjson empty "$STORAGE_EMPTY" \
  --argjson reconstructed "$STORAGE_RECONSTRUCTED" --argjson final "$STORAGE_FINAL" \
  --argjson history "$HISTORY_ADDED" '{
  timing_s: {images: $images, reset: $reset, reference_data_verify: $data_verify,
    reference_contract_reconstruction: $reconstruct, pristine_verify: $pristine_verify,
    second_bootstrap_convergence: $converge, scene_ml: $scene_ml, episode_learning: $episode_learning,
    scene_ml_second_run: $scene_ml_again, episode_learning_second_run: $episode_learning_again,
    final_verify: $final_verify, total_wall: $total},
  storage: {before_reset: $before, after_reset: $empty, after_reconstruction: $reconstructed, final: $final},
  second_journey_run_execution_history_added: $history}'
